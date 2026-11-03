"""Archive redo keeps its bounds, source identity, and transcription dependency."""

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_extended import audio_data
from test_recaps import FakeInference

from omi_local.api import create_app
from omi_local.domain import EvidenceChanged
from omi_local.inference import Inference
from omi_local.recording_topics import ensure_topics, topic_fingerprint
from omi_local.reprocessing import RedoRequest, Reprocessing
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.source_matching import recognition_contract
from omi_local.store import Store
from omi_local.topic_context import topic_contexts
from omi_local.worker import process_one


@pytest.fixture
def jobs(tmp_path):
    settings = Settings(data_dir=tmp_path, timezone="Europe/Copenhagen", worker_enabled=True,
                        _env_file=None)
    return Reprocessing(Memory(Store(tmp_path), settings, SimpleNamespace()))


def recording(jobs, at="2026-10-15T12:00:00+00:00", text="Existing transcript"):
    key = str(uuid4())
    jobs.store.ingest(key, "phone", datetime.fromisoformat(at), audio_data(), 1,
                      source_kind="manual_upload")
    jobs.store.complete(key, text)
    return key


def test_availability_uses_local_dates_and_retained_audio(jobs):
    retained = recording(jobs, "2026-10-14T22:30:00+00:00")
    expired = recording(jobs, "2026-10-18T12:00:00+00:00")
    jobs.store.audio_storage.delete(expired)
    available = jobs.observation()
    assert available["transcripts"] == {"start": "2026-10-15", "end": "2026-10-15", "count": 1}
    assert available["summaries"] == {"start": "2026-10-15", "end": "2026-10-18", "count": 2}
    assert jobs.store.get_audio(retained)["state"] == "done"


def test_ranges_are_paired_ordered_bounded_and_nonempty(jobs):
    recording(jobs)
    recording(jobs, "2026-10-18T12:00:00+00:00")
    for values in ({"start": "2026-10-15"},
                   {"start": "2026-10-18", "end": "2026-10-15"}):
        with pytest.raises(ValueError):
            RedoRequest(kind="summaries", **values)
    for start, end in (("2026-10-14", "2026-10-15"),
                       ("2026-10-15", "2026-10-19"),
                       ("2026-10-16", "2026-10-17")):
        with pytest.raises(HTTPException) as error:
            jobs.enqueue(RedoRequest(kind="summaries", start=start, end=end))
        assert error.value.status_code == 422


def test_redo_is_durable_and_waits_for_all_transcripts_before_summaries(jobs):
    selected = recording(jobs)
    untouched = recording(jobs, "2026-10-18T12:00:00+00:00")
    original = jobs.store.get_audio(selected)
    job = jobs.enqueue(RedoRequest(kind="transcripts", start=date(2026, 10, 15),
                                   end=date(2026, 10, 15)))
    assert job["transcript_total"] == 1
    assert jobs.store.get_audio(selected)["state"] == "queued"
    assert jobs.store.get_audio(untouched)["state"] == "done"
    jobs = Reprocessing(jobs.memory)
    assert jobs.observation()["job"]["id"] == job["id"]
    calls = []

    async def recap(day, **kwargs):
        calls.append((day, kwargs))
        return {"summary": "Updated"}

    async def transcribe(data):
        return "New recognition"

    jobs.memory.recap = recap
    jobs.memory.inference.transcribe = transcribe
    assert not asyncio.run(jobs.process_one())
    assert calls == []
    asyncio.run(process_one(jobs.memory))
    assert asyncio.run(jobs.process_one())
    assert calls == [(date(2026, 10, 15), {"force": True})]
    result = jobs.observation()["job"]
    assert result["state"] == "completed"
    assert result["transcript_completed"] == result["summary_completed"] == 1
    updated = jobs.store.get_audio(selected)
    assert updated["text"] == "New recognition"
    assert updated["id"] == original["id"]
    assert updated["started_at"] == original["started_at"]
    assert updated["source_version"] != original["source_version"]


