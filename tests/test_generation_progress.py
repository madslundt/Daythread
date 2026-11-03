"""Redo exposes scoped numeric progress while complete model output stays private."""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from test_reprocessing import jobs as jobs
from test_reprocessing import recording

from omi_local.domain import EvidenceChanged
from omi_local.generation_progress import (
    GenerationProgress,
    generation_event,
    generation_phase,
    generation_request,
)
from omi_local.inference import Inference
from omi_local.reprocessing import RedoRequest, Reprocessing
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.structured_generation import SUMMARY_OUTPUT_TOKENS


@pytest.mark.parametrize("with_usage", [False, True])
def test_redo_reports_live_request_progress_and_clears_it_on_cancel(jobs, with_usage):
    recording(jobs)
    jobs.memory.settings = jobs.memory.settings.model_copy(update={"llm_enable_thinking": False})

    async def run():
        received, release = asyncio.Event(), asyncio.Event()

        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield ("data: " + json.dumps({"choices": [{"index": 0, "delta": {
                    "reasoning_content": "private reasoning must never leave the model adapter",
                }, "finish_reason": None}]}) + "\n\n").encode()
                if with_usage:
                    yield ("data: " + json.dumps({"choices": [], "usage": {
                        "prompt_tokens": 27, "completion_tokens": 123,
                    }}) + "\n\n").encode()
                received.set()
                await release.wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Pending(),
        ))) as client:
            jobs.memory.inference = Inference(jobs.memory.settings, client, task="chat")
            jobs.enqueue(RedoRequest(kind="summaries"))
            task = asyncio.create_task(jobs.process_one())
            try:
                await asyncio.wait_for(received.wait(), 1)
                progress = jobs.observation()["job"].get("progress")
                assert progress is not None
                assert progress["day"] == "2026-10-15"
                assert progress["stage"] == "daily_summary"
                assert progress["status"] == "reasoning"
                assert progress["thinking"] is False
                assert progress["max_tokens"] == 65536
                assert progress["reasoning_characters"] > 0
                assert progress["content_characters"] == 0
                assert progress["token_count_source"] == ("provider" if with_usage else "estimate")
                if with_usage:
                    assert progress["output_tokens"] == 123
                assert "private reasoning" not in json.dumps(progress)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            assert jobs.observation()["job"].get("progress") is None

    asyncio.run(run())


