"""Restored archives require new phone registrations before sending pushes."""

import runpy
import shutil
import sqlite3
import zipfile
from pathlib import Path

from omi_local.store import Store


def registered_archive(root):
    store = Store(root)
    token = "abcdef1234567890" * 8
    store.register_notification_device("original-phone", token, "sandbox", False)
    event = store.publish_notification(
        "recap", "Recap ready", "Your notes are ready.", {"day": "2026-10-17"}, "test-recap",
    )
    assert len(store.pending_notifications()) == 1
    return store, token, event


def test_backup_excludes_push_bindings_without_changing_live_archive(tmp_path):
    store, token, event = registered_archive(tmp_path / "live")
    backup = store.export_archive()
    try:
        with zipfile.ZipFile(backup) as archive:
            snapshot = archive.read("omi.sqlite3")
        assert token.encode() not in snapshot
        exported = tmp_path / "snapshot.sqlite3"
        exported.write_bytes(snapshot)
        with sqlite3.connect(exported) as db:
            assert db.execute("SELECT count(*) FROM notification_devices").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0] == 0
            assert db.execute("SELECT id FROM notifications").fetchone()[0] == event["id"]
        assert store.pending_notifications()[0]["token"] == token
    finally:
        shutil.rmtree(backup.parent)


def test_restore_discards_device_bindings_from_older_backups(tmp_path):
    store, token, event = registered_archive(tmp_path / "live")
    backup = tmp_path / "older-backup.zip"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.write(store.root / "omi.sqlite3", "omi.sqlite3")
    restore = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "archive.py"))["restore"]
    destination = tmp_path / "restored"
    restore(backup, destination, max_bytes=100_000_000)
    restored = Store(destination)
    assert token.encode() not in (destination / "omi.sqlite3").read_bytes()
    with restored.connect() as db:
        assert db.execute("SELECT count(*) FROM notification_devices").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0] == 0
    assert restored.notifications_after(0)["notifications"] == [event]
    assert store.pending_notifications()[0]["token"] == token
