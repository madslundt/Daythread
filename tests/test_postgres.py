"""Exercise real managed persistence with TEST_POSTGRES_URL pointing at a test DB."""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omi_local.postgres import PostgresDatabase
from omi_local.store import Store

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL is not configured")
AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def pg_store(tmp_path):
    schema = "test_archive_" + uuid4().hex
    store = Store(tmp_path, database_url=URL, database_schema=schema)
    yield store
    with store.database.pool.connection() as db:
        db.execute(f'DROP SCHEMA "{schema}" CASCADE')


def record(store, text="Søren owns the budget.", *, key=None):
    key = key or uuid4()
    store.ingest(key, "phone", AT, b"recording", 30)
    store.complete(str(key), text)
    return str(key)


def test_postgres_primary_survives_restart_and_isolates_accounts(pg_store, tmp_path):
    key = record(pg_store)
    restarted = Store(
        tmp_path / "another-host",
        database_url=URL,
        database_schema=pg_store.database.schema,
    )
    assert restarted.get_audio(key)["text"] == "Søren owns the budget."
    assert not (tmp_path / "omi.sqlite3").exists()
    assert [row["id"] for row in restarted.search("Søren budget", 10)] == [key]
    other_schema = "test_archive_" + uuid4().hex
    try:
        other = Store(tmp_path / "other", database_url=URL, database_schema=other_schema)
        assert other.get_audio(key) is None
        assert other.search("budget", 10) == []
    finally:
        with pg_store.database.pool.connection() as db:
            db.execute(f'DROP SCHEMA "{other_schema}" CASCADE')


def test_postgres_claims_are_exclusive_and_rollbacks_keep_data(pg_store):
    keys = []
    for index in range(6):
        key = uuid4()
        pg_store.ingest(key, "phone", AT + timedelta(minutes=index), str(index).encode(), 30)
        keys.append(str(key))
    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = list(pool.map(lambda _: pg_store.claim(), range(6)))
    assert {row["id"] for row in claimed} == set(keys)
    assert pg_store.claim() is None
    with pytest.raises(RuntimeError), pg_store.connect() as db:
        db.execute("DELETE FROM audio")
        raise RuntimeError("Cancel transaction")
    assert len(pg_store.window(AT, AT + timedelta(hours=1))) == 6


def test_postgres_trigger_correction_invalidates_derivatives(pg_store):
    key = record(pg_store)
    old = pg_store.get_audio(key)
    with pg_store.connect() as db:
        db.execute(
            "INSERT INTO transcript_translations VALUES(?,?,?,?,?)",
            (key, "da", old["transcript_version"], "Budgettet", AT.isoformat()),
        )
        db.execute("INSERT INTO recap_summaries VALUES(?,?)", ("fingerprint", "Summary"))
    pg_store.correct_transcript(key, "The deadline changed.")
    current = pg_store.get_audio(key)
    assert current["source_version"] != old["source_version"]
    assert current["transcript_version"] != old["transcript_version"]
    assert pg_store.search("budget", 10) == []
    assert [row["id"] for row in pg_store.search("deadline", 10)] == [key]
    with pg_store.connect() as db:
        assert db.execute("SELECT count(*) FROM transcript_translations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM recap_summaries").fetchone()[0] == 0
    pg_store.delete_audio(key)
    assert pg_store.get_audio(key) is None
    assert pg_store.search("deadline", 10) == []


def test_postgres_json_activity_keeps_nested_objects(pg_store):
    key = record(pg_store)
    activity = {"status": "observed", "speech": [[0, 1]], "non_speech": [[1, 30]]}
    pg_store.complete(key, "Budget", details={"voice_activity": activity})
    import json

    snapshot = pg_store.recap_snapshot()
    actual = json.loads(snapshot["rows"][0]["transcription_details"])
    assert actual["voice_activity"] == activity


