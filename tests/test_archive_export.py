"""Portable account exports preserve all personal profiles and standard restores."""

import asyncio
import hashlib
import io
import json
import runpy
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from test_backend import AT, wav

from omi_local.api import create_app
from omi_local.archive_export import ArchiveDownload, export_profiles
from omi_local.archive_stream import write_archive
from omi_local.settings import Settings
from omi_local.store import Store


def validate_snapshot(data, destination, expected_id, audio):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "omi.sqlite3" in archive.namelist()
        assert archive.read(f"audio/{expected_id}.wav") == audio
        destination.write_bytes(archive.read("omi.sqlite3"))
    with sqlite3.connect(destination) as db:
        rows = db.execute("SELECT id,digest FROM audio").fetchall()
        assert rows == [(str(expected_id), hashlib.sha256(audio).hexdigest())]
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    # Every returned standard archive is accepted by the unchanged local CLI.
    source = destination.with_suffix(".zip")
    source.write_bytes(data)
    restored = destination.parent / (destination.stem + "-restored")
    restore = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/archive.py"))[
        "restore"
    ]
    restore(source, restored, 100_000_000)
    assert (restored / "audio" / f"{expected_id}.wav").read_bytes() == audio


def test_single_profile_download_is_standard_backup_and_removes_temporary_files(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.memory.store
        identity, audio = uuid4(), wav([9] * 16000)
        store.ingest(identity, "omi", AT, audio, 1)
        (tmp_path / ".private-settings.json").write_text('{"password":"secret"}')
        response = client.get("/v1/archive/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert 'filename="omi-backup.zip"' in response.headers["content-disposition"]
        validate_snapshot(response.content, tmp_path / "check.sqlite3", identity, audio)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert not any("private" in name for name in archive.namelist())
        assert list((tmp_path / ".exports").iterdir()) == []


def test_bundle_includes_each_own_profile_and_excludes_other_account(tmp_path):
    root = tmp_path / "person"
    settings = Settings(data_dir=root, worker_enabled=False, _env_file=None)
    unrelated = Store(tmp_path / "other-person")
    other_identity = uuid4()
    unrelated.ingest(other_identity, "omi", AT, wav([13] * 16000), 1)
    with TestClient(create_app(settings)) as client:
        profile = client.post("/v1/profiles", json={"name": "私人 / Work"}).json()
        expected = {}
        for identity in ("default", profile["id"]):
            runtime = client.app.state.profiles.runtimes[identity]
            key, audio = uuid4(), wav([7 if identity == "default" else 11] * 16000)
            runtime.memory.store.ingest(key, "omi", AT, audio, 1)
            expected[identity] = (key, audio)
        response = client.get("/v1/archive/export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["format"] == "daythread-profiles"
            assert manifest["version"] == 1
            assert len(manifest["profiles"]) == 2
            assert [p["name"] for p in manifest["profiles"]] == ["Default", "私人 / Work"]
            for index, item in enumerate(manifest["profiles"]):
                assert item["archive"] == f"profiles/{item['id']}.zip"
                assert bundle.getinfo(item["archive"]).extract_version >= 45
                key, audio = expected[item["id"]]
                validate_snapshot(
                    bundle.read(item["archive"]), tmp_path / f"check{index}.sqlite3", key, audio
                )
        assert str(other_identity).encode() not in response.content
        assert list((root / ".exports").iterdir()) == []
        assert list((root / "profiles" / profile["id"] / ".exports").iterdir()) == []


def test_failed_second_profile_export_cleans_all_work(tmp_path, monkeypatch):
    first, second = Store(tmp_path / "first"), Store(tmp_path / "second")
    identity = str(uuid4())
    catalog = SimpleNamespace(
        root=tmp_path,
        list=lambda: [{"id": "default", "name": "Default"}, {"id": identity, "name": "Work"}],
        runtimes={
            "default": SimpleNamespace(memory=SimpleNamespace(store=first)),
            identity: SimpleNamespace(memory=SimpleNamespace(store=second)),
        },
    )

    original = write_archive

    def broken(store, sink):
        if store is second:
            raise OSError("offline storage")
        return original(store, sink)

    monkeypatch.setattr("omi_local.archive_export.write_archive", broken)
    with pytest.raises(OSError):
        export_profiles(catalog, io.BytesIO())
    assert list((first.root / ".exports").iterdir()) == []


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_download_cleanup_is_idempotent_after_disconnect():
    lock = Lock()
    lock.acquire()
    response = ArchiveDownload(lambda sink: sink.write(b"ZIP"), lock, "omi-backup.zip")
    await response.cleanup()
    await response.cleanup()
    assert not lock.locked()
    assert not response.worker.is_alive()


class RemoteAudio:
    is_remote = True

    def __init__(self, root):
        self.root, self.objects, self.reads = root, {}, []

    def location(self, identity):
        return "s3://private-bucket/person/audio/" + str(identity) + ".wav"

    def write(self, identity, data):
        self.objects[str(identity)] = data

    def read(self, identity):
        self.reads.append(str(identity))
        # Only metadata snapshots may use working disk, regardless of total audio.
        assert not list(self.root.rglob("*.wav"))
        assert not list(self.root.rglob("*.zip"))
        return self.objects[str(identity)]

    def exists(self, identity):
        return str(identity) in self.objects

    def delete(self, identity):
        self.objects.pop(str(identity), None)


def test_remote_audio_stream_uses_no_wav_or_zip_work_files_and_restores(tmp_path):
    root = tmp_path / "person"
    storage = RemoteAudio(root)
    store = Store(root, storage=storage)
    identity, audio = uuid4(), wav([11] * 16000)
    store.ingest(identity, "omi", AT, audio, 1)
    result = io.BytesIO()
    write_archive(store, result)
    assert storage.reads == [str(identity)]
    assert list((root / ".exports").iterdir()) == []
    validate_snapshot(result.getvalue(), tmp_path / "remote.sqlite3", identity, audio)


def test_stream_checks_recording_hash_and_cleans_failed_snapshot(tmp_path):
    root = tmp_path / "person"
    storage = RemoteAudio(root)
    store = Store(root, storage=storage)
    identity = uuid4()
    store.ingest(identity, "omi", AT, wav([7] * 16000), 1)
    storage.objects[str(identity)] = b"corrupt"
    with pytest.raises(ValueError, match="checksum mismatch"):
        write_archive(store, io.BytesIO())
    assert list((root / ".exports").iterdir()) == []


@pytest.mark.anyio
async def test_producer_backpressure_and_disconnect_release_admission():
    # A 256KB queue bounds producer output before any HTTP body is consumed.
    entered = Event()
    completed = Event()

    def producer(sink):
        entered.set()
        for _ in range(10000):
            sink.write(b"x" * 65536)
        completed.set()

    lock = Lock()
    lock.acquire()
    response = ArchiveDownload(producer, lock, "omi-backup.zip")
    assert await asyncio.to_thread(entered.wait, 2)
    for _ in range(100):
        if response.output.full():
            break
        await asyncio.sleep(0.01)
    assert response.output.qsize() == 4
    assert not completed.is_set()
    await asyncio.wait_for(response.cleanup(), timeout=2)
    assert not response.worker.is_alive()
    assert not lock.locked()


@pytest.mark.anyio
async def test_cancelling_streamed_remote_export_joins_producer_and_removes_snapshot(tmp_path):
    import os

    root = tmp_path / "person"
    storage = RemoteAudio(root)
    store = Store(root, storage=storage)
    for _ in range(20):
        store.ingest(uuid4(), "omi", AT, wav(list(os.urandom(16000))), 1)
    lock = Lock()
    lock.acquire()
    response = ArchiveDownload(lambda sink: write_archive(store, sink), lock, "omi-backup.zip")
    for _ in range(100):
        if response.output.full():
            break
        await asyncio.sleep(0.01)
    assert response.output.full()
    assert len(storage.reads) < 20
    await asyncio.wait_for(response.cleanup(), timeout=2)
    assert not response.worker.is_alive()
    assert not lock.locked()
    assert list((root / ".exports").iterdir()) == []


def test_stream_guard_prevents_remote_recording_deletion_until_snapshot_finishes(
    tmp_path, monkeypatch
):
    root = tmp_path / "person"
    storage = RemoteAudio(root)
    store = Store(root, storage=storage)
    identity, audio = uuid4(), wav([7] * 16000)
    store.ingest(identity, "omi", AT, audio, 1)
    entered, release = Event(), Event()
    original_read = storage.read

    def blocked_read(key):
        entered.set()
        assert release.wait(2)
        return original_read(key)

    monkeypatch.setattr(storage, "read", blocked_read)
    result = io.BytesIO()
    with ThreadPoolExecutor(max_workers=2) as pool:
        export = pool.submit(write_archive, store, result)
        assert entered.wait(2)
        deletion = pool.submit(store.delete_audio, str(identity))
        with pytest.raises(TimeoutError):
            deletion.result(timeout=0.05)
        release.set()
        export.result(timeout=2)
        assert deletion.result(timeout=2)
    validate_snapshot(result.getvalue(), tmp_path / "pinned.sqlite3", identity, audio)


@pytest.mark.anyio
async def test_response_start_failure_cancels_backpressured_worker_before_releasing_admission():
    def producer(sink):
        for _ in range(10000):
            sink.write(b"x" * 65536)

    lock = Lock()
    lock.acquire()
    response = ArchiveDownload(producer, lock, "omi-backup.zip")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/archive/export",
        "query_string": b"",
        "headers": [],
        "asgi": {"spec_version": "2.4"},
    }

    async def send(event):
        raise OSError("Disconnected before response headers")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)
    assert not response.worker.is_alive()
    assert not lock.locked()


@pytest.mark.anyio
async def test_producer_failure_closes_stream_and_releases_admission_without_private_errors():
    def broken(sink):
        sink.write(b"partial ZIP")
        raise ValueError("private-bucket/secret-object and credentials")

    lock = Lock()
    lock.acquire()
    response = ArchiveDownload(broken, lock, "omi-backup.zip")
    chunks = []
    with pytest.raises(RuntimeError, match="^Your archive could not be exported.$"):
        async for chunk in response.body_iterator:
            chunks.append(chunk)
    assert chunks == [b"partial ZIP"]
    assert not response.worker.is_alive()
    assert not lock.locked()
