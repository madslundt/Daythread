"""Token limits and exact source spans; these are not real-model quality tests."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from omi_local.api import create_app
from omi_local.embedding_chunking import chunk_passages, input_fits, token_count
from omi_local.embeddings import EmbeddingError, Embeddings, index_one
from omi_local.settings import Settings
from omi_local.store import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


def manifest(**changes):
    return dict(chunking_strategy="topic-token-v1", target_tokens=40, max_tokens=64,
                overlap_tokens=8, tokenizer="utf8", max_input_tokens=80,
                special_tokens=8, max_input_chars=200, document_prefix="Passage: ") | changes


def assert_covered(text, spans):
    covered = set()
    for span in spans:
        start, end = span["start_offset"], span["end_offset"]
        assert 0 <= start < end <= len(text)
        covered.update(range(start, end))
    assert {index for index, character in enumerate(text) if not character.isspace()} <= covered


@pytest.mark.parametrize("text", [
    "Peter lovede at ringe. Årsagen var prisen. " * 30,
    "中文。Dansk æøå! 🧠👨‍👩‍👧‍👦 " * 50,
    "x" * 1000,
    "A short thought. Another related one.\nSpeaker: yes!",
])
def test_bounded_deterministic_full_coverage(text):
    contract = manifest()
    spans = chunk_passages(text, contract)
    assert spans == chunk_passages(text, contract)
    assert_covered(text, spans)
    for span in spans:
        content = text[span["start_offset"]:span["end_offset"]]
        assert token_count(content, contract) <= contract["max_tokens"]
        assert input_fits(contract["document_prefix"] + content, contract)


def test_topics_gaps_and_invalid_boundaries():
    text = "Car decision. " * 30 + "Restaurant tip. " * 30
    split = len("Car decision. " * 30)
    topics = [dict(start_offset=0, end_offset=split),
              dict(start_offset=split, end_offset=len(text))]
    spans = chunk_passages(text, manifest(), topics)
    assert_covered(text, spans)
    assert all(span["end_offset"] <= split or span["start_offset"] >= split for span in spans)
    assert {span["topic_id"] for span in spans} == {0, 1}
    gaps = [dict(start_offset=10, end_offset=split - 10)]
    assert_covered(text, chunk_passages(text, manifest(), gaps))
    for broken in ([dict(start_offset=-1, end_offset=40)],
                   [dict(start_offset=True, end_offset=40)], topics[::-1], [None]):
        assert chunk_passages(text, manifest(), broken) == chunk_passages(text, manifest())


def test_complete_sentences_and_whitespace():
    text = "One short thought. " * 8
    spans = chunk_passages(text, manifest(overlap_tokens=0))
    assert all(text[span["start_offset"]:span["end_offset"]].endswith(". ") for span in spans)
    assert chunk_passages(" \n\t", manifest()) == []


def test_local_actual_tokenizer_fingerprint(tmp_path):
    tokenizers = pytest.importorskip("tokenizers")
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel(
        {"[UNK]": 0, "hello": 1}, unk_token="[UNK]"
    ))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    settings = Settings(_env_file=None, embedding_enabled=True, embedding_model="local",
                        embedding_revision="v1", embedding_dimension=2,
                        embedding_tokenizer=str(path), embedding_tokenizer_revision="toy-v1")
    before = settings.embedding_manifest
    assert token_count("hello " * 300, before) == 300
    text = "hello " * 600
    spans = chunk_passages(text, before)
    assert_covered(text, spans)
    assert len(spans) == 2
    for span in spans:
        assert token_count(text[span["start_offset"]:span["end_offset"]], before) <= 512
    path.write_text(path.read_text() + "\n")
    assert settings.embedding_manifest != before


def test_model_specific_instructions_and_contract_identity():
    settings = Settings(_env_file=None, embedding_enabled=True,
                        embedding_model="Qwen/Qwen3-Embedding-0.6B",
                        embedding_revision="pinned", embedding_dimension=1024)
    assert settings.embedding_prefix(query=True).startswith("Instruct: ")
    assert settings.embedding_prefix(query=True).endswith("\nQuery:")
    assert settings.embedding_prefix(query=False) == ""
    e5 = settings.model_copy(update={"embedding_model": "intfloat/multilingual-e5-large"})
    assert e5.embedding_prefix(query=True) == "query: "
    assert e5.embedding_prefix(query=False) == "passage: "
    plain = settings.model_copy(update={"embedding_model": "other-model"})
    assert plain.embedding_prefix(query=True) == ""
    for key, value in (("embedding_max_tokens", 400), ("embedding_overlap_tokens", 50),
                       ("embedding_query_instruction", "Find evidence"),
                       ("embedding_tokenizer_revision", "changed")):
        changed = settings.model_copy(update={key: value})
        assert changed.embedding_manifest != settings.embedding_manifest
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedding_overlap_tokens=320)


@pytest.mark.anyio
async def test_no_truncation_and_strict_model_response():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"model": "wrong-model", "data": [
            {"index": 0, "embedding": [1, 0]}]})

    settings = Settings(_env_file=None, embedding_enabled=True, embedding_model="local",
                        embedding_revision="v1", embedding_dimension=2,
                        embedding_send_dimensions=True, embedding_max_input_tokens=64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        embeddings = Embeddings(settings, client)
        with pytest.raises(EmbeddingError):
            await embeddings.embed(["🧠" * 100])
        assert requests == []
        with pytest.raises(EmbeddingError):
            await embeddings.embed(["Hello"])
        assert requests[0]["dimensions"] == 2
        settings.embedding_revision = "changed"
        with pytest.raises(EmbeddingError):
            await embeddings.embed(["Hello"])
        assert len(requests) == 1


@pytest.mark.anyio
async def test_identical_chunk_content_embedded_once_without_losing_source_offsets(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, worker_enabled=False, embedding_enabled=True,
        embedding_model="local", embedding_revision="v1", embedding_dimension=2,
        embedding_target_tokens=38, embedding_max_tokens=38, embedding_overlap_tokens=0,
    )
    store = Store(tmp_path)
    set_id = store.configure_embeddings(settings.embedding_manifest)
    key = uuid4()
    store.ingest(key, "test", datetime(2026, 10, 19, tzinfo=UTC), b"test-audio", 30)
    text = "Repeated sentence. " * 100
    store.complete(str(key), text)
    inputs = []

    def respond(request):
        batch = json.loads(request.content)["input"]
        inputs.extend(batch)
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [1, 0]} for i in range(len(batch))]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = Embeddings(settings, client)
        while await index_one(store, adapter, set_id):
            pass
    assert len(inputs) == 1
    assert store.embedding_coverage(set_id) == {"total": 1, "indexed": 1}
    with store.connect() as db:
        spans = [dict(row) for row in db.execute(
            "SELECT start_offset,end_offset FROM retrieval_passages ORDER BY ordinal"
        )]
    assert len(spans) == 50
    assert_covered(text, spans)


def test_api_rebuild_preserves_good_vectors_and_disabled_guard(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, worker_enabled=False, embedding_enabled=True,
        embedding_model="local", embedding_revision="v1", embedding_dimension=2,
    )
    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        store, set_id = memory.store, memory.retriever.set_id
        key = uuid4()
        store.ingest(key, "test", datetime(2026, 10, 19, tzinfo=UTC), b"test-audio", 30)
        store.complete(str(key), "An original decision.")
        job = store.claim_embedding_batch(set_id, 1000)
        assert store.finish_embedding_batch(job, [[1, 0]])
        response = client.post("/v1/embeddings/rebuild")
        assert response.status_code == 200
        assert response.json()["indexed"] == 0
        assert store.semantic_candidates(set_id, [1, 0], -1, 5, None, None)
        rebuilt = store.claim_embedding_batch(set_id, 1000)
        assert rebuilt is not None  # explicit rebuild can repair a damaged derived vector
        assert store.finish_embedding_batch(rebuilt, [[0, 1]])
        assert store.embedding_coverage(set_id)["indexed"] == 1
    with TestClient(create_app(settings.model_copy(update={
        "data_dir": tmp_path / "disabled", "embedding_enabled": False,
    }))) as client:
        assert client.post("/v1/embeddings/rebuild").status_code == 409