def test_summary_thinking_budget_and_live_progress_share_one_request(tmp_path):
    async def run():
        received, release = asyncio.Event(), asyncio.Event()
        requests = []

        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield ("data: " + json.dumps({"choices": [{"index": 0, "delta": {
                    "reasoning_content": "thinking",
                }, "finish_reason": None}]}) + "\n\n").encode()
                received.set()
                await release.wait()
                yield ("data: " + json.dumps({"choices": [{"index": 0, "delta": {
                    "content": "Complete",
                }, "finish_reason": None}]}) + "\n\n").encode()
                yield ("data: " + json.dumps({"choices": [{"index": 0, "delta": {},
                    "finish_reason": "stop"}]}) + "\n\n").encode()
                yield ("data: " + json.dumps({"choices": [], "usage": {
                    "prompt_tokens": 17, "completion_tokens": 2,
                }}) + "\n\n").encode()
                yield b'data: [DONE]\n\n'

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Pending())

        settings = Settings(
            data_dir=tmp_path, llm_enable_thinking=True,
            llm_summary_thinking_token_budget=8192, _env_file=None,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(settings, client, task="summary")
            progress = GenerationProgress("daily_summary", "2026-11-03")
            with generation_phase(progress):
                task = asyncio.create_task(inference.generate("Task", "Evidence"))
                try:
                    await asyncio.wait_for(received.wait(), 1)
                    snapshot = progress.snapshot()
                    assert snapshot["thinking"] is True
                    assert snapshot["max_tokens"] == SUMMARY_OUTPUT_TOKENS
                    assert snapshot["status"] == "reasoning"
                    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
                    assert requests[0]["thinking_token_budget"] == 8192
                    assert requests[0]["max_tokens"] == SUMMARY_OUTPUT_TOKENS
                finally:
                    release.set()
                assert await task == "Complete"
            assert progress.snapshot() is None

    asyncio.run(run())


@pytest.fixture
def clock(monkeypatch):
    now = [datetime(2026, 11, 3, 12, tzinfo=UTC)]
    ticks = [0.0]
    monkeypatch.setattr("omi_local.generation_progress.utc_now", lambda: now[0])
    monkeypatch.setattr("omi_local.generation_progress.monotonic", lambda: ticks[0])

    def advance(seconds):
        now[0] += timedelta(seconds=seconds)
        ticks[0] += seconds

    return advance


def test_progress_marks_estimates_and_uses_provider_counts_when_reported(clock):
    progress = GenerationProgress("daily_summary", "2026-11-03")
    with generation_phase(progress), generation_request(65536, False):
        generation_event(reasoning_characters=12, reasoning_bytes=12)
        assert progress.snapshot()["output_tokens"] == 3
        assert progress.snapshot()["token_count_source"] == "estimate"
        clock(2)
        generation_event(output_tokens=8)
        assert progress.snapshot()["output_tokens"] == 8
        assert progress.snapshot()["token_count_source"] == "provider"
        clock(2)
        generation_event(content_characters=4, content_bytes=4)
        snapshot = progress.snapshot()
        assert snapshot["output_tokens"] == 9  # Latest exact count + rough new UTF8-byte estimate.
        assert snapshot["token_count_source"] == "estimate"
        assert snapshot["reasoning_characters"] == 12
        assert snapshot["content_characters"] == 4
        generation_event(output_tokens=15, finished=True)
        assert progress.snapshot()["output_tokens"] == 15
        assert progress.snapshot()["token_count_source"] == "provider"


def test_quiet_only_reports_no_recent_tokens_and_heartbeats_do_not_prove_generation(clock):
    progress = GenerationProgress("daily_summary", "2026-11-03")
    with generation_phase(progress), generation_request(65536, True):
        assert progress.snapshot()["status"] == "waiting"
        clock(60)
        snapshot = progress.snapshot()
        assert snapshot["quiet"] is True
        assert snapshot["seconds_since_token"] == 60
        assert snapshot["last_token_at"] is None
        generation_event()
        assert progress.snapshot()["quiet"] is True
        generation_event(reasoning_characters=4, reasoning_bytes=4)
        assert progress.snapshot()["status"] == "reasoning"
        assert progress.snapshot()["quiet"] is False
        clock(61)
        generation_event()  # Empty/role/usage-free events are activity, not token evidence.
        assert progress.snapshot()["quiet"] is True
        assert progress.snapshot()["events"] == 3
        assert "stuck" not in progress.snapshot()


def test_counter_publication_is_throttled_without_losing_private_counts(clock):
    progress = GenerationProgress("daily_summary", "2026-11-03")
    with generation_phase(progress), generation_request(65536, False):
        generation_event(content_characters=1, content_bytes=1)
        assert progress.snapshot()["content_characters"] == 1
        clock(0.1)
        generation_event(content_characters=3, content_bytes=3)
        assert progress.snapshot()["content_characters"] == 1
        clock(1)
        generation_event(content_characters=4, content_bytes=4)
        assert progress.snapshot()["content_characters"] == 8
        assert progress.snapshot()["events"] == 3


def test_new_request_resets_counters_and_uses_actual_fallback_thinking_flag():
    progress = GenerationProgress("daily_summary", "2026-11-03")
    with generation_phase(progress):
        with generation_request(65536, True):
            generation_event(reasoning_characters=100, reasoning_bytes=100)
            first = progress.snapshot()
            assert first["thinking"] is True
        assert progress.snapshot()["request_id"] is None
        assert progress.snapshot()["output_tokens"] == 0
        with generation_request(65536, False):
            second = progress.snapshot()
            assert second["request_id"] != first["request_id"]
            assert second["request_sequence"] == first["request_sequence"] + 1
            assert second["reasoning_characters"] == second["content_characters"] == 0
            assert second["thinking"] is False
            assert second["status"] == "waiting"
    assert progress.snapshot() is None


def test_parallel_writers_keep_separate_request_counters():
    async def run():
        progress = GenerationProgress("daily_summary", "2026-11-03")
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]

        async def writer(index, amount):
            with generation_request(65536, bool(index)):
                generation_event(content_characters=amount, content_bytes=amount)
                entered[index].set()
                await release[index].wait()

        with generation_phase(progress):
            first = asyncio.create_task(writer(0, 2))
            await entered[0].wait()
            second = asyncio.create_task(writer(1, 20))
            await entered[1].wait()
            assert progress.snapshot()["active_requests"] == 2
            assert progress.snapshot()["content_characters"] == 2
            assert progress.snapshot()["thinking"] is False
            release[0].set()
            await first
            assert progress.snapshot()["active_requests"] == 1
            assert progress.snapshot()["content_characters"] == 20
            assert progress.snapshot()["thinking"] is True
            release[1].set()
            await second
            assert progress.snapshot()["active_requests"] == 0
            assert progress.snapshot()["content_characters"] == 0

    asyncio.run(run())


