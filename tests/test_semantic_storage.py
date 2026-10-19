"""Synthetic storage regressions: no model endpoint or personal archive is used."""

import json
import os
import sqlite3
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omi_local.domain import stamp, utc_now
from omi_local.semantic_metadata import refresh_passage_metadata, segment_metadata
from omi_local.semantic_schema import migrate_semantic_schema
from omi_local.semantic_store import SEMANTIC_SCHEMA
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
MANIFEST = {
    "model": "synthetic", "revision": "test", "dimension": 2,
    "chunking_strategy": "topic-token-v1", "tokenizer": "utf8",
    "target_tokens": 64, "max_tokens": 80, "overlap_tokens": 8,
    "max_input_tokens": 256, "max_input_chars": 1000,
}


@pytest.fixture(params=["sqlite", "postgres"])
def store(tmp_path, request):
    if request.param == "sqlite":
        return Store(tmp_path)
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    schema = "test_semantic_" + uuid4().hex
    archive = Store(tmp_path, database_url=url, database_schema=schema)
    def cleanup():
        with archive.database.pool.connection() as db:
            db.execute(f'DROP SCHEMA "{schema}" CASCADE')
    request.addfinalizer(cleanup)
    return archive


def source(store, text):
    key = uuid4()
    store.ingest(key, "phone", AT, key.bytes, 30)
    store.complete(str(key), text)
    return str(key)


def drain(store, set_id):
    requests = []
    while job := store.claim_embedding_batch(set_id, 1000):
        requests.append(job)
        assert store.finish_embedding_batch(job, [[1, 0]] * len(job["passages"]))
    return requests


def topics(store, key, sections):
    with store.connect() as db:
        db.execute("UPDATE audio SET topic_details=? WHERE id=?", (
            json.dumps({"title": "Synthetic recording", "sections": sections}), key,
        ))


def test_late_topics_rechunk_atomically_and_reject_superseded_work(store):
    set_id = store.configure_embeddings(MANIFEST)
    text = "The project decision needs context. " * 35 + "A different topic concerns heating. " * 35
    key = source(store, text)
    drain(store, set_id)
    with store.connect() as db:
        original = list(db.execute("SELECT id,vector FROM retrieval_passages ORDER BY ordinal"))
    boundary = text.index("A different")
    sections = [
        {"start_offset": 0, "end_offset": boundary},
        {"start_offset": boundary, "end_offset": len(text)},
    ]
    topics(store, key, sections)
    assert store.embedding_coverage(set_id) == {"total": 1, "indexed": 0}
    stale = store.claim_embedding_batch(set_id, 1000)
    assert stale
    # The current complete version stays intact until every replacement vector exists.
    with store.connect() as db:
        assert [tuple(row) for row in db.execute(
            "SELECT id,vector FROM retrieval_passages ORDER BY ordinal"
        )] == [tuple(row) for row in original]
    assert store.semantic_candidates(set_id, [1, 0], -1, 10, None, None)
    topics(store, key, [{"start_offset": 0, "end_offset": len(text)}])
    active = store.claim_embedding_batch(set_id, 1000)
    assert not store.finish_embedding_batch(stale, [[0, 1]] * len(stale["passages"]))
    if active:
        assert store.finish_embedding_batch(active, [[1, 0]] * len(active["passages"]))
    drain(store, set_id)
    topics(store, key, sections)
    drain(store, set_id)
    with store.connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM retrieval_passages ORDER BY ordinal"
        )]
    assert all(not row["start_offset"] < boundary < row["end_offset"] for row in rows)
    assert {row["topic_id"] for row in rows} == {"0", "1"}
    assert all(row["version"] == store.get_audio(key)["transcript_version"] for row in rows)
    assert all(json.loads(row["metadata"])["embedding_revision"] == "test" for row in rows)
    assert all(any(row["start_offset"] <= i < row["end_offset"] for row in rows)
               for i, char in enumerate(text) if not char.isspace())


@pytest.mark.parametrize("sections", [None, "unreliable", [{"start_offset": -1, "end_offset": 50}],
                                     [{"start_offset": 5, "end_offset": 999999}]])
def test_invalid_topics_never_block_fallback_indexing(store, sections):
    set_id = store.configure_embeddings(MANIFEST)
    key = source(store, "An exact original Danish beslutning. " * 10)
    topics(store, key, sections)
    assert drain(store, set_id)
    assert store.embedding_coverage(set_id) == {"total": 1, "indexed": 1}
    matches = store.semantic_candidates(set_id, [1, 0], -1, 10, None, None)
    assert matches and all(row["topic_id"] is None for row in matches)


