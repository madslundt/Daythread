"""The pinned app can drain foreground requests without force-killing durable work."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcription_shutdown as shutdown  # noqa: E402


def test_only_audited_inbox_and_speaker_work_can_restart():
    shutdown.require_restartable({"db": {"inbox_jobs": {"queued": 54, "running": 1}}})
    shutdown.require_restartable({"db": {"speaker_jobs": {"queued": 1, "processing": 1}}})
    for table, state in (("weekly_jobs", "running"), ("notification_deliveries", "pending"),
                         ("embedding_jobs", "working"), ("unknown_jobs", "queued"),
                         ("speaker_jobs", "running"), ("speaker_jobs", "pending")):
        with pytest.raises(RuntimeError, match=table):
            shutdown.require_restartable({"db": {table: {state: 1}}})


def test_shutdown_observer_leaves_slow_foreground_request_alive(monkeypatch):
    monkeypatch.setattr(shutdown.time, "sleep", lambda _: None)
    current = {"Id": "old", "State": {"Running": True}}
    with pytest.raises(shutdown.ShutdownPending):
        shutdown.wait_for_shutdown(lambda: current, "old", attempts=2)
    assert current["State"]["Running"] is True
    current["State"] = {"Running": False, "ExitCode": 0}
    assert shutdown.wait_for_shutdown(lambda: current, "old")["State"]["ExitCode"] == 0


def test_shutdown_observer_rejects_container_replacement():
    with pytest.raises(RuntimeError, match="identity"):
        shutdown.wait_for_shutdown(lambda: {"Id": "foreign"}, "old")
    with pytest.raises(RuntimeError, match="unknown"):
        shutdown.wait_for_shutdown(lambda: {"Id": "old", "State": {}}, "old")


def test_runtime_proof_requires_exact_sources_command_and_no_init():
    current = {"Config": {"Cmd": shutdown.COMMAND, "Entrypoint": None}, "HostConfig": {}}
    def output(*_):
        return json.dumps(shutdown.SOURCE_HASHES)
    shutdown.verify_shutdown_runtime(current, output, "app")
    with pytest.raises(RuntimeError, match="source"):
        shutdown.verify_shutdown_runtime(current, lambda *_: "{}", "app")
    current["HostConfig"]["Init"] = True
    with pytest.raises(RuntimeError, match="PID1"):
        shutdown.verify_shutdown_runtime(current, output, "app")
    current["HostConfig"]["Init"] = False
    current["Config"]["Env"] = ["UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=1"]
    with pytest.raises(RuntimeError, match="PID1"):
        shutdown.verify_shutdown_runtime(current, output, "app")
    current["Config"]["Env"] = []
    current["Config"]["Cmd"] = [*shutdown.COMMAND, "--timeout-graceful-shutdown", "1"]
    with pytest.raises(RuntimeError, match="PID1"):
        shutdown.verify_shutdown_runtime(current, output, "app")


def test_inbox_ids_survive_completion_and_missing_rows_are_rejected(tmp_path):
    path = tmp_path / "omi.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox_jobs(id TEXT PRIMARY KEY,state TEXT)")
        db.executemany("INSERT INTO inbox_jobs VALUES(?,?)", [("a", "running"), ("b", "queued")])
    before = shutdown.inbox_snapshot(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE inbox_jobs SET state='done' WHERE id='a'")
    after = shutdown.inbox_snapshot(tmp_path, before)
    assert before["omi.sqlite3"]["ids_sha256"] == after["omi.sqlite3"]["ids_sha256"]
    assert after["omi.sqlite3"]["jobs"] == {"a": "done", "b": "queued"}
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM inbox_jobs WHERE id='b'")
    with pytest.raises(RuntimeError, match="identities"):
        shutdown.inbox_snapshot(tmp_path, before)


def speaker_database(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE speaker_jobs(scope_key TEXT PRIMARY KEY,fingerprint TEXT,"
                   "state TEXT,attempts INTEGER,retry_at TEXT,error TEXT,updated_at TEXT)")
        db.execute("INSERT INTO speaker_jobs VALUES('source:a','fingerprint-a',"
                   "'processing',5,'2000-01-01',NULL,'before')")


@pytest.mark.parametrize("state,attempts,retry,error", [
    ("processing", 5, "2000-01-01", None), ("queued", 5, "2000-01-01", None),
    ("done", 5, None, None), ("error", 6, "later", "Speaker detection failed"),
    ("error", 8, "later", "Speaker detection failed"), ("done", 8, None, None),
])
def test_speaker_identity_and_retry_history_survive_natural_transitions(
    tmp_path, state, attempts, retry, error,
):
    path = tmp_path / "omi.sqlite3"
    speaker_database(path)
    before = shutdown.speaker_snapshot(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE speaker_jobs SET state=?,attempts=?,retry_at=?,error=?,updated_at=?",
                   (state, attempts, retry, error, "after"))
    after = shutdown.speaker_snapshot(tmp_path, before)
    assert after["omi.sqlite3"]["ids_sha256"] == before["omi.sqlite3"]["ids_sha256"]
    assert after["omi.sqlite3"]["jobs"]["source:a"] == {
        "fingerprint": "fingerprint-a", "state": state, "attempts": attempts,
        "retry_at": retry, "error": error, "updated_at": "after",
    }


@pytest.mark.parametrize("sql", [
    "DELETE FROM speaker_jobs", "UPDATE speaker_jobs SET scope_key='different'",
    "UPDATE speaker_jobs SET fingerprint='different'", "UPDATE speaker_jobs SET attempts=0",
    "UPDATE speaker_jobs SET state='unrecognized'",
    "UPDATE speaker_jobs SET state='done'",  # Completion must clear retry metadata.
    "UPDATE speaker_jobs SET state='error'",  # A natural failure retains its reason.
])
def test_speaker_snapshot_rejects_identity_or_retry_history_loss(tmp_path, sql):
    path = tmp_path / "omi.sqlite3"
    speaker_database(path)
    before = shutdown.speaker_snapshot(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(sql)
    with pytest.raises(RuntimeError, match="speaker"):
        shutdown.speaker_snapshot(tmp_path, before)


def test_speaker_snapshot_tracks_new_jobs_without_erasing_completed_identity(tmp_path):
    path = tmp_path / "omi.sqlite3"
    speaker_database(path)
    before = shutdown.speaker_snapshot(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE speaker_jobs SET state='done',retry_at=NULL")
        db.execute("INSERT INTO speaker_jobs VALUES('source:b','fingerprint-b',"
                   "'queued',0,NULL,NULL,'during-drain')")
    stopped = shutdown.speaker_snapshot(tmp_path, before)
    assert set(stopped["omi.sqlite3"]["jobs"]) == {"source:a", "source:b"}
    assert stopped["omi.sqlite3"]["jobs"]["source:a"]["state"] == "done"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE speaker_jobs SET fingerprint='changed' WHERE scope_key='source:a'")
    with pytest.raises(RuntimeError, match="speaker"):
        shutdown.speaker_snapshot(tmp_path, stopped)