def test_parallel_phases_and_unbound_background_calls_do_not_mix():
    async def run():
        first = GenerationProgress("daily_summary", "2026-11-02")
        second = GenerationProgress("day_overview", "2026-11-03")
        entered, release = asyncio.Event(), asyncio.Event()

        async def bound():
            with generation_phase(first), generation_request(65536, False):
                generation_event(content_characters=4, content_bytes=4)
                entered.set()
                await release.wait()

        task = asyncio.create_task(bound())
        await entered.wait()
        generation_event(content_characters=9999, content_bytes=9999)  # Automatic background task.
        with generation_phase(second), generation_request(65536, True):
            generation_event(reasoning_characters=8, reasoning_bytes=8)
            assert first.snapshot()["content_characters"] == 4
            assert first.snapshot()["reasoning_characters"] == 0
            assert second.snapshot()["content_characters"] == 0
            assert second.snapshot()["reasoning_characters"] == 8
        release.set()
        await task
        assert first.snapshot() is second.snapshot() is None

    asyncio.run(run())


@pytest.mark.parametrize("error", [RuntimeError, EvidenceChanged])
def test_redo_failure_or_evidence_retry_clears_phase_progress(jobs, error):
    recording(jobs)
    jobs.enqueue(RedoRequest(kind="summaries"))
    snapshots = []

    async def recap(*args, **kwargs):
        with generation_request(65536, False):
            generation_event(content_characters=12, content_bytes=12)
            snapshots.append(jobs.observation()["job"]["progress"])
            raise error("No private provider detail may be forwarded")

    jobs.memory.recap = recap
    asyncio.run(jobs.process_one())
    assert snapshots[0]["status"] == "generating"
    assert snapshots[0]["output_tokens"] == 3
    assert jobs.observation()["job"]["progress"] is None
    assert jobs.observation()["job"]["state"] == (
        "failed" if error is RuntimeError else "summarizing"
    )


def test_redo_success_clears_request_and_phase_progress(jobs):
    recording(jobs)
    jobs.enqueue(RedoRequest(kind="summaries"))

    async def recap(*args, **kwargs):
        with generation_request(65536, False):
            generation_event(content_characters=12, content_bytes=12)
            assert jobs.observation()["job"]["progress"]["request_id"] is not None
        return {}

    jobs.memory.recap = recap
    assert asyncio.run(jobs.process_one())
    assert jobs.observation()["job"]["state"] == "completed"
    assert jobs.observation()["job"]["progress"] is None


def test_job_and_profile_observations_only_expose_their_own_bound_progress(jobs, tmp_path):
    recording(jobs)
    job = jobs.enqueue(RedoRequest(kind="summaries"))
    other_settings = jobs.memory.settings.model_copy(update={"data_dir": tmp_path / "other"})
    other = Reprocessing(Memory(Store(other_settings.data_dir), other_settings, SimpleNamespace()))
    recording(other)
    other.enqueue(RedoRequest(kind="summaries"))

    with jobs._track_progress(job["id"], "daily_summary", "2026-10-15"):
        with generation_request(65536, False):
            generation_event(content_characters=16, content_bytes=16)
            assert jobs.observation()["job"]["progress"]["content_characters"] == 16
            assert other.observation()["job"]["progress"] is None
            with jobs.store.connect() as db:
                previous = dict(db.execute("SELECT * FROM reprocessing_jobs").fetchone())
                previous["id"] = "previous-job"
                assert jobs.public_job(db, previous)["progress"] is None

    assert jobs.observation()["job"]["progress"] is None