def test_summary_only_redo_never_queues_audio_and_rejects_overlap(jobs):
    key = recording(jobs)
    job = jobs.enqueue(RedoRequest(kind="summaries"))
    assert job["transcript_total"] == 0
    assert jobs.store.get_audio(key)["state"] == "done"
    with pytest.raises(HTTPException) as error:
        jobs.enqueue(RedoRequest(kind="summaries"))
    assert error.value.status_code == 409


def test_disabled_models_do_not_mutate_archive(jobs):
    key = recording(jobs)
    jobs.memory.settings = jobs.memory.settings.model_copy(update={"llm_enabled": False})
    with pytest.raises(HTTPException) as error:
        jobs.enqueue(RedoRequest(kind="transcripts"))
    assert error.value.status_code == 409
    assert jobs.store.get_audio(key)["state"] == "done"


def test_disabled_workers_reject_work_that_cannot_run(jobs):
    recording(jobs)
    for changes, kind in (({"worker_enabled": False}, "transcripts"),
                          ({"background_processing_enabled": False}, "summaries")):
        jobs.memory.settings = jobs.memory.settings.model_copy(update=changes)
        with pytest.raises(HTTPException) as error:
            jobs.enqueue(RedoRequest(kind=kind))
        assert error.value.status_code == 409
    assert jobs.observation()["job"] is None


def test_force_redo_bypasses_notes_and_daily_cache_preserving_prior_versions(jobs):
    recording(jobs)
    jobs.memory.inference = FakeInference()
    day = date(2026, 10, 15)
    first = asyncio.run(jobs.memory.recap(day))
    call_count = len(jobs.memory.inference.calls)
    assert asyncio.run(jobs.memory.recap(day))["output_id"] == first["output_id"]
    assert len(jobs.memory.inference.calls) == call_count
    second = asyncio.run(jobs.memory.recap(day, force=True))
    assert second["output_id"] != first["output_id"]
    assert len(jobs.memory.inference.calls) > call_count
    assert len(jobs.store.diary_versions(day.isoformat())) == 2


def test_pending_conversations_wait_before_any_redo_inference(jobs):
    key = recording(jobs)
    with jobs.store.connect() as db:
        db.execute("UPDATE audio SET source_kind='' WHERE id=?", (key,))
    jobs.enqueue(RedoRequest(kind="summaries"))
    jobs.memory.inference = FakeInference()
    assert not asyncio.run(jobs.process_one())
    assert jobs.memory.inference.calls == []
    assert jobs.observation()["job"]["state"] == "summarizing"
    with pytest.raises(EvidenceChanged):
        asyncio.run(jobs.memory.recap(date(2026, 10, 15), force=True))


def test_deleted_recording_fails_redo_instead_of_false_completion(jobs):
    key = recording(jobs)
    jobs.enqueue(RedoRequest(kind="transcripts"))
    with jobs.store.connect() as db:
        db.execute("DELETE FROM audio WHERE id=?", (key,))
    assert asyncio.run(jobs.process_one())
    assert jobs.observation()["job"]["state"] == "failed"


def test_cross_midnight_redo_refreshes_every_affected_day(jobs):
    key = recording(jobs, "2026-10-15T21:59:59+00:00")
    with jobs.store.connect() as db:
        db.execute("UPDATE audio SET ended_at='2026-10-15T22:00:01+00:00' WHERE id=?", (key,))
    result = jobs.enqueue(RedoRequest(kind="transcripts", start="2026-10-15", end="2026-10-15"))
    assert result["summary_total"] == 2
    assert result["transcript_total"] == 1


