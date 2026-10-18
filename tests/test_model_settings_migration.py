"""Model .env cleanup preserves effective settings, credentials and unrelated entries."""

import json
import stat
from pathlib import Path

import pytest

from omi_local.configuration import Configuration
from omi_local.settings import Settings
from scripts import migrate_model_settings as migration


@pytest.fixture(autouse=True)
def isolate_archive_environment(monkeypatch):
    monkeypatch.delenv("OMI_DATA_DIR", raising=False)


def test_migration_preserves_saved_values_nested_keys_and_unrelated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    unrelated = 'OMI_DATA_DIR=archive\n# keep this comment\nOTHER="a\nb"\n'
    env.write_text(
        unrelated + "export OMI_STT_MODEL=old-speech\n"
        "OMI_STT_BASE_URL=https://speech.example.com/v1\n"
        "OMI_STT_API_KEY=speech-secret\n"
        "OMI_LLM_MODEL=old-chat\n"
        "OMI_LLM_ROUTES__CHAT__TOKEN=route-secret\n"
        'OMI_STT_ROUTES=\'{\n"da": {"model": "danish"}\n}\'\n'
    )
    before = Settings(_env_file=env)
    configuration = Configuration(before)
    saved = configuration.prepare(before, {"llm_model": "saved-chat", "llm_api_key": "saved-key"})
    configuration.save(saved, ["llm_model", "llm_api_key"])
    expected = configuration.startup(before)
    assert migration.migrate(env) == 6
    assert env.read_text() == unrelated
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    after = Settings(_env_file=env)
    actual = Configuration(after).startup(after)
    assert actual.model_dump() == expected.model_dump()
    assert actual.llm_route("chat").token.get_secret_value() == "route-secret"
    assert actual.stt_route("en").token.get_secret_value() == "speech-secret"
    assert stat.S_IMODE(configuration.path.stat().st_mode) == 0o600
    assert migration.migrate(env) == 0
    assert not (tmp_path / "archive/omi.sqlite3").exists()


@pytest.mark.parametrize(
    "source",
    [
        'OMI_STT_MODEL="unterminated\n',
        "OMI_LLM_UNKNOWN=value\n",
        "OMI_LLM_MODEL=chat\nOTHER=${OMI_LLM_MODEL}\n",
    ],
)
def test_invalid_or_referenced_variables_leave_both_sources_untouched(
    tmp_path, monkeypatch, source
):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(source)
    with pytest.raises(ValueError):
        migration.migrate(env)
    assert env.read_text() == source
    assert not (tmp_path / "data/.private-settings.json").exists()


def test_failed_env_replacement_keeps_original_env_and_persisted_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    source = "OMI_LLM_MODEL=chat\nOMI_LLM_API_KEY=private-key\n"
    env.write_text(source)
    original_replace = migration.os.replace

    def replace(source, target):
        if Path(target) == env:
            raise OSError("Cannot replace environment file")
        original_replace(source, target)

    monkeypatch.setattr(migration.os, "replace", replace)
    with pytest.raises(OSError):
        migration.migrate(env)
    assert env.read_text() == source
    saved = json.loads((tmp_path / "data/.private-settings.json").read_text())["overrides"]
    assert saved["llm_model"] == "chat"
    assert saved["llm_api_key"] == "private-key"
    assert not list(tmp_path.glob(".env-models-*"))
