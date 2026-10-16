"""Weekly review contracts use disposable archives and simulated inference, never live data."""

import asyncio
import importlib.util
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.inference import InferenceError
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.weekly import WeeklyReviews
from omi_local.weekly_inference import decode_items
from omi_local.weekly_models import default_week, markdown, week_bounds

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
WEEK = date(2026, 10, 5)
AT = datetime(2026, 10, 6, 12, tzinfo=UTC)


def insert(store, text="I will send the proposal", at=AT, state="done", duration=30):
    key = str(uuid4())
    with store.connect() as db:
        db.execute(
            "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text,"
            "audio_available,source_kind,source_id) VALUES(?,?,?,?,?,?,?,?,0,'omi_api',?)",
            (
                key,
                "test",
                stamp(at),
                stamp(at + timedelta(seconds=duration)),
                "unused",
                str(store.audio_dir / f"{key}.wav"),
                state,
                text,
                key,
            ),
        )
    return key


def item(key, quote="I will send the proposal", category="commitment", status=None):
    return {
        "category": category,
        "claim": quote,
        "qualification": "Follow-through is not established by the recordings.",
        "status": status
        or ("follow_through_unconfirmed" if category == "commitment" else "supported"),
        "evidence": [{"source_id": key, "quote": quote}],
    }


class FakeInference:
    def __init__(self):
        self.calls = []
        self.mutate = None

    async def generate_json(self, task, payload):
        records = json.loads(payload)
        self.calls.append((task, records))
        if self.mutate:
            action, self.mutate = self.mutate, None
            action()
        if "Create the final" in task:
            candidates = [record["item"] for record in records]
            # Simulate matching explicit resolution evidence, not absence-based completion.
            resolved = any(value["category"] == "resolution" for value in candidates)
            return json.dumps(
                {
                    "items": [
                        value
                        for value in candidates
                        if value["category"] != "resolution"
                        and not (resolved and value["category"] == "commitment")
                    ]
                }
            )
        if "Consolidate" in task:
            return json.dumps({"items": [record["item"] for record in records]})
        return json.dumps(
            {
                "items": [
                    item(
                        record["id"],
                        record["text"].split("\n")[0],
                        "resolution" if "completed" in record["text"] else "commitment",
                    )
                    for record in records
                ]
            }
        )


@pytest.fixture
def weekly(tmp_path, monkeypatch):
    monkeypatch.setattr("omi_local.weekly.utc_now", lambda: NOW)
    monkeypatch.setattr("omi_local.weekly_models.utc_now", lambda: NOW)
    config = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    memory = Memory(Store(tmp_path), config, FakeInference())
    return WeeklyReviews(memory)


def finish(weekly, force=False, request_id=None):
    result = weekly.enqueue(WEEK, request_id or str(uuid4()), force)
    if result["job"]:
        asyncio.run(weekly.process_one())
        job = weekly.store.job(result["job"]["id"])
        assert job["state"] == "succeeded", job
    return weekly.read(WEEK)


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 23), 167), (date(2026, 10, 19), 169)])
def test_calendar_uses_local_monday_boundaries_across_dst(day, hours):
    before, start, end = week_bounds(day, "Europe/Copenhagen")
    assert (end - start).total_seconds() == hours * 3600
    assert (start - before).total_seconds() == 168 * 3600
    assert default_week("Europe/Copenhagen", NOW) == WEEK
    assert week_bounds(date(2025, 12, 29), "UTC")[2].date() == date(2026, 1, 5)
    with pytest.raises(ValueError):
        week_bounds(date(2026, 10, 6), "UTC")


def test_empty_partial_pending_and_exclusive_bounds(weekly):
    assert weekly.read(WEEK)["state"] == "empty"
    assert weekly.enqueue(WEEK, str(uuid4()), False)["state"] == "empty"
    insert(weekly.memory.store, None, AT, "queued")
    insert(weekly.memory.store, "", AT + timedelta(days=1))
    insert(weekly.memory.store, None, AT + timedelta(days=2), "expired")
    insert(weekly.memory.store, "outside", datetime(2026, 10, 12, tzinfo=UTC))
    result = weekly.read(WEEK)
    assert result["coverage"]["sources"] == 3
    assert result["coverage"]["pending_chunks"] == 1
    assert result["coverage"]["empty_chunks"] == 1
    assert result["coverage"]["expired_chunks"] == 1
    current = weekly.read(date(2026, 10, 12))
    assert current["partial"] and current["cutoff"] == stamp(NOW)
    assert current["comparison_coverage"]["sources"] == 3
    with pytest.raises(ValueError):
        weekly.read(date(2026, 10, 19))