def test_reprocessing_api_is_profile_scoped_and_rejects_invalid_ranges(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.profiles.runtimes["default"]
        recording(runtime.reprocessing)
        response = client.get("/v1/reprocessing")
        assert response.status_code == 200
        assert response.json()["transcripts"]["count"] == 1
        assert client.post("/v1/reprocessing", json={
            "kind": "summaries", "start": "2026-10-15",
        }).status_code == 422
        response = client.post("/v1/reprocessing", json={"kind": "summaries"})
        assert response.status_code == 202
        assert response.json()["summary_total"] == 1
        profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
        other = client.get("/v1/reprocessing", headers={"X-Omi-Profile": profile}).json()
        assert other["job"] is None
        assert other["summaries"]["count"] == other["transcripts"]["count"] == 0


def test_redo_transcription_bypasses_matching_recognition_cache(jobs):
    first = recording(jobs)
    duplicate = recording(jobs, "2026-10-18T12:00:00+00:00")
    jobs.store.complete(duplicate, "Cached old recognition", details={
        "recognition_contract": recognition_contract(jobs.memory.settings),
    })
    jobs.enqueue(RedoRequest(kind="transcripts", start="2026-10-15", end="2026-10-15"))
    calls = []

    async def transcribe(data):
        calls.append(data)
        return "Fresh recognition"

    jobs.memory.inference.transcribe = transcribe
    asyncio.run(process_one(jobs.memory))
    assert len(calls) == 1
    assert jobs.store.get_audio(first)["text"] == "Fresh recognition"
    assert jobs.store.get_audio(duplicate)["text"] == "Cached old recognition"


def test_redo_regenerates_previously_saved_day_and_week_overviews(jobs, monkeypatch):
    key = recording(jobs)
    with jobs.store.connect() as db:
        db.execute("INSERT INTO day_overviews VALUES('2026-10-15','old','{}','[]')")
        db.execute("INSERT INTO week_overviews VALUES('2026-10-12','old','{}','[]')")
    jobs.enqueue(RedoRequest(kind="transcripts"))
    # Queueing changes source state and invalidates views; the durable request remembers them.
    jobs.store.complete(key, "Updated recognition")
    calls = []

    async def recap(day, *, force=False):
        calls.append(("recap", day, force))
        return {}

    async def day_overview(self, day, *, force=False):
        calls.append(("day", day, force))
        return {}

    async def week_overview(self, day, *, force=False):
        calls.append(("week", day, force))
        return {}

    jobs.memory.recap = recap
    monkeypatch.setattr("omi_local.day_overview.DayOverview.generate", day_overview)
    monkeypatch.setattr("omi_local.weekly_overview.WeekOverview.generate", week_overview)
    assert asyncio.run(jobs.process_one())
    assert calls == [("recap", date(2026, 10, 15), True),
                     ("day", date(2026, 10, 15), True),
                     ("week", date(2026, 10, 12), True)]
    assert jobs.observation()["job"]["state"] == "completed"


def test_force_topics_reanalyzes_even_with_current_topic_cache(jobs, monkeypatch):
    key = recording(jobs)
    with jobs.store.connect() as db:
        context = topic_contexts(db)[0]
        db.execute("UPDATE audio SET topic_details=? WHERE id=?", (json.dumps({
            "fingerprint": topic_fingerprint(jobs.memory), "context": context.key,
        }), key))
    calls = []

    async def organize(memory, context, now, *, force=False):
        calls.append((context.ids, force))

    jobs.memory.inference.generate_json = None  # The organizer spy owns inference in this test.
    monkeypatch.setattr("omi_local.recording_topics.organize_context", organize)
    asyncio.run(ensure_topics(jobs.memory, {key}))
    assert calls == []
    asyncio.run(ensure_topics(jobs.memory, {key}, force=True))
    assert calls == [([key], True)]


def test_failed_redo_preserves_previous_diary(jobs):
    recording(jobs)
    jobs.memory.inference = FakeInference()
    day = date(2026, 10, 15)
    old = asyncio.run(jobs.memory.recap(day))
    jobs.enqueue(RedoRequest(kind="summaries"))

    async def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    jobs.memory.inference.summarize = fail
    assert asyncio.run(jobs.process_one())
    assert jobs.observation()["job"]["state"] == "failed"
    assert jobs.memory.cached_recap(day)["output_id"] == old["output_id"]


def test_real_redo_fails_when_reasoning_exhausts_summary_output_budget(jobs):
    text = "We agreed to send the revised delivery plan on Friday."
    key = recording(jobs, text=text)
    jobs.memory.settings = jobs.memory.settings.model_copy(update={
        "llm_enable_thinking": True, "summary_language": "en",
    })
    notes_requests = []

    def provider(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        task = payload["task"]
        notes = task.startswith("Create notes from")
        thinking = body["chat_template_kwargs"]["enable_thinking"]
        if notes:
            notes_requests.append((body["max_tokens"], thinking))
            if thinking:
                return httpx.Response(200, json={"choices": [{
                    "finish_reason": "length", "message": {
                        "content": "", "reasoning_content": "Unfinished reasoning",
                    },
                }]})
        if "response_format" in body:
            result = json.dumps({"title": "Delivery plan", "continues_previous": False,
                                 "sections": [{"title": "Delivery plan", "start_quote": text}]})
        elif task.startswith(("Organize useful recorded information",
                              "Review the complete daily topic plan")):
            result = json.dumps({"topics": [{"title": "Delivery plan", "scope": "Friday delivery",
                                             "items": ["n1"]}]})
        else:
            result = f"The revised delivery plan is due Friday. [{key}]"
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": result},
        }]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            jobs.memory.inference = Inference(jobs.memory.settings, client)
            jobs.enqueue(RedoRequest(kind="transcripts"))
            jobs.store.complete(key, text)  # The STT worker has finished every selected transcript.
            assert await jobs.process_one()

    asyncio.run(run())
    status = jobs.observation()["job"]
    assert status["state"] == "failed"
    assert status["transcript_completed"] == 1
    assert status["summary_completed"] == 0
    assert notes_requests == [(65536, True)]
    assert jobs.memory.settings.llm_enable_thinking is True
    summary = (jobs.memory.cached_recap(date(2026, 10, 15)) or {}).get("summary") or ""
    assert "delivery plan" not in summary.lower()


