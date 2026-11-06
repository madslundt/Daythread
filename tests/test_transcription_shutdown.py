"""The pinned app can drain foreground requests without force-killing durable work."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcription_shutdown as shutdown  # noqa: E402


def test_only_audited_inbox_work_can_restart():
    shutdown.require_restartable({"db": {"inbox_jobs": {"queued": 54, "running": 1}}})
    for table, state in (("weekly_jobs", "running"), ("notification_deliveries", "pending"),
                         ("embedding_jobs", "working"), ("unknown_jobs", "queued")):
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
