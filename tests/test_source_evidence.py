"""Redundant evidence cannot consume recall limits or hide complementary passages."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.weekly import WeeklyReviews

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
TEXT = (
    "We reviewed the launch proposal and agreed to send the revised budget to Maria "
    "before Friday afternoon so that the team can confirm the final delivery date."
)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def source(store, text=TEXT, *, data=b"original", kind="manual_upload", key=None, at=AT):
    key = key or uuid4()
    store.ingest(key, "test", at, data, 30, source_kind=kind)
    store.complete(str(key), text)
    return str(key)


def test_duplicates_are_removed_before_search_and_sampling_limits(store):
    # UUID order differs from insertion order: identity must follow the original source.
    first = source(store, key=UUID(int=900))
    duplicates = {source(store, key=UUID(int=i)) for i in range(1, 6)}
    unique = source(store, "The budget also needs an accessibility review.", data=b"unique")
    assert {row["id"] for row in store.search("budget", 2)} == {first, unique}
    sampled, total = store.sample_transcripts(2)
    assert total == 2 and {row["id"] for row in sampled} == {first, unique}
    assert len(store.window(AT, AT + timedelta(minutes=1))) == 7
    assert all(store.get_audio(key) is not None for key in duplicates)


def test_partial_unique_ending_is_preserved_even_with_a_matching_relationship(store):
    first = source(store)
    alternative = source(store, TEXT + " The accessibility checklist is due Monday.",
                         data=b"different recording")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM source_matches").fetchone()[0] == 1
    assert {row["id"] for row in store.search("budget", 2)} == {first, alternative}
    sampled, total = store.sample_transcripts(2)
    assert total == 2 and {row["id"] for row in sampled} == {first, alternative}


def test_matching_identical_transcripts_can_share_evidence_across_audio_formats(store):
    first = source(store)
    second = source(store, data=b"different encoding")
    assert [row["id"] for row in store.search("budget", 2)] == [first]
    assert store.get_audio(second)["text"] == TEXT


def test_identical_reading_with_unique_time_coverage_remains_available_in_scope(store):
    first = source(store)
    second = source(store, data=b"overlapping", at=AT + timedelta(seconds=10))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM source_matches").fetchone()[0] == 1
    start, end = AT + timedelta(seconds=31), AT + timedelta(seconds=40)
    assert [row["id"] for row in store.search("budget", 1, start, end)] == [second]
    sampled, total = store.sample_transcripts(1, start, end)
    assert total == 1 and sampled[0]["id"] == second
    assert {row["id"] for row in store.search("budget", 2)} == {first, second}


def test_correction_and_deletion_reveal_surviving_original_evidence(store):
    first = source(store)
    second = source(store)
    assert store.sample_transcripts(5)[1] == 1
    store.correct_transcript(first, "The corrected budget has a different deadline.")
    assert {row["id"] for row in store.search("budget", 5)} == {first, second}
    store.correct_transcript(first, TEXT)
    assert store.sample_transcripts(5)[1] == 1
    store.delete_audio(first)
    assert [row["id"] for row in store.search("budget", 5)] == [second]


def test_short_exact_audio_deduplicates_but_timestamps_alone_do_not(store):
    first = source(store, "Send the budget tomorrow.")
    source(store, "Send the budget tomorrow.")
    unrelated = source(store, "Send the budget tomorrow.", data=b"unrelated")
    assert {row["id"] for row in store.search("budget", 3)} == {first, unrelated}


@pytest.mark.parametrize("kind", ["omi_audio_webhook", "omi_api"])
def test_receipt_estimates_and_import_digests_do_not_establish_duplicate_audio(store, kind):
    # A short identical reading cannot establish a transcript relationship either.
    first = source(store, "Send the budget tomorrow.", kind=kind)
    second = source(store, "Send the budget tomorrow.", kind=kind)
    assert {row["id"] for row in store.search("budget", 2)} == {first, second}


def test_even_linked_webhook_estimates_keep_both_sources_searchable(store):
    first = source(store, kind="omi_audio_webhook")
    second = source(store, kind="omi_audio_webhook")
    with store.connect() as db:
        db.execute("INSERT INTO source_matches VALUES(?,?,'transcript')", sorted((first, second)))
    assert {row["id"] for row in store.search("budget", 2)} == {first, second}


def test_semantic_limit_preserves_distinct_evidence(store):
    first = source(store)
    for _ in range(4):
        source(store)
    unique = source(store, "Budget needs an accessibility review.", data=b"unique")
    set_id = store.configure_embeddings({"dimension": 2})
    while job := store.claim_embedding_batch(set_id, 500):
        assert store.finish_embedding_batch(job, [[1.0, 0.0]] * len(job["passages"]))
    candidates = store.semantic_candidates(set_id, [1.0, 0.0], 0.5, 2, None, None)
    assert {row["audio_id"] for row in candidates} == {first, unique}


def test_weekly_review_uses_distinct_evidence_and_keeps_unique_details(store, monkeypatch):
    first = source(store)
    source(store)
    unique = source(store, TEXT + " The accessibility checklist is due Monday.", data=b"unique")
    settings = Settings(data_dir=store.root, timezone="UTC", _env_file=None)
    memory = Memory(store, settings, object())
    monkeypatch.setattr("omi_local.weekly.utc_now", lambda: AT + timedelta(hours=1))
    snapshot = WeeklyReviews(memory).snapshot(date(2026, 10, 12))
    assert {row["id"] for row in snapshot["sources"]} == {first, unique}
