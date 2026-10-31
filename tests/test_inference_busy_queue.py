"""Admission rejections remain durable; no live model or notification service is used."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from test_generation_jobs import jobs as jobs
from test_generation_jobs import request
from test_inbox import Model, source
from test_inbox import archive as archive

from omi_local import generation_jobs, weekly
from omi_local.domain import utc_now
from omi_local.generation_jobs import GenerationJobs
from omi_local.inbox_jobs import InboxJobs
from omi_local.inference import Inference
from omi_local.inference_errors import InferenceUnavailable
from omi_local.settings import Settings
from omi_local.weekly import WeeklyReviews


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "enabled,status,error_type,retry,url,provider,queued",
    [
        (
            True,
            503,
            "service_unavailable",
            "30",
            "https://gateway.test/v1/chat/completions",
            "llm",
            True,
        ),
        (
            False,
            503,
            "service_unavailable",
            "30",
            "https://gateway.test/v1/chat/completions",
            "llm",
            False,
        ),
        (
            True,
            500,
            "service_unavailable",
            "30",
            "https://gateway.test/v1/chat/completions",
            "llm",
            False,
        ),
        (
            True,
            503,
            "provider_error",
            "30",
            "https://gateway.test/v1/chat/completions",
            "llm",
            False,
        ),
        (
            True,
            503,
            "service_unavailable",
            "31",
            "https://gateway.test/v1/chat/completions",
            "llm",
            False,
        ),
        (
            True,
            503,
            "service_unavailable",
            "30",
            "https://other.test/v1/chat/completions",
            "llm",
            False,
        ),
        (
            True,
            503,
            "service_unavailable",
            "30",
            "https://gateway.test/v1/chat/completions",
            "stt",
            False,
        ),
    ],
)
async def test_only_opted_in_configured_gateway_admission_rejection_queues(
    tmp_path,
    enabled,
    status,
    error_type,
    retry,
    url,
    provider,
    queued,
):
    settings = Settings(
        data_dir=tmp_path,
        _env_file=None,
        llm_busy_retry_enabled=enabled,
        llm_base_url="https://gateway.test/v1",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                headers={"Retry-After": retry},
                json={"error": {"type": error_type}},
            )
        )
    ) as client:
        inference = Inference(settings, client)
        with pytest.raises(InferenceUnavailable if queued else httpx.HTTPStatusError):
            await inference.post(provider, url)
    assert settings.llm_enabled and settings.stt_enabled


def test_generation_busy_wait_persists_restart_then_completes_once(jobs, monkeypatch):
    now = utc_now()
    monkeypatch.setattr(generation_jobs, "utc_now", lambda: now)
    body = request()
    job = jobs.enqueue(body)
    calls = []

    async def execute(_):
        calls.append(True)
        if len(calls) == 1:
            raise InferenceUnavailable("busy")
        return {"summary": "Ready", "sources": []}

    jobs.execute = execute
    assert asyncio.run(jobs.process_one())
    assert jobs.get(job["id"])["state"] == "queued"
    assert not jobs.get(job["id"])["error"]
    assert jobs.get(job["id"])["retry_at"]
    jobs.notify_completed()
    assert not jobs.store.notifications_after(0)["notifications"]
    restarted = GenerationJobs(jobs.memory)
    restarted.execute = execute
    assert not asyncio.run(restarted.process_one())
    assert len(calls) == 1
    monkeypatch.setattr(generation_jobs, "utc_now", lambda: now + timedelta(seconds=31))
    assert asyncio.run(restarted.process_one())
    restarted.notify_completed()
    restarted.notify_completed()
    assert restarted.get(job["id"])["state"] == "completed"
    assert len(calls) == 2
    assert len(jobs.store.notifications_after(0)["notifications"]) == 1


def test_existing_generation_queue_migrates_without_losing_requests(jobs):
    job = jobs.enqueue(request())
    with jobs.store.connect() as db:
        db.execute("ALTER TABLE generation_jobs DROP COLUMN retry_at")
    restored = GenerationJobs(jobs.memory)
    assert restored.get(job["id"])["state"] == "queued"
    assert restored.get(job["id"])["retry_at"] == ""


def test_inbox_busy_refunds_attempt_and_releases_fenced_claim(archive):
    source(archive)
    jobs = InboxJobs(archive)
    key = jobs.discover("Europe/Copenhagen")[0]
    assert asyncio.run(jobs.process_one(Model(InferenceUnavailable("busy"))))
    assert jobs.status(key)["state"] == "queued"
    assert jobs.status(key)["attempts"] == 0
    assert jobs.status(key)["error"] is None
    restarted = InboxJobs(archive)
    assert not asyncio.run(restarted.process_one(Model([])))
    with archive.connect() as db:
        row = db.execute("SELECT claim,retry_at FROM inbox_jobs WHERE id=?", (key,)).fetchone()
        assert row["claim"] is None and row["retry_at"]
        db.execute("UPDATE inbox_jobs SET retry_at='' WHERE id=?", (key,))
    assert asyncio.run(restarted.process_one(Model([])))
    assert restarted.status(key)["state"] == "done"
    assert not asyncio.run(restarted.process_one(Model([])))


def test_weekly_busy_refunds_attempt_and_persists_cooldown(jobs, monkeypatch):
    reviews = WeeklyReviews(jobs.memory)
    snapshot = {
        "fingerprint": "same",
        "timezone": "UTC",
        "cutoff": "2026-10-01T00:00:00Z",
        "sources": [],
        "summary_language": "en",
    }
    queued = reviews.store.enqueue("2026-09-28", snapshot, "busy-weekly-test", False)
    reviews.snapshot = lambda *args, **kwargs: snapshot

    async def busy(*args, **kwargs):
        raise InferenceUnavailable("busy")

    monkeypatch.setattr(weekly, "generate_items", busy)
    assert asyncio.run(reviews.process_one())
    job = reviews.store.job(queued["job"]["id"])
    assert job["state"] == "queued" and job["attempts"] == 0 and job["error"] is None
    assert not asyncio.run(reviews.process_one())


@pytest.mark.anyio
async def test_partial_stream_and_uncertain_provider_errors_are_never_busy_replayed(tmp_path):
    from omi_local.inference_errors import InferenceError

    settings = Settings(
        data_dir=tmp_path,
        _env_file=None,
        llm_busy_retry_enabled=True,
        llm_base_url="https://gateway.test/v1",
    )
    calls, deltas = [], []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"Already started"}}]}\n\n'
                'data: {"error":{"type":"service_unavailable"}}\n\n'
            ),
        )

    async def delta(value):
        deltas.append(value)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        inference = Inference(settings, client)
        with pytest.raises(InferenceError) as error:
            await inference.generate_stream("test", "evidence", delta)
    assert not isinstance(error.value, InferenceUnavailable)
    assert len(calls) == 1 and deltas == ["Already started"]
