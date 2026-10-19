"""Evaluation and lifecycle checks; all HTTP responses and vectors are controlled."""

import asyncio
import json
import runpy
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.embeddings import EmbeddingError, Embeddings, index_one
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config(tmp_path):
    return Settings(
        data_dir=tmp_path,
        timezone="UTC",
        worker_enabled=False,
        embedding_enabled=True,
        embedding_model="fictional",
        embedding_revision="test1",
        embedding_dimension=2,
        _env_file=None,
    )


def handler(request):
    inputs = json.loads(request.content)["input"]
    return httpx.Response(
        200, json={"data": [{"index": index, "embedding": [1, 0]} for index in range(len(inputs))]}
    )


def test_fictional_evaluation_labels_and_metric_formula():
    fixture = json.loads((Path(__file__).parent / "fixtures/retrieval_eval.json").read_text())
    assert len(fixture["documents"]) == 24 and len(fixture["queries"]) == 36
    ids = {row["id"]: row for row in fixture["documents"]}
    for query in fixture["queries"]:
        assert set(query["relevance"]) <= ids.keys()
        assert all(ids[key]["split"] == query["split"] for key in query["relevance"])
    functions = runpy.run_path(str(Path(__file__).parents[1] / "scripts/evaluate_retrieval.py"))
    metrics = functions["metrics"]
    assert metrics([], {}) == {"negative_rejected": 1}
    assert metrics(["wrong"], {}) == {"negative_rejected": 0}
    measured = metrics(["wrong", "correct"], {"correct": 3})
    assert measured["recall_at_5"] == 1
    assert measured["mrr_at_10"] == 0.5
    assert measured["ndcg_at_10"] == pytest.approx(0.6309297536)
    assert metrics(["a"], {"a": 3, "b": 2})["required_context_at_12"] == 0


@pytest.mark.anyio
async def test_evaluation_modes_are_independent(config):
    store = Store(config.data_dir)
    memory = Memory(store, config, None)
    key = uuid4()
    store.ingest(key, "fictional", AT, b"audio", 30)
    store.complete(str(key), "Helt andre ord om et emne")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        memory.retriever.embeddings = Embeddings(config, http)
        while await index_one(store, memory.retriever.embeddings, memory.retriever.set_id):
            pass
        keyword = await memory.search("invoice", 12, retrieval_mode="keyword")
        semantic = await memory.search("invoice", 12, retrieval_mode="semantic")
        hybrid = await memory.search("invoice", 12, retrieval_mode="hybrid")
    assert keyword["sources"] == []
    assert semantic["sources"][0]["id"] == str(key)
    assert hybrid["sources"][0]["id"] == str(key)
    assert semantic["retrieval"] == "semantic" and hybrid["retrieval"] == "hybrid"


def test_real_import_route_enqueues_current_version_without_stt(config):
    config = config.model_copy(update={"omi_api_key": "fictional-local-test-key"})
    # SecretStr must remain a SecretStr when copying validated configuration.
    from pydantic import SecretStr

    config.omi_api_key = SecretStr("fictional-local-test-key")
    with TestClient(create_app(config)) as client:
        remote = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=[
                        {
                            "id": "fictional-import",
                            "started_at": AT.isoformat(),
                            "finished_at": (AT + timedelta(minutes=5)).isoformat(),
                            "transcript_segments": [
                                {
                                    "start": 0,
                                    "end": 30,
                                    "text": "Søren betaler fakturaen",
                                    "speaker_name": "Ada",
                                }
                            ],
                        }
                    ],
                )
            )
        )
        try:
            client.app.state.omi.client = remote
            response = client.post("/v1/integrations/omi/import", json={})
            assert response.status_code == 200, response.text
            source = response.json()["items"][0]
            assert source["state"] == "done" and source["transcript_version"]
            with client.app.state.memory.store.connect() as db:
                job = dict(db.execute("SELECT * FROM embedding_jobs").fetchone())
                assert job["audio_id"] == source["id"]
                assert job["version"] == source["transcript_version"]
                assert job["state"] == "pending"
        finally:
            asyncio.run(remote.aclose())


