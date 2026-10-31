"""Audio invalidation removes only previews whose published evidence changed."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omi_local.domain import stamp
from omi_local.postgres import PostgresConnection
from omi_local.store import Store

AT = datetime(2026, 10, 31, 12, tzinfo=UTC)


def source(store, text="Recorded words"):
    key = str(uuid4())
    store.ingest(key, "phone", AT, b"audio", 30)
    store.complete(key, text)
    return key


@pytest.mark.parametrize("kind", ["recap", "weekly"])
def test_saved_projection_notices_keep_original_source_dependencies(tmp_path, kind):
    store = Store(tmp_path)
    first, second, unrelated = source(store), source(store), source(store)
    version = str(uuid4())
    content = json.dumps({"sources": [{"id": first}, {"id": second}]})
    with store.connect() as db:
        db.execute("INSERT INTO diary VALUES(?,?,?,?)",
                   ("2026-10-31", "fingerprint", content, stamp(AT)))
        db.execute("INSERT INTO weekly_versions VALUES(?,?,?,?,?)",
                   (version, "2026-10-26", "fingerprint", content, stamp(AT)))
    target = {"day": "2026-10-31"} if kind == "recap" else {"version_id": version}
    store.register_notification_device("phone", "ab" * 32, "sandbox", False)
    notice = store.publish_notification(kind, "Ready", "Recorded words", target, "test:1")
    store.correct_transcript(unrelated, "Unrelated correction")
    assert store.notifications_after(0)["notifications"] == [notice]
    assert len(store.pending_notifications()) == 1
    store.correct_transcript(second, "Changed contributing evidence")
    assert store.notifications_after(0)["notifications"] == []
    assert store.pending_notifications() == []
    with store.connect() as db:
        assert not db.execute("SELECT * FROM notification_sources").fetchall()


@pytest.mark.parametrize("mutation", ["delete", "correct", "expire", "replace"])
def test_direct_source_notices_invalidate_only_affected_delivery(tmp_path, mutation):
    store = Store(tmp_path)
    affected, other = source(store), source(store)
    store.register_notification_device("phone", "ab" * 32, "sandbox", False)
    stale = store.publish_notification("translation", "Ready", "Old words",
                                       {"source_id": affected}, "affected")
    valid = store.publish_notification("translation", "Ready", "Other words",
                                       {"source_id": other}, "other")
    if mutation == "delete":
        store.delete_audio(affected)
    elif mutation == "correct":
        store.correct_transcript(affected, "Corrected words")
    else:
        with store.connect() as db:
            if mutation == "expire":
                db.execute("UPDATE audio SET text=NULL,state='expired' WHERE id=?", (affected,))
            else:
                db.execute("UPDATE audio SET source_version='replacement' WHERE id=?", (affected,))
    assert store.notifications_after(0)["notifications"] == [valid]
    assert [item["id"] for item in store.pending_notifications()] == [valid["id"]]
    assert not store.notification_still_pending(stale["id"], "phone")


@pytest.mark.parametrize("kind", ["wearable_action", "recap", "weekly", "translation"])
def test_reopen_migrates_legacy_notices_and_replaces_global_triggers(tmp_path, kind):
    store = Store(tmp_path)
    first, second, unrelated = source(store), source(store), source(store)
    job, version = str(uuid4()), str(uuid4())
    content = json.dumps({"sources": [{"id": first}, {"id": second}]})
    with store.connect() as db:
        db.executemany("INSERT INTO generation_job_sources VALUES(?,?)",
                       [(job, first), (job, second)])
        db.execute("INSERT INTO diary VALUES(?,?,?,?)", ("2026-10-31", "key", content, stamp(AT)))
        db.execute("INSERT INTO weekly_versions VALUES(?,?,?,?,?)",
                   (version, "2026-10-26", "key", content, stamp(AT)))
    target = {"wearable_action": {"job_id": job}, "recap": {"day": "2026-10-31"},
              "weekly": {"version_id": version}, "translation": {"source_id": first}}[kind]
    store.register_notification_device("phone", "ab" * 32, "sandbox", False)
    notice = store.publish_notification(kind, "Ready", "Original result", target, "legacy")
    with store.connect() as db:
        db.executescript("""
            DROP TRIGGER notification_source_removed;
            DROP TRIGGER notification_source_corrected;
            DROP TRIGGER notification_removed;
            DROP TABLE notification_sources;
            CREATE TRIGGER notification_removed AFTER DELETE ON notifications BEGIN
              DELETE FROM notification_deliveries WHERE notification_id=OLD.id;
            END;
            CREATE TRIGGER notification_source_removed AFTER DELETE ON audio BEGIN
              DELETE FROM notifications;
            END;
            CREATE TRIGGER notification_source_corrected AFTER UPDATE OF text ON audio BEGIN
              DELETE FROM notifications;
            END;
        """)
    restored = Store(tmp_path)
    Store(tmp_path)  # Repeating startup must preserve the complete source snapshot.
    with restored.connect() as db:
        rows = db.execute("SELECT source_id FROM notification_sources WHERE notification_id=?",
                          (notice["id"],)).fetchall()
    expected = {first} if kind == "translation" else {first, second}
    assert {row["source_id"] for row in rows} == expected
    restored.correct_transcript(unrelated, "Unrelated words changed after restart")
    assert restored.notifications_after(0)["notifications"] == [notice]
    assert len(restored.pending_notifications()) == 1
    restored.correct_transcript(first, "Related words changed after restart")
    assert restored.notifications_after(0)["notifications"] == []
    assert restored.pending_notifications() == []


def test_postgres_drop_trigger_resolves_its_table_without_an_external_database():
    calls = []

    class Connection:
        def execute(self, sql, parameters=None):
            calls.append((sql, parameters))
            return SimpleNamespace(fetchall=lambda: [("audio",)])

        def cursor(self):
            return "empty cursor"

    database = SimpleNamespace(schema="archive_test", tables={})
    connection = PostgresConnection(database, Connection())
    result = connection.execute("DROP TRIGGER IF EXISTS notification_source_corrected;")
    assert result == "empty cursor"
    assert calls[0][1] == ("archive_test", "notification_source_corrected")
    assert calls[1] == ('DROP TRIGGER IF EXISTS "notification_source_corrected" ON "audio"', None)
