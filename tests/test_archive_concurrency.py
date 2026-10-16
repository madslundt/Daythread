"""Archive snapshots must remain recoverable during capture maintenance."""

import errno
import hashlib
import io
import shutil
import sqlite3
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest

from omi_local.store import Store


@pytest.mark.parametrize("operation", ["retention", "delete"])
@pytest.mark.parametrize("cross_filesystem", [False, True])
def test_backup_keeps_audio_during_concurrent_removal(
    tmp_path, monkeypatch, operation, cross_filesystem
):
    store = Store(tmp_path / "archive")
    key = uuid4()
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\x00" * 16000)
    data = output.getvalue()
    at = datetime(2026, 10, 15, tzinfo=UTC)
    store.ingest(key, "omi", at, data, 1)
    if cross_filesystem:
        def unavailable_link(*args):
            raise OSError(errno.EXDEV, "Cross-device link")

        monkeypatch.setattr("omi_local.archive_store.os.link", unavailable_link)
    started = Event()
    original_write = zipfile.ZipFile.write
    retention = None

    def expire():
        started.set()
        concurrent = Store(store.root)
        if operation == "delete":
            return concurrent.delete_audio(str(key))
        return concurrent.apply_retention(1, 0, at + timedelta(days=3))

    with ThreadPoolExecutor(max_workers=1) as pool:
        def write(archive, filename, *args, **kwargs):
            nonlocal retention
            result = original_write(archive, filename, *args, **kwargs)
            if str(filename).endswith("omi.sqlite3"):
                retention = pool.submit(expire)
                assert started.wait(2)
                try:
                    retention.result(timeout=0.25)
                except TimeoutError:
                    pass  # A correct backup holds deletion until its referenced files are copied.
            return result

        monkeypatch.setattr(zipfile.ZipFile, "write", write)
        backup = store.export_archive()
        try:
            result = retention.result(timeout=5)
            assert result if operation == "delete" else result["audio_files_removed"] == 1
            with zipfile.ZipFile(backup) as archive:
                recording = archive.read(f"audio/{key}.wav")
                assert recording == data
                snapshot = tmp_path / "snapshot.sqlite3"
                snapshot.write_bytes(archive.read("omi.sqlite3"))
            with sqlite3.connect(snapshot) as db:
                digest, available = db.execute(
                    "SELECT digest,audio_available FROM audio WHERE id=?", (str(key),)
                ).fetchone()
            assert available == 1
            assert hashlib.sha256(recording).hexdigest() == digest
        finally:
            shutil.rmtree(backup.parent)


def test_concurrent_settings_updates_preserve_both_phone_changes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from omi_local.api import create_app
    from omi_local.settings import Settings

    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.memory.store
        save = store.save_settings
        first_entered, second_finished = Event(), Event()

        def delayed_save(changes):
            if "language" in changes:
                first_entered.set()
                second_finished.wait(0.25)
            save(changes)

        monkeypatch.setattr(store, "save_settings", delayed_save)

        def change_hour():
            result = client.patch("/v1/settings", json={"recap_hour": 20})
            second_finished.set()
            return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.patch, "/v1/settings", json={"language": "da"})
            assert first_entered.wait(2)
            second = pool.submit(change_hour)
            assert first.result(timeout=5).status_code == 200
            assert second.result(timeout=5).status_code == 200
        current = client.get("/v1/settings").json()
        assert current["language"] == "da"
        assert current["recap_hour"] == 20
        assert store.runtime_settings() == {"language": "da", "recap_hour": 20}


@pytest.mark.parametrize("operation", ["retention", "delete"])
def test_failed_logical_removal_preserves_referenced_recording(tmp_path, monkeypatch, operation):
    store = Store(tmp_path)
    key = uuid4()
    at = datetime(2026, 10, 15, tzinfo=UTC)
    store.ingest(key, "omi", at, b"original recording", 1)

    def failed_invalidation(db, purge_history=False):
        raise OSError("Injected projection invalidation failure")

    monkeypatch.setattr(store, "_invalidate_diaries", failed_invalidation)
    with pytest.raises(OSError, match="Injected"):
        if operation == "delete":
            store.delete_audio(str(key))
        else:
            store.apply_retention(1, 0, at + timedelta(days=3))
    assert store.get_audio(str(key))["audio_available"] == 1
    assert (store.audio_dir / f"{key}.wav").read_bytes() == b"original recording"


def test_cleanup_does_not_unlink_same_id_reuploaded_after_deletion(tmp_path, monkeypatch):
    store = Store(tmp_path)
    key = uuid4()
    at = datetime(2026, 10, 15, tzinfo=UTC)
    store.ingest(key, "omi", at, b"first recording", 1)
    prune = store._prune_files

    def reupload_before_cleanup(audio_paths=()):
        # Replay the interval between deletion's commit and its physical cleanup.
        Store(tmp_path).ingest(key, "omi", at, b"retried recording", 1)
        prune(audio_paths)

    monkeypatch.setattr(store, "_prune_files", reupload_before_cleanup)
    assert store.delete_audio(str(key))
    assert store.get_audio(str(key))["audio_available"] == 1
    assert (store.audio_dir / f"{key}.wav").read_bytes() == b"retried recording"