def test_cached_versions_force_request_retries_and_markdown(weekly):
    key = insert(weekly.memory.store)
    request_id = str(uuid4())
    result = finish(weekly, request_id=request_id)
    assert result["version"]["items"][0]["status"] == "follow_through_unconfirmed"
    assert result["version"]["sources"][0]["id"] == key
    assert not result["stale"]
    calls = len(weekly.memory.inference.calls)
    assert finish(weekly)["version"]["id"] == result["version"]["id"]
    assert len(weekly.memory.inference.calls) == calls
    retry = weekly.enqueue(WEEK, request_id, False)
    assert retry["version_id"] == result["version"]["id"]
    with pytest.raises(ValueError):
        weekly.enqueue(WEEK, request_id, True)
    new = finish(weekly, force=True)
    assert new["version"]["id"] != result["version"]["id"]
    assert len(weekly.store.versions(WEEK.isoformat())) == 2
    exported = markdown(new["version"])
    assert key in exported and "I will send the proposal" in exported
    assert "backlog is unknown" in exported
    assert "Absence of later mention never establishes completion" in exported


def test_concurrent_requests_coalesce(weekly):
    insert(weekly.memory.store)
    first = weekly.enqueue(WEEK, str(uuid4()), False)
    second = weekly.enqueue(WEEK, str(uuid4()), True)
    assert first["job"]["id"] == second["job"]["id"]


@pytest.mark.parametrize("during_inference", [False, True])
def test_summary_language_change_rejects_queued_or_running_review(weekly, during_inference):
    insert(weekly.memory.store)
    queued = weekly.enqueue(WEEK, str(uuid4()), False)

    def change_language():
        # Publication must see the persisted write even before live settings activate.
        weekly.memory.store.save_settings({"summary_language": "da"})

    if during_inference:
        weekly.memory.inference.mutate = change_language
    else:
        change_language()
    asyncio.run(weekly.process_one())
    job = weekly.store.job(queued["job"]["id"])
    assert job["state"] == "failed"
    assert weekly.store.versions(WEEK.isoformat()) == []
    if not during_inference:
        assert weekly.memory.inference.calls == []


@pytest.mark.parametrize("offset", [0, -7])
def test_late_arrivals_in_either_window_invalidate_saved_review(weekly, offset):
    insert(weekly.memory.store)
    result = finish(weekly)
    insert(weekly.memory.store, "Additional promise", AT + timedelta(days=offset, hours=1))
    assert weekly.read(WEEK)["stale"]
    assert finish(weekly)["version"]["id"] != result["version"]["id"]


def test_correction_preserves_historical_interpretation_but_uses_current_sources(weekly):
    key = insert(weekly.memory.store)
    old = finish(weekly)["version"]
    weekly.memory.store.correct_transcript(key, "Corrected promise")
    result = weekly.read(WEEK, old["id"])
    assert result["stale"]
    assert result["version"]["sources"][0]["text"] == "I will send the proposal"
    assert result["sources"][0]["text"] == "Corrected promise"
    assert len(weekly.store.versions(WEEK.isoformat())) == 1


@pytest.mark.parametrize(
    "mutation", ["correction", "late_previous", "delete", "retention", "timezone"]
)
def test_mutation_during_inference_never_publishes_obsolete_or_deleted_evidence(weekly, mutation):
    store = weekly.memory.store
    key = insert(store)
    actions = {
        "correction": lambda: store.correct_transcript(key, "Corrected"),
        "late_previous": lambda: insert(store, "Late promise", AT - timedelta(days=7)),
        "delete": lambda: store.delete_audio(key),
        "retention": lambda: store.apply_retention(0, 1, NOW),
        "timezone": lambda: store.save_settings({"timezone": "Europe/Copenhagen"}),
    }
    weekly.memory.inference.mutate = actions[mutation]
    job = weekly.enqueue(WEEK, str(uuid4()), False)["job"]
    asyncio.run(weekly.process_one())
    assert not weekly.store.versions(WEEK.isoformat())
    result = weekly.store.job(job["id"])
    assert result is None or result["state"] == "failed"