def test_hybrid_search_meeting_scope_and_saved_range_survive_restart(config):
    with TestClient(create_app(config)) as client:
        store = client.app.state.memory.store
        expected = str(uuid4())
        for key, at in ((expected, AT), (str(uuid4()), AT - timedelta(days=1))):
            from uuid import UUID

            store.ingest(UUID(key), "omi", at, b"audio", 30)
            store.complete(key, "Helt andre ord")
        event_id = str(uuid4())
        for key, at in ((event_id, AT), (str(uuid4()), AT + timedelta(minutes=2))):
            assert (
                client.post(
                    "/v1/events",
                    json={
                        "event_id": key,
                        "device_id": "omi",
                        "occurred_at": stamp(at),
                        "action": "meeting_toggle",
                    },
                ).status_code
                == 200
            )
        remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = Embeddings(config, remote)
            client.app.state.memory.retriever.embeddings = adapter

            async def backfill():
                while await index_one(store, adapter, client.app.state.memory.retriever.set_id):
                    pass

            asyncio.run(backfill())
            result = client.post(
                "/v1/search",
                json={
                    "question": "billing",
                    "meeting_id": event_id,
                },
            )
            assert result.status_code == 200
            assert [source["id"] for source in result.json()["sources"]] == [expected]
            assert result.json()["retrieval"] == "hybrid"
            session = store.ensure_session(
                None, "range", start_day="2026-10-15", end_day="2026-10-15"
            )
        finally:
            asyncio.run(remote.aclose())
    with TestClient(create_app(config)) as client:
        remote = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            client.app.state.memory.retriever.embeddings = Embeddings(config, remote)
            result = client.post(
                "/v1/search",
                json={
                    "question": "billing 2026-10-14",
                    "session_id": session["id"],
                },
            )
            assert [source["id"] for source in result.json()["sources"]] == [expected]
            assert result.json()["retrieval_details"]["embedding"]["indexed"] == 1
        finally:
            asyncio.run(remote.aclose())


@pytest.mark.anyio
async def test_embedding_input_ceiling_and_total_stream_timeout(config):
    calls = []

    def record(request):
        calls.append(request)
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        adapter = Embeddings(config, http)
        with pytest.raises(EmbeddingError):
            await adapter.embed(["x" * (config.embedding_max_input_chars + 1)])
        assert calls == []  # Reject locally, never truncate.

    class SlowResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            yield b"{}"

    config = config.model_copy(update={"embedding_timeout_seconds": 0.01})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowResponse()))
    ) as http:
        with pytest.raises(EmbeddingError):
            await Embeddings(config, http).embed(["invoice"])


@pytest.mark.anyio
async def test_correction_while_query_is_embedding_never_returns_stale_lexical_source(
    config, monkeypatch
):
    store = Store(config.data_dir)
    memory = Memory(store, config, None)
    key = uuid4()
    store.ingest(key, "omi", AT, b"audio", 30)
    store.complete(str(key), "Invoice old evidence")
    # Force the stale-read ordering explicitly now that lexical and semantic branches
    # overlap. A correction before the read can legitimately return the new evidence.
    lexical_read = threading.Event()
    search = store.search

    def read_before_correction(*args):
        rows = search(*args)
        lexical_read.set()
        return rows

    monkeypatch.setattr(store, "search", read_before_correction)

    class CorrectingEmbeddings:
        health = {"state": "ready"}

        async def embed(self, texts, query=False):
            assert await asyncio.to_thread(lexical_read.wait, 1)
            store.correct_transcript(str(key), "Invoice new evidence")
            return [[1, 0]]

    memory.retriever.embeddings = CorrectingEmbeddings()
    from omi_local.domain import EvidenceChanged

    with pytest.raises(EvidenceChanged):
        await memory.search("invoice", 12)


def evaluator():
    return runpy.run_path(str(Path(__file__).parents[1] / "scripts/evaluate_retrieval.py"))


def test_extended_metrics_deduplicate_passages_and_ignore_zero_relevance():
    measured = evaluator()["metrics"](["wrong", "a", "a"], {"a": 3, "negative": 0})
    assert measured["recall_at_10"] == 1
    assert measured["precision_at_5"] == 0.2
    assert measured["retrieval_precision"] == 0.5
    assert measured["evidence_coverage_at_10"] == 1
    assert evaluator()["aggregate"]([])["latency_p95_ms"] is None


