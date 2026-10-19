"""Deterministic passage/reranker contract tests; no real model quality claims."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from omi_local.hybrid import (
    HybridRetriever,
    bound_sources,
    passage_excerpt,
    relevant_excerpts,
    verified_context,
)
from omi_local.reranking import Reranker, RerankingError
from omi_local.settings import Settings
from omi_local.store import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config(tmp_path):
    return Settings(
        data_dir=tmp_path, worker_enabled=False, embedding_enabled=True,
        embedding_model="controlled", embedding_revision="test", embedding_dimension=2,
        _env_file=None,
    )


def insert(store, text):
    key = uuid4()
    store.ingest(key, "omi", datetime(2026, 10, 15, 12, tzinfo=UTC), b"audio", 30)
    store.complete(str(key), text)
    return store.get_audio(str(key))


class ControlledEmbeddings:
    health = {"state": "ready"}

    async def embed(self, texts, *, query=False):
        return [[1, 0]]


def passage(row, start, end, ordinal=0, **metadata):
    return {
        "id": f"{row['id']}:{ordinal}", "audio_id": row["id"],
        "version": row["transcript_version"], "ordinal": ordinal,
        "start_offset": start, "end_offset": end, "metadata": metadata, "score": 0.9,
    }


@pytest.mark.anyio
async def test_multiple_distant_passages_preserve_source_text_and_versions(config, monkeypatch):
    store = Store(config.data_dir)
    text = "First promise. " + "Weather. " * 500 + "Second promise."
    row = insert(store, text)
    retriever = HybridRetriever(store, config, ControlledEmbeddings())
    spans = [passage(row, 0, 14), passage(row, len(text) - 15, len(text), 1)]
    monkeypatch.setattr(store, "semantic_candidates", lambda *args: spans)
    sources, details = await retriever.retrieve("commitments", "commitments", 5, None, None)
    assert len(sources) == 1 and len(sources[0]["matches"]) == 2
    assert sources[0]["text"] == text
    assert sources[0]["matches"][0]["text"].startswith("First promise")
    assert "Second promise" in sources[0]["matches"][1]["text"]
    assert all(m["transcript_version"] == row["transcript_version"]
               for m in sources[0]["matches"])
    assert details["mode"] == "hybrid"
    with store.connect() as db:
        store.verify_sources(db, sources)


@pytest.mark.anyio
async def test_repeated_passages_do_not_inflate_recording_rank(config, monkeypatch):
    store = Store(config.data_dir)
    first = insert(store, "One topic. " * 200)
    repeated = insert(store, "Another topic. " * 200)
    retriever = HybridRetriever(store, config, ControlledEmbeddings())
    spans = [passage(first, 0, 100)] + [
        passage(repeated, offset, offset + 100, index)
        for index, offset in enumerate(range(0, 2000, 400))
    ]
    monkeypatch.setattr(store, "semantic_candidates", lambda *args: spans)
    sources, _ = await retriever.retrieve("recall", "recall", 2, None, None, "semantic")
    assert [row["id"] for row in sources] == [first["id"], repeated["id"]]
    assert len(sources[1]["matches"]) == config.retrieval_matches_per_source


@pytest.mark.anyio
async def test_stale_passages_never_become_evidence(config, monkeypatch):
    store = Store(config.data_dir)
    old = insert(store, "Old decision")
    retriever = HybridRetriever(store, config, ControlledEmbeddings())
    store.correct_transcript(old["id"], "New decision")
    monkeypatch.setattr(store, "semantic_candidates", lambda *args: [passage(old, 0, 10)])
    sources, _ = await retriever.retrieve("recall", "recall", 2, None, None, "semantic")
    assert sources == []


def test_context_expansion_respects_validated_topic_bounds():
    row = {
        "text": "PRIVATE A. A decision and its reason. PRIVATE B.",
        "started_at": "2026-10-15T12:00:00+00:00", "ended_at": "2026-10-15T12:00:30+00:00",
    }
    start, end = 11, 36
    row["topic_details"] = json.dumps({"sections": [{
        "start_offset": start, "end_offset": end, "title": "Decision",
    }]})
    item = {
        "id": "passage", "version": "version", "start_offset": 13, "end_offset": 25,
        "ordinal": 0,
        "metadata": {"topic_start_offset": 0, "topic_end_offset": len(row["text"]),
                     "chunking_strategy": "topic-token-v1"},
    }
    match = passage_excerpt(row, item, "decision")
    assert match["text"] == row["text"][start:end]
    assert (match["start_offset"], match["end_offset"]) == (start, end)
    assert match["timing"] == "chunk"
    assert passage_excerpt(row, item | {"end_offset": 100}, "") is None


def test_precise_passage_timing_is_refreshed_without_claiming_expanded_context_precision():
    text = "Before. I will pay. After."
    start = text.index("I will pay.")
    end = start + len("I will pay.")
    row = {
        "id": "source", "text": text, "transcript_version": "v1", "source_version": "v2",
        "started_at": "2026-10-15T12:00:00+00:00", "ended_at": "2026-10-15T12:00:30+00:00",
        "segments": json.dumps([
            {"text": "Before.", "start": 0, "end": 2},
            {"text": "I will pay.", "start": 10, "end": 12, "speaker_id": "speaker-1"},
            {"text": "After.", "start": 20, "end": 22},
        ]),
    }
    item = {
        "id": "passage", "version": "v1", "ordinal": 0,
        "start_offset": start, "end_offset": end,
        "metadata": {"timing": "segment", "start_seconds": 1, "end_seconds": 2},
    }
    match = passage_excerpt(row, item, "pay")
    assert match["text"] == text and match["timing"] == "chunk"
    assert (match["passage_start_offset"], match["passage_end_offset"]) == (start, end)
    assert match["metadata"]["timing"] == "segment"
    assert (match["metadata"]["start_seconds"], match["metadata"]["end_seconds"]) == (10, 12)
    assert match["metadata"]["speaker_id"] == "speaker-1"
    assert match["metadata"]["source_version"] == "v2"


def test_lexical_context_respects_topic_boundaries():
    row = {
        "text": "House repairs. Invoice due tomorrow. Sports results.",
        "lexical_offsets": [15],
        "topic_details": json.dumps({"sections": [
            {"start_offset": 0, "end_offset": 15, "title": "Repairs"},
            {"start_offset": 15, "end_offset": 37, "title": "Invoice"},
            {"start_offset": 37, "end_offset": 52, "title": "Sports"},
        ]}),
    }
    # The final boundary must match the authoritative transcript length.
    details = json.loads(row["topic_details"])
    details["sections"][-1]["end_offset"] = len(row["text"])
    row["topic_details"] = json.dumps(details)
    match = relevant_excerpts(row, "invoice", None)[0]
    assert match["text"] == row["text"][15:37]


def test_explicit_grouping_does_not_cross_known_distinct_topics():
    def row(key, at, title):
        return {
            "id": key, "started_at": at, "text": "topic text",
            "source_metadata": '{"recording_id":"same"}',
            "topic_details": json.dumps({"sections": [
                {"start_offset": 0, "end_offset": 10, "title": title},
            ]}),
        }

    a, b = row("a", "1", "Invoice"), row("b", "2", "Sports")
    assert not verified_context(a, b)
    b["topic_details"] = a["topic_details"]
    assert verified_context(a, b)


def test_configured_final_budget_prioritizes_recording_diversity():
    sources = [
        {"id": str(i), "text": "x" * 100,
         "matches": [{"text": text, "start_offset": 0, "end_offset": len(text)}
                     for text in ("a" * 6, "b" * 6)]}
        for i in range(2)
    ]
    rows, truncated = bound_sources(sources, "", 2, character_budget=15)
    assert len(rows) == 2 and truncated
    assert sum(len(m["text"]) for row in rows for m in row["matches"]) == 15
    assert rows[0]["matches"][1]["end_offset"] == 3
    assert rows[1]["matches"][0]["text"] == "a" * 6


def reranker_config(config, **updates):
    return config.model_copy(update={
        "reranker_enabled": True, "reranker_model": "controlled-reranker",
        "reranker_base_url": "http://127.0.0.1:8010/v1", **updates,
    })


@pytest.mark.anyio
async def test_reranker_bounded_inputs_scores_reordered_and_no_source_metadata(config):
    config = reranker_config(config, reranker_max_passage_chars=10)
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(request)
        assert payload == {
            "model": "controlled-reranker", "query": "question",
            "documents": ["transcript", "other text"], "top_n": 2, "return_documents": False,
        }
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.1},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Reranker(config, client).scores(
            "question", ["transcript more private text", "other text"],
        ) == [0.1, 0.8]
    assert seen[0].extensions["timeout"]["read"] == config.reranker_timeout_seconds


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [
    {}, {"results": []}, {"results": [{"index": 0, "relevance_score": True}]},
    {"results": [{"index": "0", "relevance_score": 0.1}]},
    {"results": [{"index": 1, "relevance_score": 0.1}]},
    {"results": [{"index": 0, "relevance_score": float("inf")}]},
    {"results": [{"index": 0, "relevance_score": "0.1"}]},
])
async def test_reranker_rejects_invalid_response(config, payload):
    # json.dumps explicitly permits nonfinite values to test validation of untrusted JSON.
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps(payload)),
    )) as client:
        with pytest.raises(RerankingError):
            await Reranker(reranker_config(config), client).scores("question", ["text"])


@pytest.mark.anyio
async def test_reranker_redirect_is_not_followed(config):
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://example.com/steal"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True,
    ) as client:
        with pytest.raises(RerankingError):
            await Reranker(reranker_config(config), client).scores("question", ["text"])
    assert len(calls) == 1


@pytest.mark.anyio
async def test_reranker_changes_passage_ranks_without_changing_evidence(config, monkeypatch):
    store = Store(config.data_dir)
    first = insert(store, "A proposed idea.")
    second = insert(store, "The chosen idea and the reason for it.")
    config = reranker_config(config)
    retriever = HybridRetriever(store, config, ControlledEmbeddings())
    monkeypatch.setattr(store, "semantic_candidates", lambda *args: [
        passage(first, 0, len(first["text"])), passage(second, 0, len(second["text"])),
    ])
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"results": [
            {"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9},
        ]}),
    )) as client:
        retriever.reranker = Reranker(config, client)
        rows, details = await retriever.retrieve("decision", "decision", 2, None, None)
    assert [row["id"] for row in rows] == [second["id"], first["id"]]
    assert rows[0]["text"] == second["text"]
    assert rows[0]["matches"][0]["passage_id"] == f"{second['id']}:0"
    assert details["reranking"] == "ready" and details["reranked_passages"] == 2
    with store.connect() as db:
        store.verify_sources(db, rows)


@pytest.mark.anyio
@pytest.mark.parametrize("changes,query,documents", [
    ({"reranker_enabled": False}, "question", ["text"]),
    ({"reranker_max_candidates": 1}, "question", ["a", "b"]),
    ({"reranker_max_query_chars": 3}, "question", ["text"]),
    ({}, "question", [" "]),
])
async def test_reranker_invalid_or_disabled_inputs_do_not_send_requests(
    config, changes, query, documents,
):
    def unexpected(request):
        raise AssertionError("Invalid input must not send private text")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(RerankingError):
            await Reranker(reranker_config(config, **changes), client).scores(query, documents)


@pytest.mark.anyio
async def test_reranker_outage_preserves_fusion_and_exact_name_protection(config, monkeypatch):
    store = Store(config.data_dir)
    literal = insert(store, "Søren owns Århus.")
    semantic = insert(store, "A different invoice.")
    config = reranker_config(config)
    retriever = HybridRetriever(store, config, ControlledEmbeddings())
    monkeypatch.setattr(store, "semantic_candidates", lambda *args: [
        passage(semantic, 0, len(semantic["text"])),
    ])

    def timeout(request):
        raise httpx.ReadTimeout("private provider diagnostic", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        retriever.reranker = Reranker(config, client)
        rows, details = await retriever.retrieve(
            "Søren Århus invoice", "Søren Århus invoice", 3, None, None,
        )
    assert rows[0]["id"] == literal["id"]
    assert {r["id"] for r in rows} == {literal["id"], semantic["id"]}
    assert details["reranking"] == "unavailable"
    assert details["fallback_reason"] is None
