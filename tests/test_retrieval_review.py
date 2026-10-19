"""Regression tests for retrieval review findings; never contact real inference."""

import asyncio
import json
import logging
import runpy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from omi_local.embeddings import EmbeddingError, Embeddings, index_one, run_embedding_worker
from omi_local.inference import Inference
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config(tmp_path):
    return Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)


def insert(store, text, at=AT, *, device="omi", source_kind=""):
    key = uuid4()
    store.ingest(key, device, at, b"audio", 30)
    store.complete(str(key), text)
    if source_kind:
        with store.connect() as db:
            db.execute("UPDATE audio SET source_kind=? WHERE id=?", (source_kind, str(key)))
    return str(key)


@pytest.mark.anyio
async def test_broad_question_reduces_full_selected_transcript_including_tail(config):
    store = Store(config.data_dir)
    tail = "Søren committed to paying the invoice on Friday."
    text = "We discussed the weather by the harbour. " * 700 + tail
    key = insert(store, text)
    supplied = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        supplied.append(payload["evidence"])
        content = f"{tail} [{key}]" if tail in payload["evidence"] else f"Weather notes [{key}]"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        memory = Memory(store, config, Inference(config, http))
        result = await memory.chat("Summarize this day", 12, day=date(2026, 10, 15))
    assert tail in result["answer"]
    assert any(tail in evidence for evidence in supplied)
    assert len(supplied) > 2  # Initial map batches and final reduction/chat request.
    assert result["sources"][0]["text"] == text
    assert "matches" not in result["sources"][0]
    details = result["retrieval_details"]
    assert details["evidence_mode"] == "full_selected_transcripts"
    assert details["evidence_character_budget"] is None and not details["evidence_truncated"]
    assert details["embedding"]["state"] == "disabled"


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "query,opening,tail",
    [
        ("bill", "The billboard is enormous. ", "Søren paid the bill on Friday."),
        ("Arhus", "A harbour story. ", "Søren paid the Århus invoice on Friday."),
    ],
)
async def test_lexical_excerpt_uses_actual_unicode61_token_match(
    config,
    query,
    opening,
    tail,
    enabled,
):
    if enabled:
        config = config.model_copy(
            update={
                "embedding_enabled": True,
                "embedding_model": "fictional",
                "embedding_revision": "test1",
                "embedding_dimension": 2,
            }
        )
    store = Store(config.data_dir)
    text = opening + "Weather discussion. " * 200 + tail
    insert(store, text)
    memory = Memory(store, config, None)

    class ControlledEmbedding:
        settings = config
        health = {"state": "ready"}

        async def embed(self, texts, *, query=False):
            return [[1, 0] for _ in texts]

    if enabled:
        memory.retriever.embeddings = ControlledEmbedding()
        while await index_one(store, memory.retriever.embeddings, memory.retriever.set_id):
            pass
    result = await memory.search(query, 12)
    assert result["retrieval"] == ("hybrid" if enabled else "keyword")
    source = result["sources"][0]
    match = source["matches"][0]
    assert tail in match["text"] and opening not in match["text"]
    assert match["text"] == text[match["start_offset"] : match["end_offset"]]
    assert len(match["text"]) <= 1300


@pytest.mark.anyio
async def test_unrelated_correction_during_query_embedding_filters_prior_assistant(config):
    config = config.model_copy(
        update={
            "embedding_enabled": True,
            "embedding_model": "fictional",
            "embedding_revision": "test1",
            "embedding_dimension": 2,
        }
    )
    store = Store(config.data_dir)

    class Answers:
        histories = []

        async def chat(self, question, sources, history):
            self.histories.append(history)
            return f"Answer [{sources[0]['id']}]"

    answers = Answers()
    memory = Memory(store, config, answers)
    invoice = insert(store, "Invoice responsibility is with Marta.")
    contract = insert(store, "Contract signing is due Friday.", AT + timedelta(minutes=3))

    class CorrectingEmbedding:
        health = {"state": "ready"}

        async def embed(self, texts, query=False):
            if "contract" in texts[0]:
                store.correct_transcript(invoice, "Invoice responsibility changed to Søren.")
            raise EmbeddingError()  # Keyword fallback remains usable.

    memory.retriever.embeddings = CorrectingEmbedding()
    first = await memory.chat("invoice", 12)
    assert first["sources"][0]["id"] == invoice
    second = await memory.chat("contract", 12, session_id=first["session_id"])
    assert [source["id"] for source in second["sources"]] == [contract]
    assert all(message["role"] != "assistant" for message in answers.histories[-1])
    assert second["retrieval_details"]["fallback_reason"] == "embedding_unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
