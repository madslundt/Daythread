"""Standalone folder imports preserve complete recordings across retries and restarts."""

import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/import_recordings.py"
AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(scope="module")
def importer():
    spec = importlib.util.spec_from_file_location("daythread_folder_importer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state(importer, tmp_path):
    result = importer.State(tmp_path / "state.sqlite3")
    yield result
    result.close()


def file(folder, name, data=b"recording"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def recording(importer, path, *, started_at=AT, vendor="manual"):
    return importer.Recording(
        key=str(path), paths=(path,), started_at=started_at,
        source_id=path.name, vendor_id=vendor,
    )


def test_parrot_pairs_exact_utc_suffix_and_ignores_imports_and_debug(importer, tmp_path):
    system = file(tmp_path, "system_2026-10-15T12-00-00Z.caf")
    mic = file(tmp_path, "mic_2026-10-15T12-00-00Z.caf")
    for name in ("aec-mic_2026-10-15T12-00-00Z.caf", "aec-ref_2026-10-15T12-00-00Z.caf",
                 "import_1792065600.caf", "unrelated.wav"):
        file(tmp_path, name)
    found = importer.discover(tmp_path, parrot=True)
    assert len(found) == 1
    assert found[0].paths == (system, mic)
    assert found[0].started_at == AT
    assert found[0].started_at.utcoffset() == timedelta(0)
    assert found[0].vendor_id == "parrot"


def test_parrot_missing_microphone_remains_importable(importer, tmp_path):
    system = file(tmp_path, "system_2026-10-15T12-00-00Z.caf")
    assert importer.discover(tmp_path, parrot=True)[0].paths == (system,)


def test_generic_discovery_keeps_files_separate_and_recursion_is_opt_in(importer, tmp_path):
    first = file(tmp_path, "meeting.wav")
    nested = file(tmp_path, "nested/meeting.mp3")
    file(tmp_path, "notes.txt")
    found = importer.discover(tmp_path, parrot=False)
    assert [item.paths for item in found] == [(first,)]
    assert found[0].started_at is None
    assert {item.paths for item in importer.discover(tmp_path, parrot=False, recursive=True)} == {
        (first,), (nested,),
    }


def test_new_watcher_baselines_existing_files_but_imports_new_arrivals(importer, state, tmp_path):
    folder = tmp_path / "recordings"
    old = file(folder, "existing.wav")
    calls = []
    watcher = importer.FolderWatcher(
        folder, False, state, lambda item, signature: calls.append(item.paths) or "uploaded",
        settle_seconds=30,
    )
    watcher.scan(now=0)
    watcher.scan(now=31)
    assert not calls
    new = file(folder, "new.wav")
    watcher.scan(now=40)
    watcher.scan(now=71)
    assert calls == [(new,)]
    assert old.exists()


def test_include_existing_still_waits_for_a_stable_recording(importer, state, tmp_path):
    folder = tmp_path / "recordings"
    path = file(folder, "existing.wav")
    calls = []
    watcher = importer.FolderWatcher(
        folder, False, state, lambda item, signature: calls.append(signature) or "uploaded",
        include_existing=True, settle_seconds=30,
    )
    watcher.scan(now=0)
    assert not calls
    path.write_bytes(path.read_bytes() + b"more audio")
    watcher.scan(now=31)
    assert not calls
    result = watcher.scan(now=62)
    assert result["imported"] == 1 and len(calls) == 1
    watcher.scan(now=100)
    assert len(calls) == 1


@pytest.mark.skipif(not shutil.which("lsof"), reason="lsof is required for writer detection")
def test_an_open_writer_blocks_even_unchanged_recordings(importer, state, tmp_path):
    folder = tmp_path / "recordings"
    path = file(folder, "open.wav")
    calls = []
    watcher = importer.FolderWatcher(
        folder, False, state, lambda item, signature: calls.append(item.key) or "uploaded",
        include_existing=True, settle_seconds=30,
    )
    with path.open("ab"):
        watcher.scan(now=0)
        watcher.scan(now=31)
        assert not calls
    watcher.scan(now=62)
    assert len(calls) == 1


def test_failed_import_retries_and_success_survives_a_watcher_restart(importer, tmp_path):
    folder = tmp_path / "recordings"
    file(folder, "retry.wav")
    calls = []

    def upload(item, signature):
        calls.append(signature)
        if len(calls) == 1:
            raise ValueError("temporary network failure")
        return "uploaded"

    state_path = tmp_path / "state.sqlite3"
    first = importer.State(state_path)
    first.bind("original destination and watch folder")
    with pytest.raises(ValueError, match="already owns"):
        importer.State(state_path)
    try:
        watcher = importer.FolderWatcher(
            folder, False, first, upload, include_existing=True, settle_seconds=30,
        )
        watcher.scan(now=0)
        assert watcher.scan(now=31)["failed"] == 1
        assert watcher.scan(now=92)["imported"] == 1
    finally:
        first.close()
    second = importer.State(state_path)
    try:
        second.bind("original destination and watch folder")
        with pytest.raises(ValueError, match="another destination/folder"):
            second.bind("different destination or watch folder")
        watcher = importer.FolderWatcher(folder, False, second, upload, settle_seconds=30)
        watcher.scan(now=100)
        watcher.scan(now=131)
        assert len(calls) == 2 and calls[0] == calls[1]
    finally:
        second.close()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for real audio conversion")
def test_cli_shares_content_history_across_folder_mtime_and_profile_case_changes(
    importer, client, tmp_path, monkeypatch,
):
    profile = client.post("/v1/profiles", json={"name": "Work"}).json()["id"]
    original = tmp_path / "original"
    original.mkdir()
    path = original / "meeting.wav"
    wav(path, struct.pack("<h", 1000) * 16000)
    os.utime(path, (AT.timestamp(), AT.timestamp()))
    uploaders, base = [], importer.Uploader

    def forbidden(*args, **kwargs):
        pytest.fail("A changed watch configuration must reuse acknowledged content history")

    def uploader_factory(server, target_profile, token, mtime_is_start):
        uploader = client_uploader(
            importer, client, base=base, profile=target_profile, mtime_is_start=mtime_is_start,
        )
        if uploaders:
            uploader.request = forbidden
        uploaders.append(uploader)
        return uploader

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(importer, "Uploader", uploader_factory)
    common = ["--server", "http://testserver", "--once", "--settle-seconds", "0"]
    previous_umask = os.umask(0o077)
    try:
        assert importer.main(common + ["--folder", str(original), "--profile", profile]) == 0
        part = uploaders[0].plans[0]["parts"][0]
        assert client.delete(f"/v1/audio/{part['id']}",
                             headers={"X-Omi-Profile": profile}).status_code == 200
        moved = original.rename(tmp_path / "moved")
        monkeypatch.setattr(importer, "convert", forbidden)
        assert importer.main(common + ["--folder", str(moved), "--profile", profile.upper(),
                                       "--mtime-is-start"]) == 0
        assert uploaders[1].profile == profile and not uploaders[1].calls
    finally:
        os.umask(previous_umask)


def wav(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        output.writeframes(samples)


def caf(path, samples):
    source = path.with_suffix(".wav")
    wav(source, samples)
    subprocess.run(
        [FFMPEG, "-v", "error", "-nostdin", "-i", str(source), "-c:a", "pcm_s16le",
         "-f", "caf", str(path)], check=True, capture_output=True,
    )
    source.unlink()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for real audio conversion")
def test_real_caf_conversion_keeps_speech_from_both_tracks(importer, tmp_path):
    system = tmp_path / "system_2026-10-15T12-00-00Z.caf"
    mic = tmp_path / "mic_2026-10-15T12-00-00Z.caf"
    caf(system, struct.pack("<h", 1000) * 16000 + b"\0\0" * 16000)
    caf(mic, b"\0\0" * 16000 + struct.pack("<h", 2000) * 16000)
    item = importer.discover(tmp_path, parrot=True)[0]
    output = tmp_path / "mixed.pcm"
    count = importer.convert(item, output)
    values = struct.unpack("<" + "h" * count, output.read_bytes())
    assert count == 32000
    assert sum(abs(v) for v in values[4000:12000]) / 8000 > 100
    assert sum(abs(v) for v in values[20000:28000]) / 8000 > 100
    assert system.exists() and mic.exists()


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "archive", worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as session:
        yield session


def client_uploader(importer, client, *, base=None, **options):
    class ClientUploader(base or importer.Uploader):
        def __init__(self):
            super().__init__("http://testserver", **options)
            self.calls, self.plans = [], []
            self.puts = 0
            self.fail_put = None

        def request(self, method, path, body=None, content=None, params=None):
            self.calls.append((method, path, body, params))
            if method == "PUT":
                self.puts += 1
                if self.puts == self.fail_put:
                    raise ValueError("interrupted connection")
            response = client.request(
                method, path, json=body, content=content, params=params,
                headers={"X-Omi-Profile": self.profile},
            )
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}: {response.text}")
            result = response.json()
            if path.endswith("/prepare"):
                self.plans.append(result)
            return result

    return ClientUploader()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for real audio conversion")
@pytest.mark.parametrize("mtime_is_start", [False, True])
def test_generic_mtime_is_end_unless_explicitly_overridden(
    importer, client, tmp_path, mtime_is_start,
):
    folder = tmp_path / "recordings"
    folder.mkdir()
    path = folder / "meeting.wav"
    wav(path, struct.pack("<h", 1000) * 16000)
    os.utime(path, (AT.timestamp(), AT.timestamp()))
    item = importer.discover(folder, parrot=False)[0]
    uploader = client_uploader(importer, client, mtime_is_start=mtime_is_start)
    assert uploader.upload(item, item.signature()) == "imported"
    plan = uploader.plans[0]
    start = AT if mtime_is_start else AT - timedelta(seconds=1)
    assert datetime.fromisoformat(plan["started_at"]) == start
    assert datetime.fromisoformat(plan["ended_at"]) == start + timedelta(seconds=1)
    row = client.app.state.memory.store.get_audio(plan["parts"][0]["id"])
    assert row["source_kind"] == "manual_upload"
    assert row["source_id"] == "meeting.wav"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for real audio conversion")
def test_real_upload_protocol_resumes_only_missing_parts_and_deduplicates(
    importer, client, state, tmp_path, monkeypatch,
):
    profile = client.post("/v1/profiles", json={"name": "Work"}).json()["id"]
    path = tmp_path / "meeting.wav"
    wav(path, struct.pack("<h", 1000) * (16000 * 121))
    item = recording(importer, path, vendor="parrot")
    signature = item.signature()
    uploader = client_uploader(importer, client, profile=profile)
    uploader.state = state
    digest = importer.file_digest(item)
    uploader.fail_put = 2
    with pytest.raises(ValueError, match="interrupted connection"):
        uploader.upload(item, signature)
    store = client.app.state.profiles.runtimes[profile].memory.store
    first = uploader.plans[0]
    assert store.get_audio(first["parts"][0]["id"]) is not None
    assert store.get_audio(first["parts"][1]["id"]) is None
    assert not state.has_digest(digest)

    assert uploader.upload(item, signature) == "imported"
    assert state.has_digest(digest)
    second = uploader.plans[1]
    assert [part["id"] for part in second["parts"]] == [
        part["id"] for part in first["parts"]
    ]
    assert [part["uploaded"] for part in second["parts"]] == [True, False]
    assert uploader.puts == 3
    assert client.get("/v1/status").json()["audio"] == {}
    assert client.get("/v1/status", headers={"X-Omi-Profile": profile}).json()["audio"] == {
        "queued": 2,
    }
    for index, entry in enumerate(second["parts"]):
        row = store.get_audio(entry["id"])
        metadata = json.loads(row["source_metadata"])
        assert metadata["vendor_id"] == "parrot"
        assert metadata["recording_id"] == second["recording_id"]
        assert metadata["part_index"] == index
        assert datetime.fromisoformat(row["started_at"]) == AT + timedelta(seconds=index * 120)

    def no_conversion(*args):
        pytest.fail("Acknowledged complete files must be skipped before conversion")

    monkeypatch.setattr(importer, "convert", no_conversion)
    assert uploader.upload(item, signature) == "duplicate"
    assert uploader.puts == 3
    assert path.exists()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required for real audio conversion")
@pytest.mark.parametrize("parrot,server_duplicate", [(False, False), (True, False), (False, True)])
def test_acknowledged_content_survives_renames_copies_touches_and_server_deletion(
    importer, client, tmp_path, monkeypatch, parrot, server_duplicate,
):
    original = tmp_path / "original"
    original.mkdir()
    if parrot:
        for role, level in (("system", 1000), ("mic", 2000)):
            caf(original / f"{role}_2026-10-15T12-00-00Z.caf", struct.pack("<h", level) * 16000)
    else:
        wav(original / "meeting.wav", struct.pack("<h", 1000) * 16000)
    item = importer.discover(original, parrot=parrot)[0]
    signature = item.signature()
    state_path = tmp_path / "state.sqlite3"
    first = importer.State(state_path)
    try:
        uploader = client_uploader(importer, client)
        if server_duplicate:
            assert uploader.upload(item, signature) == "imported"
            assert not first.has_digest(importer.file_digest(item))
        uploader.state = first
        expected = "duplicate" if server_duplicate else "imported"
        assert uploader.upload(item, signature) == expected
        assert first.has_digest(importer.file_digest(item))
        for part in uploader.plans[0]["parts"]:
            assert client.delete(f"/v1/audio/{part['id']}").status_code == 200
    finally:
        first.close()

    renamed = tmp_path / "renamed"
    copied = tmp_path / "copied"
    renamed.mkdir()
    copied.mkdir()
    for path in item.paths:
        name = path.name.replace("12-00-00Z", "12-00-01Z") if parrot else "renamed.wav"
        moved = path.rename(renamed / name)
        os.utime(moved, (AT.timestamp() + 60, AT.timestamp() + 60))
        copy_name = name.replace("12-00-01Z", "12-00-02Z") if parrot else "copy.wav"
        copy = Path(shutil.copyfile(moved, copied / copy_name))
        os.utime(copy, (AT.timestamp() + 120, AT.timestamp() + 120))

    def forbidden(*args, **kwargs):
        pytest.fail("Acknowledged bytes must never need another network request or conversion")

    monkeypatch.setattr(importer, "convert", forbidden)
    second = importer.State(state_path)
    try:
        uploader = client_uploader(importer, client)
        uploader.state = second
        monkeypatch.setattr(uploader, "request", forbidden)
        for folder in (renamed, copied):
            candidate = importer.discover(folder, parrot=parrot)[0]
            assert candidate.signature() != signature
            assert uploader.upload(candidate, candidate.signature()) == "duplicate"
            assert all(path.exists() for path in candidate.paths)
        assert not uploader.calls
    finally:
        second.close()


def test_script_runs_isolated_and_installs_a_standalone_command(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--help"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    assert "--parrot" in result.stdout and "--once" in result.stdout
    assert "--watch" in result.stdout
    target = tmp_path / "bin/daythread-import"
    subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--install", str(target)], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    assert target.read_bytes() == SCRIPT.read_bytes()
    assert os.access(target, os.X_OK)
    result = subprocess.run(
        [sys.executable, "-I", str(target), "--help"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    assert "--include-existing" in result.stdout


def test_once_dry_run_includes_existing_without_saved_receipts_or_uploads(tmp_path):
    folder = tmp_path / "recordings"
    path = file(folder, "meeting.wav")
    state_path = tmp_path / "state.sqlite3"
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--folder", str(folder), "--once", "--dry-run",
         "--settle-seconds", "0", "--state", str(state_path)], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    )
    assert "Would import meeting.wav" in result.stderr
    assert "1 imported/duplicate" in result.stderr
    assert not state_path.exists()
    assert path.read_bytes() == b"recording"


def test_watch_mode_polls_and_stops_cleanly_without_uploading(
    importer, tmp_path, monkeypatch, caplog,
):
    folder = tmp_path / "recordings"
    file(folder, "meeting.wav")
    ticks = []

    def interrupt_after_one_poll(seconds):
        ticks.append(seconds)
        if len(ticks) == 2:
            raise KeyboardInterrupt

    caplog.set_level("INFO", logger="daythread-import")
    monkeypatch.setattr(importer.time, "sleep", interrupt_after_one_poll)
    previous_umask = os.umask(0o077)
    try:
        assert importer.main([
            "--watch", "--dry-run", "--folder", str(folder), "--include-existing",
            "--settle-seconds", "0", "--poll-seconds", "2",
        ]) == 0
    finally:
        os.umask(previous_umask)
    assert ticks == [2, 2]
    assert "Would import meeting.wav" in caplog.text
    assert "Stopped" in caplog.text


def test_watch_and_once_are_mutually_exclusive(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--watch", "--once", "--dry-run"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_install_refuses_to_overwrite_an_unrelated_file(tmp_path):
    target = file(tmp_path, "existing-command", b"unrelated command")
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--install", str(target)], cwd=tmp_path,
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert target.read_bytes() == b"unrelated command"
