"""Hosted archives keep recordings; deployment policy survives saved/restored settings."""

import json
import runpy
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_backend import AT, wav

from omi_local.api import create_app
from omi_local.settings import RETENTION_FIELDS, Settings
from omi_local.store import Store


def settings(root, **kwargs):
    return Settings(data_dir=root, worker_enabled=False, _env_file=None, **kwargs)


def test_hosted_policy_is_selected_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OMI_MANAGED_HOSTING", "true")
    monkeypatch.setenv("OMI_AUDIO_RETENTION_DAYS", "7")
    monkeypatch.setenv("OMI_TRANSCRIPT_RETENTION_DAYS", "30")
    configured = settings(tmp_path / "archive")
    assert configured.managed_hosting
    assert configured.audio_retention_days == 0
    assert configured.transcript_retention_days == 0


def test_hosted_policy_overrides_saved_private_runtime_and_profile_retention(tmp_path):
    root = tmp_path / "archive"
    with TestClient(create_app(settings(root))) as client:
        profile_id = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
        assert client.patch("/v1/settings", json={
            "audio_retention_days": 1, "transcript_retention_days": 1,
        }).status_code == 200
    (root / ".private-settings.json").write_text(json.dumps({"overrides": {
        "managed_hosting": False, "audio_retention_days": 2, "transcript_retention_days": 2,
    }}))
    # Even archive settings from older or restored databases cannot override deployment mode.
    Store(root).save_settings({"managed_hosting": False})
    with TestClient(create_app(settings(root, managed_hosting=True))) as client:
        assert {key: client.get("/v1/settings").json()[key] for key in RETENTION_FIELDS} == {
            "audio_retention_days": 0, "transcript_retention_days": 0,
        }
        for profile in ("default", profile_id):
            runtime = client.app.state.profiles.runtimes[profile]
            assert runtime.memory.settings.managed_hosting
            assert runtime.memory.settings.audio_retention_days == 0
            assert runtime.memory.settings.transcript_retention_days == 0
            # The deployment policy overrides saved preferences without destroying them.
            assert all(
                runtime.memory.store.runtime_settings()[key] == 1 for key in RETENTION_FIELDS
            )
        new_id = client.post("/v1/profiles", json={"name": "New"}).json()["id"]
        assert client.app.state.profiles.runtimes[new_id].memory.settings.audio_retention_days == 0
    with TestClient(create_app(settings(root))) as client:
        value = client.get("/v1/settings").json()
        assert value["audio_retention_days"] == 1
        assert value["transcript_retention_days"] == 1


@pytest.mark.parametrize("route", ["/v1/settings", "/v1/configuration"])
@pytest.mark.parametrize("changes", [
    {"audio_retention_days": 7}, {"transcript_retention_days": 30},
    {"managed_hosting": False},
])
def test_hosted_policy_rejects_changes_atomically(tmp_path, route, changes):
    root = tmp_path / "archive"
    with TestClient(create_app(settings(root, managed_hosting=True))) as client:
        before = client.get("/v1/configuration").json()
        saved = client.app.state.memory.store.runtime_settings()
        changes = {"summary_language": "da", **changes}
        body = {"revision": before["revision"], "changes": changes} if route.endswith(
            "configuration"
        ) else changes
        assert client.patch(route, json=body).status_code == 422
        assert client.get("/v1/configuration").json() == before
        assert client.app.state.memory.store.runtime_settings() == saved
        assert not (root / ".private-settings.json").exists()
        assert "managed_hosting" not in before["values"]
        assert "managed_hosting" not in {field["key"] for field in before["fields"]}


def test_hosted_restored_recordings_do_not_expire(tmp_path):
    root = tmp_path / "archive"
    store = Store(root)
    key = uuid4()
    store.ingest(key, "omi", AT, wav([7] * 16000), 1)
    store.complete(str(key), "Keep this restored transcript.")
    store.save_settings({"audio_retention_days": 1, "transcript_retention_days": 1})
    snapshot = store.export_archive()
    restored = tmp_path / "restored"
    try:
        restore = runpy.run_path(str(Path(__file__).parents[1] / "scripts/archive.py"))["restore"]
        restore(snapshot, restored, 10_000_000)
    finally:
        shutil.rmtree(snapshot.parent)
    with TestClient(create_app(settings(restored, managed_hosting=True))) as client:
        memory = client.app.state.memory
        memory.store.apply_retention(
            memory.settings.audio_retention_days, memory.settings.transcript_retention_days,
            AT + timedelta(days=1000),
        )
        assert client.get(f"/v1/audio/{key}").status_code == 200
        assert client.get(f"/v1/sources/{key}").json()["text"] == "Keep this restored transcript."
        assert client.patch("/v1/settings", json={
            "audio_retention_days": 0, "transcript_retention_days": 0, "summary_language": "da",
        }).status_code == 200


def test_self_hosted_retention_remains_configurable(tmp_path):
    root = tmp_path / "archive"
    with TestClient(create_app(settings(root))) as client:
        value = client.get("/v1/settings").json()
        assert value["audio_retention_days"] == 7
        assert value["transcript_retention_days"] == 30
        assert client.patch("/v1/settings", json={
            "audio_retention_days": 90, "transcript_retention_days": 365,
        }).status_code == 200
    with TestClient(create_app(settings(root))) as client:
        value = client.get("/v1/settings").json()
        assert value["audio_retention_days"] == 90
        assert value["transcript_retention_days"] == 365
