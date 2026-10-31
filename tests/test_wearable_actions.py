"""Pendant jobs preserve capture-time windows and send the actual generated result."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from test_generation_jobs import jobs as jobs

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.generation_jobs import GenerationJobs, GenerationRequest
from omi_local.inference import Inference
from omi_local.settings import Settings

END = datetime(2026, 10, 31, 12, tzinfo=UTC)


@pytest.mark.parametrize("action", ["summarize", "answer", "translate"])
def test_ios_sync_payload_is_accepted_and_retry_keeps_its_identity(tmp_path, action):
    async def synchronize():
        app = create_app(Settings(
            data_dir=tmp_path, worker_enabled=False, background_processing_enabled=False,
            translation_languages=["da"], _env_file=None,
        ))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                # Match DurableStore's JSON file sent by BackgroundUploader to /v1/jobs.
                body = {"id": str(uuid4()), "kind": "wearable_action", "parameters": {
                    "action": action, "duration_seconds": 30,
                    "end": stamp(END), "device_id": "omi-test",
                }}
                accepted = await client.post("/v1/jobs", json=body)
                assert accepted.status_code == 202, accepted.text
                saved = accepted.json()
                assert saved["id"] == body["id"]
                assert saved["kind"] == "wearable_action"
                assert saved["state"] == "queued"
                assert datetime.fromisoformat(saved["parameters"]["end"]) == END
                retry = await client.post("/v1/jobs", json=body)
                assert retry.status_code == 202, retry.text
                assert retry.json() == saved
                changed = body | {"parameters": body["parameters"] | {"duration_seconds": 60}}
                assert (await client.post("/v1/jobs", json=changed)).status_code == 409

    asyncio.run(synchronize())


class Model:
    def __init__(self):
        self.calls = []

    async def summarize(self, sources, purpose):
        self.calls.append(("summarize", sources, purpose))
        return "They discussed next week's delivery."

    async def answer_recent(self, sources):
        self.calls.append(("answer", sources))
        return "Madrid."

    async def translate(self, text, language):
        self.calls.append(("translate", text, language))
        return "Hvad er Spaniens hovedstad?"


def request(action="answer", **parameters):
    return GenerationRequest(id=uuid4(), kind="wearable_action", parameters={
        "action": action, "end": stamp(END), "device_id": "omi-test", **parameters,
    })


def source(jobs, text="What is the capital of Spain?", seconds=30, end=END,
           device="omi-test", segments=None, complete=True):
    key = str(uuid4())
    jobs.store.ingest(key, device, end - timedelta(seconds=seconds), b"audio", seconds)
    if complete:
        jobs.store.complete(key, text, segments=segments)
    return key


def run(jobs, body):
    jobs.enqueue(body)
    asyncio.run(jobs.process_one())
    return jobs.get(str(body.id))


@pytest.mark.parametrize("action", ["summarize", "answer", "translate"])
def test_distinct_actions_publish_the_result_and_remain_idempotent(jobs, action):
    model = Model()
    jobs.memory.inference = model
    jobs.memory.settings.translation_languages = ["da"]
    key = source(jobs)
    body = request(action)
    job = run(jobs, body)
    assert job["state"] == "completed"
    result = job["result"]
    assert result["duration_seconds"] == 30
    assert result["start"] == stamp(END - timedelta(seconds=30))
    assert result["end"] == stamp(END)
    assert result["sources"][0]["id"] == key
    assert model.calls[0][0] == action
    assert result["summary"] == (result["text"] if action == "summarize" else None)
    jobs.notify_completed()
    jobs.notify_completed()
    notices = jobs.store.notifications_after(0)["notifications"]
    assert len(notices) == 1
    assert notices[0]["body"] == result["text"]
    assert notices[0]["target"]["job_id"] == str(body.id)
    assert not asyncio.run(jobs.process_one())
    assert jobs.enqueue(body)["result"] == result


def test_window_is_fixed_to_click_and_excludes_other_devices_and_times(jobs):
    model = Model()
    jobs.memory.inference = model
    expected = source(jobs, "Selected question", seconds=60)
    source(jobs, "Too old", end=END - timedelta(seconds=60))
    source(jobs, "Too new", end=END + timedelta(seconds=30))
    source(jobs, "Other device", device="another-phone")
    body = request(duration_seconds=60)
    jobs.enqueue(body)
    restarted = GenerationJobs(jobs.memory)
    assert datetime.fromisoformat(restarted.enqueue(body)["parameters"]["end"]) == END
    asyncio.run(restarted.process_one())
    result = restarted.get(str(body.id))["result"]
    assert [s["id"] for s in result["sources"]] == [expected]
    assert model.calls[0][1][0]["text"] == "Selected question"


def test_job_waits_durably_for_transcription_without_blocking_other_jobs(jobs):
    jobs.memory.inference = Model()
    key = source(jobs, complete=False)
    body = request()
    job = run(jobs, body)
    assert job["state"] == "queued"
    assert job["retry_at"] and job["result"] is None and job["error"] is None
    assert jobs.memory.inference.calls == []
    second = run(jobs, request(device_id="other-device"))
    assert second["state"] == "completed"
    jobs.store.complete(key, "What is the capital of Spain?")
    with jobs.store.connect() as db:
        db.execute("UPDATE generation_jobs SET retry_at='' WHERE id=?", (str(body.id),))
    restarted = GenerationJobs(jobs.memory)
    asyncio.run(restarted.process_one())
    assert restarted.get(str(body.id))["result"]["text"] == "Madrid."


def test_only_segments_overlapping_requested_audio_reach_the_model(jobs):
    jobs.memory.inference = Model()
    source(jobs, "Old question. Recent question? Future question.", seconds=90,
           end=END + timedelta(seconds=30), segments=[
               {"start": 0, "end": 30, "text": "Old question."},
               {"start": 30, "end": 60, "text": "Recent question?"},
               {"start": 60, "end": 90, "text": "Future question."},
           ])
    result = run(jobs, request())["result"]
    assert jobs.memory.inference.calls[0][1][0]["text"] == "Recent question?"
    assert not result["approximate_window"]
    assert result["sources"][0]["window_timing"] == "segment"


def test_missing_segment_timing_is_disclosed_instead_of_invented(jobs):
    jobs.memory.inference = Model()
    source(jobs, seconds=60)
    result = run(jobs, request())["result"]
    assert result["approximate_window"]
    assert result["sources"][0]["window_timing"] == "overlapping_chunk"


def test_segment_crossing_a_window_boundary_is_also_disclosed(jobs):
    jobs.memory.inference = Model()
    source(jobs, "Question crossing the boundary?", seconds=60, segments=[
        {"start": 0, "end": 60, "text": "Question crossing the boundary?"},
    ])
    assert run(jobs, request())["result"]["approximate_window"]


def test_empty_or_silent_window_does_not_call_model(jobs):
    jobs.memory.inference = Model()
    source(jobs, "")
    result = run(jobs, request())["result"]
    assert result["text"] == "No transcribed speech is available in this window."
    assert jobs.memory.inference.calls == []


def test_translation_default_freezes_at_acceptance_and_retry_normalizes_language(jobs):
    jobs.memory.settings.translation_languages = ["da", "en"]
    body = request("translate")
    first = jobs.enqueue(body)
    assert first["parameters"]["language"] == "da"
    jobs.memory.settings.translation_languages = ["en", "da"]
    assert jobs.enqueue(body) == first
    explicit = request("translate", language="DA")
    assert jobs.enqueue(explicit)["parameters"]["language"] == "da"
    with pytest.raises(HTTPException) as error:
        jobs.enqueue(body.model_copy(update={"parameters": body.parameters | {"language": "en"}}))
    assert error.value.status_code == 409


def test_translation_with_no_enabled_language_is_rejected(jobs):
    jobs.memory.settings.translation_languages = []
    with pytest.raises(HTTPException) as error:
        jobs.enqueue(request("translate"))
    assert error.value.status_code == 422


@pytest.mark.parametrize("parameters", [
    {"duration_seconds": 0}, {"duration_seconds": 301}, {"duration_seconds": True},
    {"duration_seconds": 30.5}, {"end": "2026-10-31T12:00:00"},
    {"action": "capture_toggle"}, {"device_id": "../bad"}, {"language": "invalid_code"},
])
def test_invalid_job_parameters_are_rejected(jobs, parameters):
    with pytest.raises(HTTPException) as error:
        jobs.enqueue(request(**parameters))
    assert error.value.status_code == 422


def test_source_correction_invalidates_saved_wearable_answer_and_notification(jobs):
    jobs.memory.inference = Model()
    key = source(jobs)
    body = request()
    run(jobs, body)
    jobs.notify_completed()
    jobs.store.complete(key, "Corrected evidence")
    saved = jobs.get(str(body.id))
    assert saved["state"] == "failed" and saved["result"] is None
    assert not jobs.store.notifications_after(0)["notifications"]


def test_correction_during_inference_cannot_publish_a_stale_answer(jobs):
    key = source(jobs)

    async def answer(sources):
        jobs.store.complete(key, "Corrected evidence")
        return "Stale answer"

    jobs.memory.inference.answer_recent = answer
    body = request()
    result = run(jobs, body)
    assert result["state"] == "failed" and result["result"] is None
    jobs.notify_completed()
    assert "Stale answer" not in json.dumps(jobs.store.notifications_after(0))


def test_answer_prompt_requests_an_answer_and_explicitly_handles_missing_questions(jobs):
    requests = []

    def respond(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Madrid."}}]})

    async def answer():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = Inference(jobs.memory.settings, client)
            return await model.answer_recent([{"id": str(uuid4()), "text": "Spain's capital?"}])

    assert asyncio.run(answer()) == "Madrid."
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert "Do not summarize" in payload["task"]
    assert "No clear question" in payload["task"]
    assert "general knowledge" in requests[0]["messages"][0]["content"]
