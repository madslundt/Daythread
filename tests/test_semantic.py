"""Controlled vectors test retrieval mechanics, never real bilingual model quality."""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from omi_local.api import create_app
from omi_local.domain import EvidenceChanged
from omi_local.embeddings import EmbeddingError, Embeddings, index_one
from omi_local.inference import Inference
from omi_local.semantic_store import normalized, passages
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
        embedding_base_url="http://embeddings.local/v1",
        embedding_model="fictional-model",
        embedding_revision="test-revision-1",
        embedding_dimension=2,
        _env_file=None,
    )


def insert(store, text="Søren handles the invoice.", at=AT, key=None):
    key = key or str(uuid4())
    store.ingest(UUID(key), "omi", at, b"audio", 30)
    store.complete(key, text)
    return key


class NoAnswer:
    async def chat(self, *args):
        raise AssertionError("Retrieval-only requests must not generate answers")


def vector(text):
    words = ("invoice", "regning", "faktura", "billing", "betaling")
    return [1, 0] if any(word in text.lower() for word in words) else [0, 1]


def transport(request):
    data = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": index, "embedding": vector(text)}
                for index, text in enumerate(data["input"])
            ]
        },
    )


async def indexed_memory(config):
    store = Store(config.data_dir)
    http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    memory = Memory(store, config, NoAnswer())
    memory.retriever.embeddings = Embeddings(config, http)
    return memory, http


async def drain(memory):
    while await index_one(memory.store, memory.retriever.embeddings, memory.retriever.set_id):
        pass


@pytest.mark.anyio
async def test_semantic_paraphrase_scope_before_top_k_and_no_generation(config):
    memory, http = await indexed_memory(config)
    try:
        expected = insert(memory.store, "Søren betaler fakturaen.")
        for _ in range(55):
            insert(memory.store, "Invoice billing invoice.", AT - timedelta(days=2))
        await drain(memory)
        result = await memory.search("Who handles billing?", 1, day=date(2026, 10, 15))
        assert [source["id"] for source in result["sources"]] == [expected]
        assert result["retrieval"] == "hybrid"
        assert result["retrieval_details"]["embedding"]["indexed"] == 1
        assert result["scope"] is not None
        assert memory.store.chat_sessions() == []
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_exact_names_protected_and_deduplicated(config):
    memory, http = await indexed_memory(config)
    try:
        expected = insert(memory.store, "Søren Møller owns project Århus.")
        insert(memory.store, "Marta handles invoices. " * 400)
        await drain(memory)
        result = await memory.search("Søren Møller invoice", 3)
        ids = [source["id"] for source in result["sources"]]
        assert ids[0] == expected
        assert len(ids) == len(set(ids)) == 2
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_long_import_tail_is_indexed_and_excerpt_does_not_replace_source(config):
    memory, http = await indexed_memory(config)
    try:
        text = "Weather report. " * 12498 + "Søren betaler fakturaen i morgen."
        key = str(uuid4())
        # Imported transcripts write directly, bypassing Store.complete.
        with memory.store.connect() as db:
            db.execute(
                "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text,"
                "audio_available,source_kind) VALUES(?,?,?,?,?,?,'done',?,0,'omi_api')",
                (
                    key,
                    "omi-api",
                    AT.isoformat(),
                    (AT + timedelta(days=2)).isoformat(),
                    "hash",
                    "unused",
                    text,
                ),
            )
        await drain(memory)
        result = await memory.search("Who handles billing?", 1)
        source = result["sources"][0]
        assert source["id"] == key and source["text"] == text
        assert "faktura" in source["matches"][0]["text"]
        assert source["matches"][0]["start_offset"] > 190000
        assert source["matches"][0]["timing"] == "chunk"
        assert result["retrieval_details"]["evidence_truncated"]
        with memory.store.connect() as db:
            memory.store.verify_sources(db, [source])
            bad = source | {"text": source["matches"][0]["text"]}
            with pytest.raises(EvidenceChanged):
                memory.store.verify_sources(db, [bad])
        assert not source["audio_available"]
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_outage_and_partial_index_keep_keyword_search(config):
    memory, http = await indexed_memory(config)
    try:
        expected = insert(memory.store)
        result = await memory.search("invoice", 5)
        assert result["sources"][0]["id"] == expected
        assert result["retrieval_details"]["partial"]

        async def unavailable(*args, **kwargs):
            raise EmbeddingError()

        memory.retriever.embeddings.embed = unavailable
        result = await memory.search("invoice", 5)
        assert result["sources"][0]["id"] == expected
        assert result["retrieval_details"]["fallback_reason"] == "embedding_unavailable"
    finally:
        await http.aclose()