def test_postgres_sqlite_import_export_preserves_versions_and_order(pg_store, tmp_path):
    source = Store(tmp_path / "source")
    first = record(source)
    record(source)
    source.save_settings({"summary_language": "da"})
    original = source.get_audio(first)
    pg_store.migrate_sqlite(source.root / "omi.sqlite3")
    assert pg_store.get_audio(first)["source_version"] == original["source_version"]
    assert pg_store.get_audio(first)["transcript_version"] == original["transcript_version"]
    assert pg_store.runtime_settings() == {"summary_language": "da"}
    assert [row["id"] for row in pg_store.search("budget", 10)] == [first]
    with sqlite3.connect(tmp_path / "export.sqlite3") as exported, pg_store.connect() as db:
        db.backup(exported)
        assert exported.execute("SELECT count(*) FROM audio").fetchone()[0] == 2
        assert exported.execute("SELECT id FROM canonical_audio").fetchone()[0] == first
        assert exported.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(ValueError, match="already contains audio"):
        pg_store.migrate_sqlite(source.root / "omi.sqlite3")


def test_postgres_rejects_schema_injection():
    with pytest.raises(ValueError, match="identifier"):
        PostgresDatabase(URL, 'bad"; DROP SCHEMA public; --')


def test_postgres_summary_chat_and_weekly_outputs_survive_restart(pg_store, tmp_path):
    from datetime import date

    from omi_local.weekly_store import WeeklyStore

    key = record(pg_store)
    sources = [pg_store.get_audio(key)]
    day = AT.date().isoformat()
    content = {"summary": "Søren will prepare the budget.", "sources": sources}
    pg_store.save_diary(day, "daily-fingerprint", content)
    session = pg_store.ensure_session(None, "Budget discussion")
    pg_store.save_exchange(session["id"], "Who owns the budget?", "Søren", sources)
    weekly = WeeklyStore(pg_store)
    snapshot = {"fingerprint": "weekly-fingerprint", "timezone": "UTC", "cutoff": AT.isoformat()}
    weekly.enqueue("2026-10-12", snapshot, str(uuid4()), False)
    job = weekly.claim()
    version = {
        "id": str(uuid4()),
        "week_start": "2026-10-12",
        "fingerprint": snapshot["fingerprint"],
        "generated_at": AT.isoformat(),
        "summary": "The budget was discussed.",
    }
    weekly.publish(job, version, lambda _: snapshot)
    restarted = Store(tmp_path, database_url=URL, database_schema=pg_store.database.schema)
    assert restarted.summary_days(date(2026, 10, 1), date(2026, 10, 31)) == [day]
    assert restarted.diaries()[0]["summary"] == content["summary"]
    messages = restarted.chat_session(session["id"])["messages"]
    assert [message["content"] for message in messages] == ["Who owns the budget?", "Søren"]
    assert messages[1]["sources"][0]["id"] == key
    assert WeeklyStore(restarted).version("2026-10-12") == version
    restarted.delete_audio(key)
    assert WeeklyStore(restarted).version("2026-10-12") is None
    assert restarted.chat_session(session["id"]) is None


def test_postgres_catalog_import_without_audio_database(tmp_path):
    import asyncio

    import httpx

    from omi_local.profiles import NewProfile, Profiles
    from omi_local.settings import Settings

    source_dir = tmp_path / "old"
    source_dir.mkdir()
    client = httpx.AsyncClient()
    source = Profiles(source_dir, client)
    created = source.create(NewProfile(name="Work"))
    schema = "test_profiles_" + uuid4().hex
    settings = Settings(database_url=URL, database_schema=schema, _env_file=None)
    destination = Profiles(tmp_path, client, settings=settings)
    try:
        with (
            sqlite3.connect(source_dir / "profiles.sqlite3") as old,
            closing(destination.connect()) as db,
            db,
        ):
            db.import_sqlite(old)
        assert destination.list() == source.list()
        assert destination.rename(created["id"], NewProfile(name="Family"))["name"] == "Family"
        with closing(destination.connect()) as db:
            assert db.execute("SELECT max(sequence) FROM profile_events").fetchone()[0] == 2
        assert not (tmp_path / "profiles.sqlite3").exists()
    finally:
        with destination.database.pool.connection() as db:
            db.execute(f'DROP SCHEMA "{schema}" CASCADE')
        asyncio.run(client.aclose())
