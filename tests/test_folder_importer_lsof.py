"""Writer checks retain lsof's diagnostics instead of retrying without an explanation."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def importer():
    path = Path(__file__).resolve().parents[1] / "scripts/import_recordings.py"
    spec = importlib.util.spec_from_file_location("daythread_importer_lsof_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status,diagnostic", [
    (1, b"lsof: status error on recording.caf: Permission denied\n"),
    (1, b"lsof: WARNING: cannot stat mounted filesystem; output may be incomplete\n"),
    (2, b"lsof: unsupported option\n"),
])
def test_failed_writer_check_reports_the_actual_error(importer, monkeypatch, status, diagnostic):
    monkeypatch.setattr(importer.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], status, b"", diagnostic))
    with pytest.raises(ValueError) as caught:
        importer.open_for_writing((Path("recording.caf"),))
    assert f"exit {status}" in str(caught.value)
    assert diagnostic.decode().strip() in str(caught.value)


@pytest.mark.parametrize("output,expected", [(b"", False), (b"p123\nf4\nar\n", False),
                                          (b"p123\nf4\naw\n", True),
                                          (b"p123\nf4\nau\n", True)])
def test_lsof_keeps_distinguishing_closed_readers_and_writers(
    importer, monkeypatch, output, expected,
):
    monkeypatch.setattr(importer.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0 if output else 1, output, b""))
    assert importer.open_for_writing((Path("recording.caf"),)) is expected


@pytest.mark.parametrize("writer", [False, True])
def test_unrelated_hfs_mount_warning_does_not_block_access_check(
    importer, monkeypatch, writer,
):
    def run(argv, **kwargs):
        diagnostic = (b"lsof: WARNING: can't stat() hfs file system /Volumes/Unavailable\n"
                      b"Output information may be incomplete.\n") if "-w" not in argv else b""
        return subprocess.CompletedProcess(argv, 0 if writer else 1,
                                           b"p123\nf4\naw\n" if writer else b"", diagnostic)

    monkeypatch.setattr(importer.subprocess, "run", run)
    assert importer.open_for_writing((Path("recording.caf"),)) is writer


@pytest.mark.skipif(not shutil.which("lsof"), reason="lsof is required")
def test_real_caf_writer_is_blocked_and_missing_file_still_fails(importer, tmp_path):
    path = tmp_path / "system_2026-11-06T09-00-00Z.caf"
    path.write_bytes(b"audio")
    assert not importer.open_for_writing((path,))
    with path.open("ab"):
        assert importer.open_for_writing((path,))
    assert not importer.open_for_writing((path,))
    with pytest.raises(ValueError, match="No such file"):
        importer.open_for_writing((tmp_path / "missing.caf",))