def test_exact_evidence_coverage_requires_all_distant_spans():
    coverage = evaluator()["span_coverage"]
    required = [
        {"source": "a", "start_offset": 10, "end_offset": 20},
        {"source": "a", "start_offset": 100, "end_offset": 120},
    ]
    sources = [{"id": "uuid", "matches": [{"start_offset": 0, "end_offset": 50}]}]
    assert coverage(sources, required, {"uuid": "a"}) == {
        "passage_evidence_coverage": 0.5,
        "all_passage_evidence": 0,
    }
    sources[0]["matches"] += [
        {"start_offset": 90, "end_offset": 110},
        {"start_offset": 110, "end_offset": 130},
    ]
    assert coverage(sources, required, {"uuid": "a"})["all_passage_evidence"] == 1


def test_conversation_fixture_has_valid_sections_spans_and_heldout_labels():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/retrieval_eval_conversations.json").read_text()
    )
    documents = {row["id"]: row for row in fixture["documents"]}
    for query in fixture["queries"]:
        for key in query["relevance"]:
            assert documents[key]["split"] == query["split"]
        for span in query.get("required_spans", []):
            assert span["source"] in query["relevance"]
            text = documents[span["source"]]["text"]
            assert 0 <= span["start_offset"] < span["end_offset"] <= len(text)
    for document in documents.values():
        previous = 0
        for section in document["sections"]:
            assert not document["text"][previous : section["start_offset"]].strip()
            assert section["end_offset"] > section["start_offset"]
            previous = section["end_offset"]
        assert previous == len(document["text"])


@pytest.mark.anyio
async def test_evaluator_never_contacts_services_by_default_or_accepts_owner_paths(tmp_path):
    functions = evaluator()
    fixture = {
        "documents": [{"id": "a", "day": "2026-10-01", "text": "Aurora invoice"}],
        "queries": [
            {
                "id": "q",
                "category": "exact",
                "split": "dev",
                "question": "Aurora invoice",
                "relevance": {"a": 3},
            }
        ],
    }

    def unexpected(request):
        pytest.fail("Keyword evaluation must never contact inference")

    report = await functions["evaluate"](
        fixture,
        False,
        ["keyword"],
        transport=httpx.MockTransport(unexpected),
    )
    assert report["live_embeddings"] is False
    assert report["answer_quality_tested"] is False
    assert report["results"]["keyword"]["overall"]["recall_at_10"] == 1
    with pytest.raises(ValueError, match="only configure"):
        functions["evaluation_settings"](str(tmp_path), False, {"data_dir": "/owner/archive"})
    with pytest.raises(ValueError, match="explicit"):
        await functions["evaluate"](fixture, False, ["semantic"])


@pytest.mark.anyio
async def test_mock_endpoint_evaluation_is_labeled_and_checks_coverage():
    functions = evaluator()
    fixture = {
        "documents": [{"id": "a", "day": "2026-10-01", "text": "Aurora invoice"}],
        "queries": [
            {
                "id": "q",
                "category": "exact",
                "split": "dev",
                "question": "Aurora invoice",
                "relevance": {"a": 3},
            }
        ],
    }
    report = await functions["evaluate"](
        fixture,
        True,
        ["keyword", "semantic", "hybrid"],
        {"embedding_model": "fictional", "embedding_revision": "test", "embedding_dimension": 2},
        transport=httpx.MockTransport(handler),
    )
    assert report["run_kind"] == "mock_adapter"
    assert report["live_embeddings"] is False
    assert report["coverage"]["indexed"] == 1
    assert report["indexed_passages"] >= 1
    assert report["indexing_passages_per_second"] > 0


@pytest.mark.anyio
async def test_evaluator_refuses_incomplete_embedding_or_failed_reranking_results():
    fixture = {
        "documents": [{"id": "a", "day": "2026-10-01", "text": "Aurora invoice"}],
        "queries": [
            {
                "id": "q",
                "category": "exact",
                "split": "dev",
                "question": "Aurora invoice",
                "relevance": {"a": 3},
            }
        ],
    }
    overrides = {
        "embedding_model": "fictional",
        "embedding_revision": "test",
        "embedding_dimension": 2,
    }
    with pytest.raises(RuntimeError, match="backfill incomplete"):
        await evaluator()["evaluate"](
            fixture,
            True,
            ["hybrid"],
            overrides,
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )

    def failing_reranker(request):
        return httpx.Response(503) if request.url.path.endswith("/rerank") else handler(request)

    with pytest.raises(RuntimeError, match="Reranking incomplete"):
        await evaluator()["evaluate"](
            fixture,
            True,
            ["hybrid"],
            overrides
            | {
                "reranker_enabled": True,
                "reranker_model": "fictional-reranker",
            },
            transport=httpx.MockTransport(failing_reranker),
        )