def test_jobs_survive_restart_recover_and_bound_interruption_retries(weekly):
    insert(weekly.memory.store)
    job = weekly.enqueue(WEEK, str(uuid4()), False)["job"]
    assert weekly.store.claim()["id"] == job["id"]
    restarted = WeeklyReviews(weekly.memory)
    restarted.store.recover()
    asyncio.run(restarted.process_one())
    assert restarted.store.job(job["id"])["attempts"] == 2
    assert restarted.store.job(job["id"])["state"] == "succeeded"
    other = restarted.enqueue(WEEK, str(uuid4()), True)["job"]
    for _ in range(3):
        restarted.store.claim()
        restarted.store.recover()
    assert restarted.store.job(other["id"])["state"] == "failed"


def test_model_failure_sanitized_and_explicit_retry_works(weekly):
    key = insert(weekly.memory.store)

    async def broken(task, payload):
        raise RuntimeError("SECRET captured transcript provider body")

    inference = weekly.memory.inference
    weekly.memory.inference = type("Broken", (), {"generate_json": staticmethod(broken)})()
    job = weekly.enqueue(WEEK, str(uuid4()), False)["job"]
    asyncio.run(weekly.process_one())
    failed = weekly.store.job(job["id"])
    assert failed["state"] == "failed" and "SECRET" not in failed["error"]
    weekly.memory.inference = inference
    assert finish(weekly)["version"]["sources"][0]["id"] == key


def test_all_gaps_and_overlapping_sources_are_counted_without_sampling(weekly):
    key = insert(weekly.memory.store)
    insert(weekly.memory.store, "second", AT + timedelta(seconds=10))
    with weekly.memory.store.connect() as db:
        db.executemany(
            "INSERT INTO capture_gaps(gap_id,device_id,started_at,ended_at,reason) "
            "VALUES(?,?,?,?,?)",
            [
                (str(uuid4()), "test", stamp(AT), stamp(AT + timedelta(seconds=1)), "gap")
                for _ in range(1001)
            ],
        )
    snapshot = weekly.snapshot(WEEK)
    assert snapshot["coverage"]["capture_gaps"] == 1001
    assert snapshot["coverage"]["known_gap_seconds"] == 1
    assert snapshot["coverage"]["captured_seconds"] == 40
    assert len(snapshot["sources"]) == 2
    assert any(source["id"] == key for source in snapshot["sources"])


def test_quote_and_category_validation(weekly):
    key = insert(weekly.memory.store)
    sources = weekly.snapshot(WEEK)["sources"]
    for bad in [
        item(str(uuid4())),
        item(key, "not in original text"),
        item(key, category="commitment", status="supported"),
        item(key, category="change"),
        item(key, category="discussion"),
    ]:
        with pytest.raises(InferenceError):
            decode_items(json.dumps({"items": [bad]}), sources, "UTC", True)
    missing = item(key)
    missing["evidence"] = []
    with pytest.raises(InferenceError):
        decode_items(json.dumps({"items": [missing]}), sources, "UTC", True)
    assert decode_items(json.dumps({"items": [item(key)]}), sources, "UTC", True)


def test_recurrence_cross_week_changes_and_boundary_sources(weekly):
    previous = insert(weekly.memory.store, "Delivery Friday", AT - timedelta(days=7))
    current = insert(weekly.memory.store, "Delivery Monday", AT)
    same_day = insert(weekly.memory.store, "Delivery discussion", AT + timedelta(hours=1))
    boundary = insert(
        weekly.memory.store, "Boundary", datetime(2026, 10, 4, 23, 59, tzinfo=UTC), duration=120
    )
    sources = weekly.snapshot(WEEK)["sources"]
    assert next(s for s in sources if s["id"] == boundary)["role"] == "boundary"
    with pytest.raises(InferenceError):
        decode_items(
            json.dumps({"items": [item(boundary, "Boundary", "decision")]}), sources, "UTC", True
        )
    changed = item(current, "Delivery Monday", "change")
    changed["evidence"].append({"source_id": previous, "quote": "Delivery Friday"})
    assert decode_items(json.dumps({"items": [changed]}), sources, "UTC", True)
    recurring = dict(changed, category="discussion")
    assert decode_items(json.dumps({"items": [recurring]}), sources, "UTC", True)
    recurring["evidence"] = [
        {"source_id": current, "quote": "Delivery Monday"},
        {"source_id": same_day, "quote": "Delivery discussion"},
    ]
    with pytest.raises(InferenceError):
        decode_items(json.dumps({"items": [recurring]}), sources, "UTC", True)


