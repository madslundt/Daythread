"""Phone-independent generation, retry identities, and saved result lifetimes."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.completion_events import publish_recap, publish_weekly
from omi_local.generation_jobs import GenerationJobs, GenerationRequest
from omi_local.notifications import NotificationService
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store


@pytest.fixture
def jobs(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    memory = Memory(Store(tmp_path), settings, SimpleNamespace())
    memory.notifications = NotificationService(memory.store, settings)
    jobs = GenerationJobs(memory)
    yield jobs
    asyncio.run(memory.notifications.close())


def request(kind="rewind", **params):
    return GenerationRequest(id=uuid4(), kind=kind, parameters=params)


def test_retry_identity_freezes_window_and_rejects_conflicting_reuse(jobs):
    body = request(minutes=17)
    first = jobs.enqueue(body)
    assert first["parameters"]["end"]
    assert jobs.enqueue(body) == first
    with pytest.raises(HTTPException) as error:
        jobs.enqueue(body.model_copy(update={"parameters": {"minutes": 18}}))
    assert error.value.status_code == 409
    assert GenerationJobs(jobs.memory).get(str(body.id)) == first


def test_completed_job_is_saved_and_notified_exactly_once(jobs):
    body = request(minutes=17, end="2026-10-17T10:00:00Z")
    jobs.enqueue(body)
    calls = []

    async def window(start, end, purpose, summarize, kind):
        calls.append((start, end))
        return {"summary": "Discussed the launch date.", "sources": []}

    jobs.memory.window = window
    asyncio.run(jobs.process_one())
    jobs.notify_completed()
    jobs.notify_completed()
    assert jobs.get(str(body.id))["result"]["summary"] == "Discussed the launch date."
    assert not asyncio.run(jobs.process_one())
    assert len(calls) == 1
    notices = jobs.store.notifications_after(0)["notifications"]
    assert len(notices) == 1
    assert "17-minute" in notices[0]["title"]
    assert "17 Oct" in notices[0]["body"]
    assert notices[0]["target"]["job_id"] == str(body.id)


def test_audio_only_rewind_still_notifies_and_does_not_block_next_job(jobs):
    first = jobs.enqueue(request(summarize=False))
    second = jobs.enqueue(request(minutes=20))

    async def window(*args, **kwargs):
        return {"summary": None, "sources": []}

    jobs.memory.window = window
    asyncio.run(jobs.process_one())
    jobs.notify_completed()
    asyncio.run(jobs.process_one())
    jobs.notify_completed()
    assert jobs.get(first["id"])["state"] == "completed"
    assert jobs.get(second["id"])["state"] == "completed"
    assert len(jobs.store.notifications_after(0)["notifications"]) == 2


def test_deleted_result_cannot_be_republished_after_notification_read(jobs):
    key = str(uuid4())
    jobs.store.ingest(key, "test", datetime.now(UTC), b"test", 30)
    jobs.store.complete(key, "Original evidence")
    job = jobs.enqueue(request())
    jobs.complete(job, {"summary": "Original evidence", "sources": [jobs.store.get_audio(key)]})
    publish = jobs.memory.notifications.publish

    def concurrent_delete(**kwargs):
        with jobs.store.connect() as db:
            db.execute("DELETE FROM audio WHERE id=?", (key,))
        return publish(**kwargs)

    jobs.memory.notifications.publish = concurrent_delete
    jobs.notify_completed()
    assert not jobs.store.notifications_after(0)["notifications"]


def test_cancelled_worker_requeues_and_restart_recovers_running(jobs):
    body = request()
    jobs.enqueue(body)

    async def run():
        started = asyncio.Event()

        async def execute(job):
            started.set()
            await asyncio.Event().wait()

        jobs.execute = execute
        task = asyncio.create_task(jobs.process_one())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert jobs.get(str(body.id))["state"] == "queued"
    with jobs.store.connect() as db:
        db.execute("UPDATE generation_jobs SET state='running'")
    assert GenerationJobs(jobs.memory).get(str(body.id))["state"] == "queued"


def test_failure_never_saves_provider_details_and_notifies(jobs):
    body = request()
    jobs.enqueue(body)

    async def execute(job):
        raise RuntimeError("secret-provider-token private transcript")

    jobs.execute = execute
    asyncio.run(jobs.process_one())
    jobs.notify_completed()
    assert jobs.get(str(body.id))["state"] == "failed"
    assert "secret" not in json.dumps(jobs.get(str(body.id)))
    notices = jobs.store.notifications_after(0)["notifications"]
    assert len(notices) == 1
    assert "could not finish" in notices[0]["title"]


@pytest.mark.parametrize("remove", [False, True])
def test_source_correction_or_deletion_invalidates_saved_results(jobs, remove):
    key = str(uuid4())
    jobs.store.ingest(key, "test", datetime.now(UTC), b"test", 30)
    jobs.store.complete(key, "Original evidence")
    source = jobs.store.get_audio(key)
    body = request()
    job = jobs.enqueue(body)
    jobs.complete(job, {"summary": "Original evidence", "sources": [source]})
    jobs.notify_completed()
    with jobs.store.connect() as db:
        if remove:
            db.execute("DELETE FROM audio WHERE id=?", (key,))
        else:
            db.execute("UPDATE audio SET text='Corrected' WHERE id=?", (key,))
    assert jobs.get(str(body.id))["result"] is None
    assert jobs.get(str(body.id))["state"] == "failed"
    assert not jobs.store.notifications_after(0)["notifications"]


def test_scheduled_completion_metadata_and_deduplication(jobs):
    recap = {"day": "2026-10-17", "summary": "## Project update\nDiscussed next week."}
    publish_recap(jobs.memory, recap, "fingerprint")
    publish_recap(jobs.memory, recap, "fingerprint")
    publish_weekly(jobs.memory, {"id": "version", "week_start": "2026-10-05", "items": [1, 2]})
    notices = jobs.store.notifications_after(0)["notifications"]
    assert len(notices) == 2
    assert notices[0]["target"] == {"day": "2026-10-17"}
    assert "Project update" in notices[0]["body"]
    assert "2 recorded" in notices[1]["body"]


def test_api_accepts_job_before_work_and_profile_isolates_result(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        body = {"id": str(uuid4()), "kind": "rewind", "parameters": {"minutes": 10}}
        response = client.post("/v1/jobs", json=body)
        assert response.status_code == 202
        saved = response.json()
        assert saved["state"] == "queued"
        assert client.get(f"/v1/jobs/{body['id']}").status_code == 200
        other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
        assert client.get(f"/v1/jobs/{body['id']}",
                          headers={"X-Omi-Profile": other}).status_code == 404
        invalid = body | {"parameters": {"minutes": 61}}
        assert client.post("/v1/jobs", json=invalid).status_code == 422


def test_translation_job_reuses_real_generation_and_survives_disconnected_client(jobs):
    key = str(uuid4())
    jobs.store.ingest(key, "test", datetime(2026, 10, 17, 10, tzinfo=UTC), b"test", 30)
    jobs.store.complete(key, "The customer wants us to send a revised proposal next week.")
    jobs.memory.settings.translation_languages = ["da"]

    async def translate(text, language):
        assert language == "da"
        return "Kunden vil gerne have et revideret forslag i næste uge."

    jobs.memory.inference.translate = translate
    body = request(kind="translation", source_id=key, language="da")
    jobs.enqueue(body)
    asyncio.run(jobs.process_one())
    jobs.notify_completed()
    saved = jobs.get(str(body.id))
    assert saved["state"] == "completed"
    assert saved["result"]["translation"]["language"] == "da"
    notice = jobs.store.notifications_after(0)["notifications"][0]
    assert "Danish" in notice["title"]
    assert "17 Oct" in notice["body"]
