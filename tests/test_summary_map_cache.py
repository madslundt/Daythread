"""Request-local sharing for concurrent daily-summary map reductions."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from omi_local.chat_context import bounded_generation
from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings
from omi_local.summary_map_cache import summary_map_cache_scope


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(content):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": content},
    }]})


@pytest.mark.anyio
async def test_daily_topic_writers_share_identical_raw_map_but_write_from_original_source(tmp_path):
    source_id = str(uuid4())
    map_calls, writer_calls, all_calls = [], [], []
    marker = "ORIGINAL_TRANSCRIPT_EVIDENCE_MARKER"
    original_text = (marker + " supports one useful recorded fact.\n") * 1400
    note_text = (
        f"## Alpha\n* Alpha is one scoped subject. [{source_id}]\n\n"
        f"## Beta\n* Beta is another scoped subject. [{source_id}]"
    )
    notes = [{"id": source_id, "started_at": "2026-10-20T10:00:00Z", "text": note_text}]
    originals = [{"id": source_id, "started_at": "2026-10-20T10:00:00Z",
                  "text": original_text}]

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        all_calls.append(payload)
        task, evidence = payload["task"], payload["evidence"]
        if task.startswith("Review the complete daily topic plan"):
            plan = json.loads(evidence)["proposed_topics"]
            return response(json.dumps({"topics": plan}))
        if task.startswith("Organize useful recorded information"):
            rows = json.loads(evidence)
            if isinstance(rows, dict):
                return response(json.dumps({"topics": rows["proposed_topics"]}))
            return response(json.dumps({"topics": [
                {"title": row["context"], "scope": row["context"], "items": [row["id"]]}
                for row in rows
            ]}))
        if "intermediate evidence notes" in task:
            assert marker in evidence
            map_calls.append(evidence)
            return response(f"The original recording supports this fact. [{source_id}]")
        writer_calls.append((task, evidence))
        return response(f"The original recording supports this topic. [{source_id}]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        result, trace = await inference.summarize_daily_traced(
            notes, "a daily recap", original_sources=originals,
        )

    assert "## Alpha" in result and "## Beta" in result
    assert result.count(f"[{source_id}]") >= 2
    # Both writers receive the same original source; their intermediate map batches
    # are shared, while their final topic tasks remain distinct.
    assert len(writer_calls) == 2
    assert len(map_calls) > 1
    assert all(marker in evidence for evidence in map_calls)
    assert trace["map_cache_hits"] > 0
    assert trace["model_calls"] == len(all_calls)
    assert trace["writing_evidence"] == "original_transcripts"


@pytest.mark.anyio
async def test_map_cache_reuses_only_identical_evidence_language_sources_and_route(tmp_path):
    first_id, other_id = str(uuid4()), str(uuid4())
    calls = []

    def provider(request):
        calls.append((request.url.host, json.loads(request.content)))
        return response("Supported map note")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        model = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        other_route = Inference(Settings(
            data_dir=tmp_path, llm_model="alternate", _env_file=None,
        ), client)
        thinking_a = Inference(Settings(
            data_dir=tmp_path, llm_enable_thinking=True,
            llm_summary_thinking_token_budget=4096, _env_file=None,
        ), client)
        thinking_b = Inference(Settings(
            data_dir=tmp_path, llm_enable_thinking=True,
            llm_summary_thinking_token_budget=2048, _env_file=None,
        ), client)
        allowed = [{"id": first_id}]
        trace_a = {"model_calls": 0}
        trace_b = {"model_calls": 0}
        trace_c = {"model_calls": 0}
        trace_d = {"model_calls": 0}
        trace_e = {"model_calls": 0}
        trace_f = {"model_calls": 0}
        trace_g = {"model_calls": 0}
        trace_h = {"model_calls": 0}
        trace_i = {"model_calls": 0}
        trace_j = {"model_calls": 0}
        async with summary_map_cache_scope() as cache:
            first = await model._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_a, 5000, " Write in English.",
            )
            repeated = await model._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_b, 5000, " Write in English.",
            )
            await model._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_c, 5000, " Write in Danish.",
            )
            await model._summary_notes(
                f"Transcript B [{first_id}]", allowed, trace_d, 5000, " Write in English.",
            )
            await other_route._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_e, 5000, " Write in English.",
            )
            await model._summary_notes(
                f"Transcript A [{other_id}]", [{"id": other_id}], trace_e, 5000,
                " Write in English.",
            )
            await thinking_a._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_f, 5000, " Write in English.",
            )
            await thinking_b._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_g, 5000, " Write in English.",
            )
            await model._summary_notes(
                f"Transcript A [{first_id}]", allowed, trace_h, 4000, " Write in English.",
            )
            with bounded_generation(50_000):
                await model._summary_notes(
                    f"Transcript A [{first_id}]", allowed, trace_i, 5000,
                    " Write in English.",
                )
            with bounded_generation(40_000):
                await model._summary_notes(
                    f"Transcript A [{first_id}]", allowed, trace_j, 5000,
                    " Write in English.",
                )
            assert cache.hits == 1

    assert first == repeated == "Supported map note"
    assert len(calls) == 10
    assert [trace_a["model_calls"], trace_b["model_calls"], trace_c["model_calls"],
            trace_d["model_calls"]] == [1, 0, 1, 1]
    assert calls[5][1]["thinking_token_budget"] == 4096
    assert calls[6][1]["thinking_token_budget"] == 2048
    assert calls[-2][1]["max_tokens"] != calls[-1][1]["max_tokens"]


@pytest.mark.anyio
async def test_map_cache_failure_is_evicted_for_later_retry(tmp_path):
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return response("Unsupported citation [src_999]")
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        key = payload["evidence"].split("[")[-1].split("]")[0]
        return response(f"Supported map note [{key}]")

    source_id = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        model = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        async with summary_map_cache_scope() as cache:
            with pytest.raises(InferenceError):
                await model._summary_notes(
                    f"Transcript [{source_id}]", [{"id": source_id}],
                    {"model_calls": 0}, 5000, " Write in English.",
                )
            result = await model._summary_notes(
                f"Transcript [{source_id}]", [{"id": source_id}],
                {"model_calls": 0}, 5000, " Write in English.",
            )
            assert cache.hits == 0
    assert result == f"Supported map note [{source_id}]"
    assert calls == 3  # Invalid first answer, one repair, then a fresh retry.


@pytest.mark.anyio
async def test_canceled_waiter_does_not_cancel_other_waiter_or_shared_map_task():
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "shared note"

    async with summary_map_cache_scope() as cache:
        canceled_waiter = asyncio.create_task(cache.get_or_create("same", factory))
        await started.wait()
        surviving_waiter = asyncio.create_task(cache.get_or_create("same", factory))
        await asyncio.sleep(0)
        canceled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await canceled_waiter
        release.set()
        assert await surviving_waiter == "shared note"
        assert calls == 1 and cache.hits == 1


def test_canceled_sole_waiter_followed_by_failed_factory_can_retry_in_same_scope():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                raise ValueError("failed map")
            return "retried map"

        async with summary_map_cache_scope() as cache:
            waiter = asyncio.create_task(cache.get_or_create("same", factory))
            await started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            shared_task = cache._tasks["same"]
            release.set()
            done, _ = await asyncio.wait([shared_task], timeout=1)
            assert shared_task in done
            assert await cache.get_or_create("same", factory) == "retried map"
            assert calls == 2 and cache.hits == 0

    asyncio.run(scenario())


@pytest.mark.anyio
async def test_request_scope_cancels_unfinished_map_tasks_and_resets_cache():
    started = asyncio.Event()
    canceled = asyncio.Event()
    calls = 0

    async def unfinished():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled.set()
            raise

    async with summary_map_cache_scope() as first_scope:
        first_waiter = asyncio.create_task(first_scope.get_or_create("same", unfinished))
        await started.wait()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter
    assert canceled.is_set()

    async def count_call():
        nonlocal calls
        calls += 1
        return "fresh request result"

    async with summary_map_cache_scope() as second_scope:
        assert await second_scope.get_or_create("same", count_call) == "fresh request result"
        assert second_scope.hits == 0
    assert calls == 1