def test_effectively_unchanged_chunks_reuse_vectors_and_refresh_metadata(store):
    set_id = store.configure_embeddings(MANIFEST)
    text = "Peter approved the budget."
    key = source(store, text)
    drain(store, set_id)
    topics(store, key, [{"title": "Budget", "start_offset": 0, "end_offset": len(text)}])
    assert drain(store, set_id) == []
    result = store.semantic_candidates(set_id, [1, 0], -1, 10, None, None)[0]
    assert result["topic_id"] == "0"
    assert result["metadata"]["topic_end_offset"] == len(text)
    segments = [{"start": 2, "end": 9, "text": text, "speaker_id": "known-speaker"}]
    with store.connect() as db:
        db.execute("UPDATE audio SET segments=? WHERE id=?", (json.dumps(segments), key))
    # A published passage may still be waiting for metadata refresh. Rendering resolves
    # its timing from the current source, never from the stale published JSON.
    refreshed = refresh_passage_metadata(store.get_audio(key), result)
    assert refreshed["timing"] == "segment"
    assert (refreshed["start_seconds"], refreshed["end_seconds"]) == (2, 9)
    assert refreshed["speaker_id"] == "known-speaker"
    assert drain(store, set_id) == []
    updated = store.semantic_candidates(set_id, [1, 0], -1, 10, None, None)[0]
    assert updated["metadata"]["start_seconds"] == 2


def test_segment_alignment_is_prepared_once_per_source(store, monkeypatch):
    import omi_local.semantic_metadata as metadata

    set_id = store.configure_embeddings(MANIFEST)
    sentences = [f"Original segment {i} discusses a project decision." for i in range(10)]
    key = source(store, " ".join(sentences))
    with store.connect() as db:
        db.execute("UPDATE audio SET segments=? WHERE id=?", (json.dumps([
            {"text": value, "start": i * 2, "end": i * 2 + 1}
            for i, value in enumerate(sentences)
        ]), key))
    calls = []
    original = metadata.segment_anchors
    def prepared(row):
        calls.append(row["audio_id"])
        return original(row)
    monkeypatch.setattr(metadata, "segment_anchors", prepared)
    assert drain(store, set_id)
    assert calls == [key]


def test_repeated_segments_have_timing_only_after_complete_ordered_alignment():
    row = {"text": "one two one", "started_at": AT.isoformat(),
           "ended_at": (AT + timedelta(seconds=30)).isoformat(), "segments": json.dumps([
               {"text": "one", "start": 0, "end": 1},
               {"text": "two", "start": 2, "end": 3},
               {"text": "one", "start": 4, "end": 5},
           ])}
    assert segment_metadata(row, {"start_offset": 8, "end_offset": 11}) == {
        "timing": "segment", "start_seconds": 4, "end_seconds": 5,
    }


def test_expired_claim_cannot_publish_or_retry_over_replacement(store):
    set_id = store.configure_embeddings(MANIFEST)
    source(store, "An original decision about the project.")
    stale = store.claim_embedding_batch(set_id, 1000)
    assert store.claim_embedding_batch(set_id, 1000) is None
    with store.connect() as db:
        db.execute("UPDATE embedding_jobs SET lease_until=?", (stamp(utc_now() - timedelta(1)),))
    replacement = store.claim_embedding_batch(set_id, 1000)
    assert replacement["lease_token"] != stale["lease_token"]
    store.retry_embedding(stale)
    assert store.claim_embedding_batch(set_id, 1000) is None
    assert not store.finish_embedding_batch(stale, [[0, 1]])
    assert store.finish_embedding_batch(replacement, [[1, 0]])
    assert not store.finish_embedding_batch(replacement, [[0, 1]])
    assert store.semantic_candidates(set_id, [1, 0], 0.9, 10, None, None)


def test_concurrent_claims_are_exclusive_and_rebuild_preserves_evidence(store):
    set_id = store.configure_embeddings(MANIFEST)
    keys = {source(store, f"Unique source number {i} for the project.") for i in range(4)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim_embedding_batch(set_id, 1000), range(8)))
    jobs = [job for job in claims if job]
    assert len(jobs) == 4 and {job["audio_id"] for job in jobs} == keys
    for job in jobs:
        assert store.finish_embedding_batch(job, [[1, 0]])
    before = store.semantic_candidates(set_id, [1, 0], -1, 10, None, None)
    store.rebuild_embeddings([set_id])
    assert store.semantic_candidates(set_id, [1, 0], -1, 10, None, None) == before
    assert drain(store, set_id)
    assert store.embedding_coverage(set_id) == {"total": 4, "indexed": 4}


