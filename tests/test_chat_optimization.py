"""Chat quality/latency regressions against disposable archives and controlled models."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.embeddings import EmbeddingError, Embeddings
from omi_local.hybrid import HybridRetriever
from omi_local.inference import Inference
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def config(tmp_path, **kwargs):
    return Settings(
        data_dir=tmp_path, worker_enabled=False, timezone="UTC", _env_file=None, **kwargs
    )


def insert(store, text, at=AT):
    key = uuid4()
    store.ingest(key, "omi", at, b"audio", 30)
    store.complete(str(key), text)
    return str(key)


@pytest.mark.anyio
async def test_topical_evidence_includes_distant_matches_in_a_long_recording(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path)
    key = insert(
        store,
        "Ada joined the discussion. " + "Weather report. " * 700 + "The budget deadline is Friday.",
    )
    result = await Memory(store, settings, None).search("Ada budget deadline", 12)
    source = result["sources"][0]
    supplied = "\n".join(match["text"] for match in source["matches"])
    assert source["id"] == key
    assert "Ada joined" in supplied and "budget deadline is Friday" in supplied
    assert sum(len(match["text"]) for row in result["sources"] for match in row["matches"]) <= 12000


@pytest.mark.anyio
async def test_referential_followup_retrieves_the_previous_topic(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path)
    key = insert(store, "The invoice must be paid Friday.")
    insert(store, "A different delivery is due next month.")
    session = store.ensure_session(None, "invoice")
    store.save_exchange(session["id"], "Who handles the invoice?", "Unknown", [])
    result = await Memory(store, settings, None).search("When is it due?", 12, session["id"])
    assert key in {source["id"] for source in result["sources"]}
    assert result["retrieval_details"]["contextual_query"]


@pytest.mark.anyio
async def test_query_vectors_are_cached_coalesced_and_invalidated_by_model_revision(tmp_path):
    settings = config(
        tmp_path,
        embedding_enabled=True,
        embedding_model="test",
        embedding_revision="v1",
        embedding_dimension=2,
    )
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        embeddings = Embeddings(settings, http)
        vectors = await asyncio.gather(
            *(embeddings.embed(["invoice"], query=True) for _ in range(5))
        )
        assert vectors == [[[1, 0]]] * 5
        assert len(calls) == 1
        await embeddings.embed(["invoice"], query=True)
        assert len(calls) == 1
        settings.embedding_revision = "v2"
        await embeddings.embed(["invoice"], query=True)
        assert len(calls) == 2
        await embeddings.embed(["invoice"])
        assert len(calls) == 3  # Background indexing never uses the query cache.


@pytest.mark.anyio
async def test_cancelling_one_query_does_not_cancel_another_waiter(tmp_path):
    settings = config(
        tmp_path,
        embedding_enabled=True,
        embedding_model="test",
        embedding_revision="v1",
        embedding_dimension=2,
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        embeddings = Embeddings(settings, http)
        first = asyncio.create_task(embeddings.embed(["invoice"], query=True))
        await started.wait()
        second = asyncio.create_task(embeddings.embed(["invoice"], query=True))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == [[1, 0]]


@pytest.mark.anyio
async def test_cancelling_the_last_query_consumer_cancels_the_provider(tmp_path):
    settings = config(
        tmp_path,
        embedding_enabled=True,
        embedding_model="test",
        embedding_revision="v1",
        embedding_dimension=2,
    )
    started, closed = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        embeddings = Embeddings(settings, http)
        task = asyncio.create_task(embeddings.embed(["invoice"], query=True))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()


@pytest.mark.anyio
async def test_embedding_outage_has_a_fast_query_deadline_and_cooldown(tmp_path):
    settings = config(
        tmp_path,
        embedding_enabled=True,
        embedding_model="test",
        embedding_revision="v1",
        embedding_dimension=2,
        embedding_query_timeout_seconds=0.02,
    )
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(10)
        raise AssertionError("The query deadline must cancel the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        embeddings = Embeddings(settings, http)
        async with asyncio.timeout(0.3):
            for question in ("invoice", "other question"):
                with pytest.raises(EmbeddingError):
                    await embeddings.embed([question], query=True)
        assert len(calls) == 1


@pytest.mark.anyio
async def test_keyword_search_and_query_embedding_overlap(tmp_path, monkeypatch):
    settings = config(
        tmp_path,
        embedding_enabled=True,
        embedding_model="test",
        embedding_revision="v1",
        embedding_dimension=2,
    )
    store = Store(tmp_path)
    keyword_started, semantic_started = threading.Event(), threading.Event()
    overlap = []

    def search(*args):
        keyword_started.set()
        overlap.append(semantic_started.wait(timeout=1))
        return []

    class QueryEmbedding:
        health = {"state": "ready"}

        async def embed(self, texts, query=False):
            semantic_started.set()
            assert await asyncio.to_thread(keyword_started.wait, 1)
            return [[1, 0]]

    monkeypatch.setattr(store, "search", search)
    await HybridRetriever(store, settings, QueryEmbedding()).retrieve(
        "invoice", "invoice", 12, None, None
    )
    assert overlap == [True]


@pytest.mark.anyio
async def test_cancelling_answer_generation_closes_stream_without_saving(tmp_path):
    settings = config(tmp_path)
    store = Store(tmp_path)
    key = insert(store, "Kitchen discussion")
    closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            event = {
                "choices": [{"delta": {"content": f"Provisional [{key}]"}, "finish_reason": None}]
            }
            yield ("data: " + json.dumps(event) + "\n\n").encode()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    async def emit(event):
        if event["type"] == "delta":
            raise asyncio.CancelledError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=Stream(), headers={"content-type": "text/event-stream"}
            )
        )
    ) as http:
        with pytest.raises(asyncio.CancelledError):
            await Memory(store, settings, Inference(settings, http)).chat(
                "Kitchen", 12, on_event=emit
            )
    assert closed.is_set()
    assert all(
        not store.chat_session(session["id"])["messages"] for session in store.chat_sessions()
    )


@pytest.mark.anyio
async def test_chat_expands_a_missed_paraphrase_without_changing_scope(tmp_path):
    settings = config(tmp_path, llm_routes={"retrieval": {"model": "planner"}})
    store = Store(tmp_path)
    key = insert(store, "Freja reserved the harbour hotel for the workshop.")
    insert(store, "Freja reserved another hotel.", datetime(2026, 10, 14, 12, tzinfo=UTC))
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        content = (
            '{"query":"hotel workshop accommodation"}'
            if body["model"] == "planner"
            else f"Freja [{key}]"
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        memory = Memory(store, settings, Inference(settings, http))
        # Search remains retrieval-only and cannot invoke a planner.
        assert not (await memory.search("overnatning arbejdsseminaret", 12, day=AT.date()))[
            "sources"
        ]
        result = await memory.chat("overnatning arbejdsseminaret", 12, day=AT.date())
        assert [source["id"] for source in result["sources"]] == [key]
        assert result["retrieval_details"]["query_expanded"]
        assert calls == ["planner", "home"]


def test_stream_completes_only_after_citation_and_source_validation(tmp_path):
    settings = config(tmp_path)
    with TestClient(create_app(settings)) as client:
        key = insert(client.app.state.memory.store, "Kitchen discussion")

        def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True
            events = [
                {"choices": [{"delta": {"content": "Recorded "}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": f"answer [{key}]"}, "finish_reason": "stop"}]},
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
                + "data: [DONE]\n\n",
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client.app.state.memory.inference = Inference(settings, http)
        try:
            response = client.post("/v1/chat/stream", json={"question": "Kitchen"})
            assert response.status_code == 200
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[0] == {"type": "status", "stage": "searching"}
            assert (
                "".join(e["text"] for e in events if e["type"] == "delta")
                == f"Recorded answer [{key}]"
            )
            assert events[-1]["type"] == "done"
            result = events[-1]["result"]
            assert result["answer"] == f"Recorded answer [{key}]"
            assert result["timings"]["first_token_ms"] >= 0
            assert result["timings"]["total_ms"] >= result["timings"]["retrieval_ms"]
            assert (
                len(client.get(f"/v1/chat/sessions/{result['session_id']}").json()["messages"]) == 2
            )
        finally:
            asyncio.run(http.aclose())


@pytest.mark.parametrize("failure", ["unknown_citation", "correction", "truncated", "incomplete"])
def test_failed_stream_never_publishes_or_saves_partial_answer(tmp_path, failure):
    settings = config(tmp_path)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.memory.store
        key = insert(store, "Kitchen discussion")

        def handler(request):
            cited = str(uuid4()) if failure == "unknown_citation" else key
            if failure == "correction":
                store.correct_transcript(key, "Corrected kitchen discussion")
            reason = (
                "length" if failure == "truncated" else None if failure == "incomplete" else "stop"
            )
            event = {
                "choices": [{"delta": {"content": f"Answer [{cited}]"}, "finish_reason": reason}]
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client.app.state.memory.inference = Inference(settings, http)
        try:
            response = client.post("/v1/chat/stream", json={"question": "Kitchen"})
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["type"] == "error"
            assert all(e["type"] != "done" for e in events)
            sessions = client.get("/v1/chat/sessions").json()["items"]
            assert all(
                not client.get(f"/v1/chat/sessions/{s['id']}").json()["messages"] for s in sessions
            )
        finally:
            asyncio.run(http.aclose())