@pytest.mark.parametrize("mutation", ["correction", "delete", "expiry", "recreate"])
def test_stale_index_write_cannot_restore_corrected_or_deleted_text(config, mutation):
    store = Store(config.data_dir)
    set_id = store.configure_embeddings(config.embedding_manifest)
    key = insert(store)
    job = store.claim_embedding_batch(set_id, 1000)
    old_version = job["version"]
    if mutation == "correction":
        store.correct_transcript(key, "Søren declined the task.")
    elif mutation == "delete":
        store.delete_audio(key)
    elif mutation == "expiry":
        store.apply_retention(0, 1, now=AT + timedelta(days=2))
    else:
        store.delete_audio(key)
        insert(store, "Different new recording", key=key)
    assert not store.finish_embedding_batch(job, [[1, 0]] * len(job["passages"]))
    assert store.semantic_candidates(set_id, [1, 0], 0.5, 50, None, None) == []
    row = store.get_audio(key)
    assert row is None or row["transcript_version"] != old_version


def test_model_revision_dimensions_and_restart_are_isolated(config):
    store = Store(config.data_dir)
    first = store.configure_embeddings(config.embedding_manifest)
    insert(store)
    job = store.claim_embedding_batch(first, 1000)
    assert job
    restarted = Store(config.data_dir)
    assert restarted.configure_embeddings(config.embedding_manifest) == first
    recovered = restarted.claim_embedding_batch(first, 1000)
    assert recovered
    revised = config.model_copy(
        update={"embedding_revision": "revision-2", "embedding_dimension": 3}
    )
    second = restarted.configure_embeddings(revised.embedding_manifest)
    assert second != first
    assert not restarted.finish_embedding_batch(recovered, [[1, 0]])
    current = restarted.claim_embedding_batch(second, 1000)
    assert restarted.finish_embedding_batch(current, [[1, 0, 0]])
    assert restarted.semantic_candidates(first, [1, 0], 0.5, 50, None, None) == []
    assert len(restarted.semantic_candidates(second, [1, 0, 0], 0.5, 50, None, None)) == 1


def test_audio_expiry_preserves_vectors_and_snapshot(config, tmp_path):
    store = Store(config.data_dir)
    set_id = store.configure_embeddings(config.embedding_manifest)
    key = insert(store)
    job = store.claim_embedding_batch(set_id, 1000)
    store.finish_embedding_batch(job, [[1, 0]])
    store.apply_retention(1, 0, now=AT + timedelta(days=2))
    assert not store.get_audio(key)["audio_available"]
    assert len(store.semantic_candidates(set_id, [1, 0], 0.5, 50, None, None)) == 1
    archive = store.export_archive()
    import sqlite3
    import zipfile

    try:
        with zipfile.ZipFile(archive) as backup:
            backup.extract("omi.sqlite3", tmp_path / "snapshot")
        with sqlite3.connect(tmp_path / "snapshot" / "omi.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM retrieval_passages").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM embedding_sets").fetchone()[0] == 1
    finally:
        import shutil

        shutil.rmtree(archive.parent)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{"index": 0, "embedding": [0, 0]}]},
        {"data": [{"index": 0, "embedding": [1]}]},
        {"data": [{"index": 0, "embedding": [True, 1]}]},
        {"data": [{"index": 1, "embedding": [1, 0]}]},
        {"data": [{"index": "0", "embedding": [1, 0]}]},
        {"data": [{"index": 0, "embedding": ["1", 0]}]},
    ],
)
async def test_malformed_embedding_response_is_unavailable(config, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        adapter = Embeddings(config, http)
        with pytest.raises(EmbeddingError):
            await adapter.embed(["invoice"])
        assert adapter.health["state"] == "unavailable"


@pytest.mark.anyio
async def test_adapter_reorders_validates_prefixes_and_does_not_follow_redirects(config):
    config = config.model_copy(update={"embedding_query_prefix": "query: "})
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content)["input"] == ["query: invoice", "query: Søren"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 5]},
                    {"index": 0, "embedding": [4, 0]},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter = Embeddings(config, http)
        assert await adapter.embed(["invoice", "Søren"], query=True) == [[1, 0], [0, 1]]
        assert adapter.health["state"] == "ready"
        assert seen[0].extensions["timeout"]["read"] == config.embedding_timeout_seconds
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://example.com/embeddings"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True
    ) as http:
        with pytest.raises(EmbeddingError):
            await Embeddings(config, http).embed(["invoice"])
    assert len(calls) == 1