def test_truncated_summary_reports_output_limit_and_keeps_transcript(jobs, caplog):
    from omi_local.inference_errors import InferenceTruncated

    key = recording(jobs)
    before = jobs.store.get_audio(key)
    job = jobs.enqueue(RedoRequest(kind="summaries"))

    async def fail(*args, **kwargs):
        raise InferenceTruncated("Private provider response must stay private")

    jobs.memory.recap = fail
    assert asyncio.run(jobs.process_one())
    failed = jobs.observation()["job"]
    assert failed["state"] == "failed"
    assert failed["error"] == "LLM output was truncated"
    assert failed["summary_completed"] == 0
    after = jobs.store.get_audio(key)
    assert (after["text"], after["state"], after["source_version"]) == (
        before["text"], before["state"], before["source_version"],
    )
    assert job["id"] in caplog.text
    assert "daily summary" in caplog.text
    assert "Private provider response" not in caplog.text


def test_summary_redo_proceeds_while_optional_topic_analysis_is_busy(jobs):
    key = recording(jobs, text="The release is planned for Friday, subject to testing.")
    before = jobs.store.get_audio(key)
    model = FakeInference()

    async def organize(*args, **kwargs):
        raise AssertionError("Summary redo must not require new navigation labels")

    model.generate_json = organize
    jobs.memory.inference = model
    jobs.enqueue(RedoRequest(kind="summaries"))

    async def run():
        # The independent organizer can hold this lock through multiple model retries.
        async with jobs.memory.topic_lock:
            async with asyncio.timeout(0.5):
                assert await jobs.process_one()

    asyncio.run(run())
    assert jobs.observation()["job"]["state"] == "completed"
    summary = jobs.memory.cached_recap(date(2026, 10, 15))["summary"]
    assert "subject to testing" in summary
    assert key in summary
    after = jobs.store.get_audio(key)
    assert (after["text"], after["source_version"]) == (
        before["text"], before["source_version"],
    )
