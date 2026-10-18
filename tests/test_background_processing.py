"""Recovery exports must not resume or rewrite durable background work."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_backend import AT, wav

from omi_local.api import create_app
from omi_local.generation_jobs import SCHEMA as JOB_SCHEMA
from omi_local.settings import Settings
from omi_local.store import Store


def test_background_policy_is_environment_only_and_default_enabled(tmp_path, monkeypatch):
    assert Settings(data_dir=tmp_path, _env_file=None).background_processing_enabled
    monkeypatch.setenv("OMI_BACKGROUND_PROCESSING_ENABLED", "false")
    settings = Settings(data_dir=tmp_path, _env_file=None)
    assert not settings.background_processing_enabled
    (tmp_path / ".private-settings.json").write_text(
        json.dumps(
            {
                "overrides": {
                    "background_processing_enabled": True,
                }
            }
        )
    )
    with TestClient(create_app(settings)) as client:
        assert not client.app.state.memory.settings.background_processing_enabled
        assert (
            "background_processing_enabled" not in client.get("/v1/configuration").json()["values"]
        )
        response = client.patch("/v1/settings", json={"background_processing_enabled": True})
        assert response.status_code in (400, 422)


def test_recovery_loads_all_profiles_without_workers_or_job_state_changes(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    with TestClient(
        create_app(
            Settings(
                data_dir=root,
                worker_enabled=False,
                _env_file=None,
            )
        )
    ) as setup:
        profile = setup.post("/v1/profiles", json={"name": "Earlier profile"}).json()["id"]
    stores = [Store(root), Store(root / "profiles" / profile)]
    identities = []
    for index, store in enumerate(stores):
        identity = uuid4()
        identities.append(identity)
        store.ingest(identity, "omi", AT, wav([7] * 16000), 1)
        store.save_settings({"summary_language": "da" if index == 0 else "de"})
        with store.connect() as db:
            db.executescript(JOB_SCHEMA)
            db.execute("UPDATE audio SET state='transcribing' WHERE id=?", (str(identity),))
            db.execute(
                "INSERT INTO generation_jobs(id,kind,parameters,state,created_at) "
                "VALUES(?,'diary','{}','running',?)",
                (str(uuid4()), AT.isoformat()),
            )

    def forbidden(*args, **kwargs):
        pytest.fail("Opening a recovery archive must not mutate startup state")

    monkeypatch.setattr(Store, "recover", forbidden)
    monkeypatch.setattr(Store, "save_settings", forbidden)
    monkeypatch.setattr(Store, "save_decision_timezone", forbidden)
    monkeypatch.setattr(Store, "configure_embeddings", forbidden)
    settings = Settings(
        data_dir=root,
        worker_enabled=True,
        managed_hosting=True,
        background_processing_enabled=False,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        status = client.get("/v1/status")
        assert status.status_code == 200
        assert status.json()["weekly_worker_running"] is False
        assert status.json()["worker_running"] is False
        runtimes = client.app.state.profiles.runtimes
        assert set(runtimes) == {"default", profile}
        for runtime in runtimes.values():
            assert all(
                task is None
                for task in (
                    runtime.worker,
                    runtime.embedding_worker,
                    runtime.weekly_worker,
                    runtime.generation_worker,
                    runtime.notification_worker,
                )
            )
        assert client.get("/v1/archive/export").status_code == 200
        for index, store in enumerate(stores):
            assert store.get_audio(str(identities[index]))["state"] == "transcribing"
            assert store.runtime_settings()["summary_language"] == ("da" if index == 0 else "de")
            with store.connect() as db:
                assert db.execute("SELECT state FROM generation_jobs").fetchone()[0] == "running"
