"""Live capture has bounded latency without starving deferred recordings."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_extended import audio_data

from omi_local.domain import stamp
from omi_local.store import Store
from omi_local.transcription_queue import MAX_RECENT_BURST

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def insert(store, minutes, *, source_kind="", retry_at="", state="queued"):
    key = str(uuid4())
    store.ingest(key, "pendant", NOW - timedelta(minutes=minutes), audio_data(), 1,
                 source_kind=source_kind)
    with store.connect() as db:
        db.execute("UPDATE audio SET retry_at=?,state=? WHERE id=?", (retry_at, state, key))
    return key


def test_recent_live_bypasses_large_history_but_history_progress_is_bounded(tmp_path):
    store = Store(tmp_path)
    old = [insert(store, 200 - i, source_kind="manual_upload") for i in range(12)]
    live = [insert(store, 5 - i / 10) for i in range(10)]
    claimed = [store.claim(now=NOW)["id"] for _ in range(8)]
    assert claimed[:MAX_RECENT_BURST] == live[:MAX_RECENT_BURST]
    assert claimed[3] == old[0]
    assert claimed[4:7] == live[3:6]
    assert claimed[7] == old[1]


def test_restart_preserves_fair_share_and_each_lane_retains_capture_order(tmp_path):
    store = Store(tmp_path)
    history = insert(store, 100)
    live = [insert(store, 5 - i / 10) for i in range(5)]
    for key in live[:3]:
        assert Store(tmp_path).claim(now=NOW)["id"] == key
    assert Store(tmp_path).claim(now=NOW)["id"] == history
    assert Store(tmp_path).claim(now=NOW)["id"] == live[3]


def test_due_retries_blocked_work_and_new_uploads_preserve_priority_semantics(tmp_path):
    store = Store(tmp_path)
    retry = insert(store, 300, retry_at=stamp(NOW + timedelta(minutes=1)))
    blocked = insert(store, 400, state="blocked")
    imported = insert(store, 2, source_kind="manual_upload")
    deferred_live = insert(store, 100)
    recent = insert(store, 1)
    assert store.claim(now=NOW)["id"] == recent
    assert store.claim(now=NOW)["id"] == deferred_live
    assert store.claim(now=NOW)["id"] == imported
    assert store.claim(now=NOW) is None
    assert store.claim(now=NOW + timedelta(minutes=1))["id"] == retry
    assert store.get_audio(blocked)["state"] == "blocked"


def test_no_history_never_delays_live_and_no_live_keeps_oldest_first(tmp_path):
    store = Store(tmp_path)
    live = [insert(store, 5 - i / 10) for i in range(6)]
    assert [store.claim(now=NOW)["id"] for _ in live] == live
    history = [insert(store, 100 - i) for i in range(3)]
    assert [store.claim(now=NOW)["id"] for _ in history] == history
    assert store.claim(now=NOW) is None