def test_cross_batch_resolution_and_source_manifest_survive_extraction(weekly):
    insert(weekly.memory.store, "I will send the proposal\n" + "x" * 5900)
    insert(weekly.memory.store, "I completed the proposal\n" + "y" * 5900, AT + timedelta(days=1))
    result = finish(weekly)
    assert result["version"]["items"] == []
    calls = weekly.memory.inference.calls
    assert len(calls) >= 3  # Two original batches, then final synthesis.
    final_task, final_evidence = calls[-1]
    assert "NEVER means completed" in final_task
    assert {record["item"]["category"] for record in final_evidence} == {"commitment", "resolution"}
    assert {record["source_context"][0]["local_date"] for record in final_evidence} == {
        "2026-10-06",
        "2026-10-07",
    }
    assert all(record["source_context"][0]["role"] == "current" for record in final_evidence)


def test_processing_budget_fails_without_saving_partial_review(weekly, monkeypatch):
    insert(weekly.memory.store)
    monkeypatch.setattr("omi_local.weekly_inference.MAX_MODEL_CALLS", 1)
    job = weekly.enqueue(WEEK, str(uuid4()), False)["job"]
    asyncio.run(weekly.process_one())
    assert weekly.store.job(job["id"])["state"] == "failed"
    assert not weekly.store.versions(WEEK.isoformat())


def test_backup_restore_versions_and_restored_markdown_obey_deletion(weekly, tmp_path):
    key = insert(weekly.memory.store)
    version = finish(weekly)["version"]
    archive = weekly.memory.store.export_archive()
    with zipfile.ZipFile(archive) as zipped:
        assert f"weekly/{version['id']}.md" in zipped.namelist()
    spec = importlib.util.spec_from_file_location("archive_cli", Path("scripts/archive.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "restored"
    module.restore(archive, target, 10_000_000)
    restored = Store(target)
    review = WeeklyReviews(Memory(restored, weekly.memory.settings, FakeInference()))
    assert review.store.version(WEEK.isoformat())["id"] == version["id"]
    assert list((target / "weekly").glob("*.md"))
    restored.delete_audio(key)
    assert not review.store.versions(WEEK.isoformat())
    assert not list((target / "weekly").glob("*.md"))


def test_api_cached_get_validation_export_and_background_generation(weekly):
    key = insert(weekly.memory.store)
    with TestClient(create_app(weekly.memory.settings)) as client:
        client.app.state.memory.inference = weekly.memory.inference
        assert client.get("/v1/weekly-reviews").json()["week_start"] == WEEK.isoformat()
        assert client.get("/v1/weekly-reviews/2026-10-06").status_code == 422
        assert client.get("/v1/weekly-reviews/2026-10-19").status_code == 422
        response = client.post(
            f"/v1/weekly-reviews/{WEEK}/generate", json={"request_id": str(uuid4())}
        )
        assert response.status_code == 202
        job_id = response.json()["job"]["id"]
        # Dispatch the real durable consumer through the TestClient event loop, no live inference.
        client.portal.call(client.app.state.weekly.process_one)
        job = client.get(f"/v1/weekly-reviews/jobs/{job_id}").json()
        assert job["state"] == "succeeded"
        assert client.get(f"/v1/weekly-reviews/{WEEK}").json()["version"]["items"]
        assert key in client.get(f"/v1/weekly-reviews/{WEEK}.md").text
        assert len(client.get(f"/v1/weekly-reviews/{WEEK}/versions").json()["items"]) == 1
        assert client.delete(f"/v1/audio/{key}").status_code == 200
        assert client.get(f"/v1/weekly-reviews/jobs/{job_id}").status_code == 404
        assert client.get(f"/v1/weekly-reviews/{WEEK}.md").status_code == 404