@pytest.mark.parametrize("failure", ["outage", "partial", "wrong_mode"])
async def test_live_evaluation_refuses_degraded_query_results(monkeypatch, mode, failure):
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/evaluate_retrieval.py"))
    for field, value in {"MODEL": "fictional", "REVISION": "test1", "DIMENSION": "2"}.items():
        monkeypatch.setenv("OMI_EMBEDDING_" + field, value)

    async def controlled_embed(self, texts, *, query=False):
        if query and failure == "outage":
            self.health = {"state": "unavailable"}
            raise EmbeddingError()
        self.health = {"state": "ready"}
        return [[1, 0] for _ in texts]

    monkeypatch.setattr(Embeddings, "embed", controlled_embed)
    original = Memory.search

    async def degraded_search(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if failure == "partial":
            result["retrieval_details"]["partial"] = True
        elif failure == "wrong_mode":
            result["retrieval"] = "keyword"
        return result

    monkeypatch.setattr(Memory, "search", degraded_search)
    fixture = {
        "documents": [{"id": "s1", "day": "2026-10-15", "text": "Invoice payment"}],
        "queries": [
            {
                "id": "q1",
                "question": "invoice",
                "split": "heldout",
                "category": "test",
                "relevance": {"s1": 3},
            }
        ],
    }
    with pytest.raises(RuntimeError, match="degraded retrieval; no quality result produced"):
        await module["evaluate"](fixture, True, [mode])


@pytest.mark.anyio
async def test_neighbor_context_preserves_split_commitment_and_ranked_slots(config):
    store = Store(config.data_dir)
    hit = insert(store, "Who can pay the invoice?")
    neighbor = insert(store, "Søren will handle it.", AT + timedelta(seconds=30))
    with store.connect() as db:
        db.execute(
            "UPDATE audio SET source_metadata=? WHERE id IN (?,?)",
            (json.dumps({"recording_id": str(uuid4())}), hit, neighbor),
        )
    memory = Memory(store, config, None)
    result = await memory.search("invoice", 3)
    sources = result["sources"]
    assert [source["id"] for source in sources] == [hit, neighbor]
    assert sources[1]["context_for"] == hit
    assert sources[1]["transcript_version"] == store.get_audio(neighbor)["transcript_version"]
    assert "Søren will handle it" in sources[1]["matches"][0]["text"]
    assert result["retrieval_details"]["context_sources"] == [neighbor]
    limited = await memory.search("invoice", 1)
    assert [source["id"] for source in limited["sources"]] == [hit]
    other_hit = insert(store, "Another invoice due", AT + timedelta(minutes=4))
    ranked = await memory.search("invoice", 2)
    assert {source["id"] for source in ranked["sources"]} == {hit, other_hit}


@pytest.mark.anyio
async def test_neighbor_proximity_and_pronouns_do_not_prove_same_conversation(config):
    store = Store(config.data_dir)
    hit = insert(store, "Who can pay the invoice?")
    insert(store, "Søren will handle it.", AT + timedelta(seconds=30))
    result = await Memory(store, config, None).search("invoice", 3)
    assert [source["id"] for source in result["sources"]] == [hit]
    assert result["retrieval_details"]["context_sources"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("exclusion", ["scope", "gap", "device", "import", "pending", "silence"])
async def test_neighbor_context_excludes_unsafe_or_unrelated_sources(config, exclusion):
    store = Store(config.data_dir)
    at = AT if exclusion != "scope" else datetime(2026, 10, 15, 23, 59, 30, tzinfo=UTC)
    hit = insert(store, "Who can pay the invoice?", at)
    neighbor = insert(
        store,
        "Søren will handle it.",
        at + timedelta(seconds=30),
        device="other" if exclusion == "device" else "omi",
        source_kind="omi_api" if exclusion == "import" else "",
    )
    if exclusion == "gap":
        store.append_gap(
            {
                "gap_id": str(uuid4()),
                "device_id": "omi",
                "started_at": (at + timedelta(seconds=29)).isoformat(),
                "ended_at": (at + timedelta(seconds=31)).isoformat(),
                "reason": "lost",
            }
        )
    elif exclusion in {"pending", "silence"}:
        with store.connect() as db:
            db.execute(
                "UPDATE audio SET state=?,text=? WHERE id=?",
                ("queued" if exclusion == "pending" else "done", "", neighbor),
            )
    result = await Memory(store, config, None).search("invoice", 12, day=date(2026, 10, 15))
    assert [source["id"] for source in result["sources"]] == [hit]


@pytest.mark.anyio
async def test_index_worker_warning_contains_only_exception_type(config, caplog, monkeypatch):
    config = config.model_copy(
        update={
            "embedding_enabled": True,
            "embedding_model": "fictional",
            "embedding_revision": "test1",
            "embedding_dimension": 2,
        }
    )
    store = Store(config.data_dir)
    memory = Memory(store, config, None)
    insert(store, "Sensitive source")

    class BrokenEmbedding:
        settings = config

        async def embed(self, texts):
            raise RuntimeError("SECRET transcript https://secret.local token")

    with caplog.at_level(logging.WARNING, logger="omi_local.embeddings"):
        assert await index_one(store, BrokenEmbedding(), memory.retriever.set_id)
    assert "RuntimeError" in caplog.text and "SECRET" not in caplog.text
    assert "Sensitive source" not in caplog.text and "secret.local" not in caplog.text
    import omi_local.embeddings as indexing

    async def broken_index(*args):
        raise OSError("SECRET private archive")

    async def stop_after_one_iteration(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(indexing, "index_one", broken_index)
    monkeypatch.setattr(indexing.asyncio, "sleep", stop_after_one_iteration)
    with pytest.raises(asyncio.CancelledError):
        await run_embedding_worker(store, BrokenEmbedding(), memory.retriever.set_id)
    assert "OSError" in caplog.text and "SECRET" not in caplog.text