@pytest.mark.parametrize("mutation", ["correct", "delete", "retention"])
def test_unpublished_staging_purged_with_source_and_stale_lease_rejected(store, mutation):
    set_id = store.configure_embeddings(MANIFEST)
    key = source(store, "An original project decision. " * 50)
    job = store.claim_embedding_batch(set_id, 1000)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_staging_passages").fetchone()[0]
    if mutation == "correct":
        store.correct_transcript(key, "A corrected decision.")
    elif mutation == "delete":
        store.delete_audio(key)
    else:
        store.apply_retention(0, 1, now=AT + timedelta(days=2))
    assert not store.finish_embedding_batch(job, [[1, 0]] * len(job["passages"]))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_staging_passages").fetchone()[0] == 0
    assert store.semantic_candidates(set_id, [1, 0], -1, 10, None, None) == []


def test_passage_diversity_and_exact_offsets_without_vector_payload(store):
    set_id = store.configure_embeddings(MANIFEST)
    key = source(store, "First discussed the project. " * 20 + "Later approved the budget. " * 20)
    other = source(store, "An independent related conversation.")
    drain(store, set_id)
    rows = store.semantic_candidates(set_id, [1, 0], -1, 10, None, None, max_per_source=3)
    assert sum(row["audio_id"] == key for row in rows) == 3
    assert any(row["audio_id"] == other for row in rows)
    assert all("vector" not in row for row in rows)
    assert all(row["end_offset"] > row["start_offset"] for row in rows)
    assert store.semantic_candidates(set_id, [1, 0], -1, 0, None, None) == []


def test_corrupt_derived_metadata_does_not_disable_retrieval(store):
    set_id = store.configure_embeddings(MANIFEST)
    source(store, "A trustworthy source with corrupted derived JSON metadata.")
    drain(store, set_id)
    with store.connect() as db:
        db.execute("UPDATE retrieval_passages SET metadata='invalid JSON'")
    rows = store.semantic_candidates(set_id, [1, 0], -1, 10, None, None)
    assert rows and rows[0]["metadata"] == {}


def test_additive_migration_is_idempotent_and_preserves_legacy_vectors():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE audio (id TEXT, text TEXT, state TEXT, transcript_version TEXT,"
                   "topic_details TEXT,segments TEXT,source_version TEXT)")
        db.executescript(SEMANTIC_SCHEMA)
        vector = struct.pack("<2f", 1, 0)
        db.execute("INSERT INTO retrieval_passages VALUES(?,?,?,?,?,?,?,?)",
                   ("passage", "source", "set", "version", 0, 0, 15, vector))
        db.execute("INSERT INTO embedding_jobs(audio_id,set_id,version,state) "
                   "VALUES('source','set','version','done')")
        migrate_semantic_schema(db)
        migrate_semantic_schema(db)
        row = dict(db.execute("SELECT * FROM retrieval_passages").fetchone())
        assert row["vector"] == vector and row["version"] == "version"
        assert row["metadata"] == "{}" and row["content_hash"] == ""
        assert db.execute("SELECT state FROM embedding_jobs").fetchone()[0] == "pending"
        assert {row["name"] for row in db.execute("PRAGMA table_info(embedding_jobs)")} >= {
            "lease_token", "lease_until", "prepared",
        }


@pytest.mark.parametrize("segments", [
    [{"start": 1, "end": 2, "text": "one"}],
    [{"start": 1, "end": 99, "text": "one two"}],
    [{"start": 1, "end": 2, "text": "wrong text"}],
    [{"start": True, "end": 2, "text": "one two"}],
    [{"start": 1, "end": 2, "text": "one"}, {"start": 3, "end": 4, "text": "two"}],
])
def test_imprecise_or_invalid_segment_alignment_never_invents_timestamps(segments):
    row = {"text": "one two one", "started_at": AT.isoformat(),
           "ended_at": (AT + timedelta(seconds=30)).isoformat(), "segments": json.dumps(segments)}
    assert segment_metadata(row, {"start_offset": 0, "end_offset": len(row["text"])}) == {}
