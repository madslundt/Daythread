"""The standalone importer loads private defaults without executing shell input."""

import importlib.util
import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/import_recordings.py"
PROFILE = "17741b4f-289e-418f-a280-e08b97287872"


@pytest.fixture
def importer(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("daythread_importer_env_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for key in ("DAYTHREAD_SERVER_URL", "DAYTHREAD_PROFILE", "DAYTHREAD_IMPORT_FOLDER",
                "DAYTHREAD_IMPORT_TOKEN", "DAYTHREAD_INSECURE", "SSL_CERT_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return module


def test_env_quotes_comments_exports_and_shell_text_are_literal(importer, tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        '\ufeff# Private importer defaults\nexport DAYTHREAD_SERVER_URL = "https://saved.example"\n'
        "DAYTHREAD_IMPORT_TOKEN='a # secret = $(touch unexpected)' # comment\n"
        'DAYTHREAD_IMPORT_FOLDER="~/My Recordings"\n', encoding="utf-8",
    )
    env = importer.read_environment(path)
    assert env["DAYTHREAD_SERVER_URL"] == "https://saved.example"
    assert env["DAYTHREAD_IMPORT_TOKEN"] == "a # secret = $(touch unexpected)"
    assert env["DAYTHREAD_IMPORT_FOLDER"] == "~/My Recordings"
    assert "DAYTHREAD_SERVER_URL" not in os.environ
    assert not (tmp_path / "unexpected").exists()


def test_exported_variables_override_file_defaults(importer, tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DAYTHREAD_SERVER_URL=https://file.example\nDAYTHREAD_IMPORT_TOKEN=file\n")
    monkeypatch.setenv("DAYTHREAD_SERVER_URL", "https://exported.example")
    monkeypatch.setenv("DAYTHREAD_IMPORT_TOKEN", "exported")
    env = importer.read_environment(path)
    assert env["DAYTHREAD_SERVER_URL"] == "https://exported.example"
    assert env["DAYTHREAD_IMPORT_TOKEN"] == "exported"


def test_missing_automatic_env_is_optional(importer, tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "__file__", str(tmp_path / "import_recordings.py"))
    monkeypatch.setenv("DAYTHREAD_SERVER_URL", "https://exported.example")
    assert importer.read_environment()["DAYTHREAD_SERVER_URL"] == "https://exported.example"


@pytest.mark.parametrize("line", ["not an assignment", "DAYTHREAD_IMPORT_TOKEN='private-value"])
def test_invalid_env_reports_location_without_disclosing_contents(importer, tmp_path, line):
    path = tmp_path / ".env"
    path.write_text("# comment\n" + line + "\n")
    with pytest.raises(ValueError, match=r"\.env:2") as caught:
        importer.read_environment(path)
    assert line not in str(caught.value)
    assert "private-value" not in str(caught.value)


def test_explicit_missing_env_file_does_not_silently_fall_back(importer, tmp_path):
    (tmp_path / ".env").write_text("DAYTHREAD_SERVER_URL=https://other.example\n")
    with pytest.raises(FileNotFoundError):
        importer.read_environment(tmp_path / "missing.env")


def test_main_uses_saved_server_profile_token_and_folder(importer, tmp_path, monkeypatch):
    folder = tmp_path / "Saved Recordings"
    folder.mkdir()
    (folder / "meeting.wav").write_bytes(b"original")
    (tmp_path / ".env").write_text(
        f'DAYTHREAD_SERVER_URL=https://saved.example\nDAYTHREAD_PROFILE={PROFILE}\n'
        f'DAYTHREAD_IMPORT_FOLDER="{folder}"\nDAYTHREAD_IMPORT_TOKEN=private-token\n'
    )
    uploaders = []
    original = importer.Uploader

    def capture(*args):
        uploader = original(*args)
        uploaders.append(uploader)
        return uploader

    monkeypatch.setattr(importer, "Uploader", capture)
    previous_umask = os.umask(0o077)
    try:
        assert importer.main(["--once", "--dry-run", "--settle-seconds", "0"]) == 0
    finally:
        os.umask(previous_umask)
    assert (uploaders[0].server, uploaders[0].profile, uploaders[0].token) == (
        "https://saved.example", PROFILE, "private-token",
    )
    assert "DAYTHREAD_IMPORT_TOKEN" not in os.environ


def test_standalone_env_discovery_and_cli_overrides(tmp_path):
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    command = downloaded / "import_recordings.py"
    shutil.copyfile(SCRIPT, command)
    recordings = tmp_path / "Saved Recordings"
    recordings.mkdir()
    (recordings / "meeting.wav").write_bytes(b"original")
    (downloaded / ".env").write_text(
        f'DAYTHREAD_SERVER_URL=https://saved.example\nDAYTHREAD_PROFILE={PROFILE}\n'
        f'DAYTHREAD_IMPORT_FOLDER="{recordings}"\n'
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("DAYTHREAD_")}
    argv = [sys.executable, "-I", str(command), "--once", "--dry-run", "--settle-seconds", "0"]
    result = subprocess.run(argv, cwd=elsewhere, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Would import meeting.wav" in result.stderr
    assert PROFILE in result.stderr
    (elsewhere / ".env").write_text("DAYTHREAD_PROFILE=default\nDAYTHREAD_SERVER_URL=invalid\n")
    result = subprocess.run(argv + ["--server", "https://cli.example", "--folder", str(recordings)],
                            cwd=elsewhere, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "destination profile default" in result.stderr
    result = subprocess.run(argv + ["--env-file", str(downloaded / ".env"), "--profile", "default"],
                            cwd=elsewhere, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Would import meeting.wav" in result.stderr
    assert "destination profile default" in result.stderr


def test_explicit_parrot_mode_overrides_saved_generic_folder(
    importer, tmp_path, monkeypatch, caplog,
):
    (tmp_path / ".env").write_text(f'DAYTHREAD_IMPORT_FOLDER="{tmp_path}"\n')
    home = tmp_path / "home"
    monkeypatch.setattr(importer.Path, "home", lambda: home)
    caplog.set_level("INFO", logger="daythread-import")
    previous_umask = os.umask(0o077)
    try:
        assert importer.main(["--parrot", "--once", "--dry-run", "--settle-seconds", "0"]) == 0
    finally:
        os.umask(previous_umask)
    assert str(home / "Library/Application Support/Parrot/Audio") in caplog.text


def test_private_ca_path_from_env_configures_https(importer, tmp_path, monkeypatch):
    ca = tmp_path / "private CA.crt"
    (tmp_path / ".env").write_text(f'SSL_CERT_FILE="{ca}"\n')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    paths = []

    def load_context(*, cafile):
        paths.append(cafile)
        return context

    monkeypatch.setattr(importer.ssl, "create_default_context", load_context)
    previous_umask = os.umask(0o077)
    try:
        assert importer.main([
            "--folder", str(tmp_path / "empty"), "--once", "--dry-run", "--settle-seconds", "0",
        ]) == 0
    finally:
        os.umask(previous_umask)
    assert paths == [ca]