@pytest.mark.anyio
async def test_timeout_and_failed_index_retry_are_durable(config):
    store = Store(config.data_dir)
    set_id = store.configure_embeddings(config.embedding_manifest)
    insert(store)

    def timeout(request):
        raise httpx.ReadTimeout("no model", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
        adapter = Embeddings(config, http)
        assert await index_one(store, adapter, set_id)
        assert adapter.health["state"] == "unavailable"
    with store.connect() as db:
        job = dict(db.execute("SELECT * FROM embedding_jobs").fetchone())
        assert job["state"] == "pending" and job["attempts"] == 1 and job["retry_at"]
    assert Store(config.data_dir).configure_embeddings(config.embedding_manifest) == set_id


def test_embedding_config_is_explicit_local_and_disabled_by_default(tmp_path):
    assert not Settings(data_dir=tmp_path, _env_file=None).embedding_enabled
    for changes in (
        {"embedding_enabled": True},
        {"embedding_base_url": "http://example.com/v1"},
        {"embedding_base_url": "http://user:secret@localhost/v1"},
    ):
        with pytest.raises(ValidationError):
            Settings(data_dir=tmp_path, _env_file=None, **changes)
    for values in ([float("nan"), 0], [float("inf"), 0], [0, 0]):
        with pytest.raises(ValueError):
            normalized(values, 2)


def test_passages_cover_all_characters_with_bounded_overlap():
    text = "A long Danish sentence. Æøå English mixed language. " * 4000
    ranges = passages(text, 1000)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert all(0 < end - start <= 1000 for start, end in ranges)
    assert all(
        left[1] >= right[0] and left[0] < right[0] for left, right in zip(ranges, ranges[1:])
    )


def test_search_api_has_no_side_effect_and_preserves_saved_scope(tmp_path):
    config = Settings(data_dir=tmp_path, worker_enabled=False, timezone="UTC", _env_file=None)
    with TestClient(create_app(config)) as client:
        memory = client.app.state.memory
        key = insert(memory.store)
        insert(memory.store, "Søren yesterday", at=AT - timedelta(days=1))
        response = client.post("/v1/search", json={"question": "Søren", "day": "2026-10-15"})
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["sources"]] == [key]
        assert client.get("/v1/chat/sessions").json()["items"] == []
        session = memory.store.ensure_session(None, "Saved", day="2026-10-15")
        response = client.post(
            "/v1/search", json={"question": "Søren 2026-10-14", "session_id": session["id"]}
        )
        assert [row["id"] for row in response.json()["sources"]] == [key]
        assert memory.store.chat_session(session["id"])["messages"] == []
        assert (
            client.post(
                "/v1/search",
                json={"question": "Søren", "session_id": session["id"], "day": "2026-10-14"},
            ).status_code
            == 409
        )
        assert client.get("/v1/status").json()["embeddings"]["state"] == "disabled"


@pytest.mark.anyio
async def test_correction_marks_history_stale_and_filters_model_context(config):
    memory, http = await indexed_memory(config)

    class Answer:
        history = None

        async def chat(self, question, sources, history):
            self.history = history
            return f"Answer [{sources[0]['id']}]"

    memory.inference = Answer()
    try:
        key = insert(memory.store)
        first = await memory.chat("invoice", 12)
        memory.store.correct_transcript(key, "New invoice evidence")
        session = memory.store.chat_session(first["session_id"])
        assert session["messages"][-1]["stale"]
        assert session["messages"][-1]["retrieval_details"] == first["retrieval_details"]
        await memory.chat("invoice", 12, session_id=first["session_id"])
        assert all(row["role"] != "assistant" for row in memory.inference.history)
    finally:
        await http.aclose()


@pytest.mark.anyio
async def test_generation_receives_bounded_excerpt_but_save_verifies_full_source(config):
    memory, http = await indexed_memory(config)
    try:
        text = "Weather. " * 1000 + "Søren handles the invoice."
        key = insert(memory.store, text)
        result = await memory.search("invoice", 12)

        def handler(request):
            payload = json.loads(json.loads(request.content)["messages"][1]["content"])
            evidence = json.loads(payload["evidence"])
            assert len(evidence[0]["text"]) < 1500
            assert "invoice" in evidence[0]["text"]
            return httpx.Response(
                200, json={"choices": [{"message": {"content": f"Answer [{key}]"}}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as model_http:
            await Inference(config, model_http).chat("invoice", result["sources"])
        assert result["sources"][0]["text"] == text
    finally:
        await http.aclose()
