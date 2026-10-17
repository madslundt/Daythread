"""Profiles isolate archives while client selection and service settings stay independent."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_backend import AT, wav

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.inference import Inference
from omi_local.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session


def create(client, name="Alice"):
    response = client.post("/v1/profiles", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, profile, key=None):
    return client.put(
        f"/v1/audio/{key or uuid4()}",
        params={"device_id": "omi", "started_at": stamp(AT)},
        headers={"X-Omi-Profile": profile, "Content-Type": "audio/wav"},
        content=wav([7] * 16000),
    )


def test_existing_archive_is_default_and_profiles_persist(settings):
    with TestClient(create_app(settings)) as client:
        source = upload(client, "default").json()
        profile = create(client, "  Alice  ")
        directory = client.get("/v1/profiles").json()
        assert directory == {
            "default_profile_id": "default",
            "profiles": [{"id": "default", "name": "Default"}, {"id": profile, "name": "Alice"}],
        }
        assert client.get("/v1/transcripts").json()["total"] == 1
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/profiles").json() == directory
        assert client.get(f"/v1/sources/{source['id']}").status_code == 200
        assert (
            client.get("/v1/transcripts", headers={"X-Omi-Profile": profile}).json()["total"] == 0
        )


def test_recordings_retries_corrections_and_deletion_are_profile_scoped(client):
    profile = create(client)
    key = uuid4()
    original = upload(client, "default", key).json()
    headers = {"X-Omi-Profile": profile}
    for path in (f"/v1/sources/{key}", f"/v1/audio/{key}", f"/v1/transcripts/{key}/revisions"):
        assert client.get(path, headers=headers).status_code == 404
    assert client.post(f"/v1/audio/{key}/retry", headers=headers).status_code == 404
    assert (
        client.patch(f"/v1/transcripts/{key}", json={"text": "other"}, headers=headers).status_code
        == 404
    )
    assert client.delete(f"/v1/audio/{key}", headers=headers).status_code == 404
    assert upload(client, profile, key).status_code == 200
    assert upload(client, profile, key).json()["source_version"] != original["source_version"]
    assert client.delete(f"/v1/audio/{key}", headers=headers).status_code == 200
    assert client.get(f"/v1/sources/{key}").status_code == 200


def test_resource_urls_and_selector_validation(client):
    profile = create(client)
    source = upload(client, profile).json()["id"]
    resource = f"/v1/audio/{source}?profile_id={profile}"
    assert client.get(resource).status_code == 200
    assert client.get(f"/v1/audio/{source}").status_code == 404
    assert client.get(resource, headers={"X-Omi-Profile": "default"}).status_code == 422
    assert client.get("/v1/status?profile_id=../../other").status_code == 404
    assert client.get("/v1/status?profile_id=default&profile_id=default").status_code == 422
    assert client.get("/v1/status", headers={"X-Omi-Profile": ""}).status_code == 404
    assert client.get("/v1/status", headers=[("X-Omi-Profile", "default")] * 2).status_code == 422


def test_labels_importance_and_meetings_use_selected_archive(client):
    profile = create(client)
    headers = {"X-Omi-Profile": profile}
    source = upload(client, profile).json()["id"]
    mark = {"kind": "source", "id": source, "important": True}
    assert client.put("/v1/importance", headers=headers, json=mark).status_code == 200
    assert client.get("/v1/importance", headers=headers).json()["items"]
    assert client.get("/v1/importance").json()["items"] == []
    assert client.put("/v1/importance", json=mark).status_code == 404
    assert client.get(f"/v1/labels/sources/{source}", headers=headers).status_code == 200
    assert client.get(f"/v1/labels/sources/{source}").status_code == 404
    event = {
        "event_id": str(uuid4()),
        "device_id": "omi",
        "occurred_at": stamp(AT),
        "action": "mark",
    }
    assert client.post("/v1/events", headers=headers, json=event).status_code == 200
    assert len(client.get("/v1/timeline", headers=headers).json()["markers"]) == 1
    assert client.get("/v1/timeline").json()["markers"] == []


def test_settings_and_live_configuration_are_global_and_persist(settings):
    with TestClient(create_app(settings)) as client:
        profile = create(client)
        headers = {"X-Omi-Profile": profile}
        assert (
            client.patch(
                "/v1/settings", headers=headers, json={"summary_language": "da"}
            ).status_code
            == 200
        )
        assert client.get("/v1/settings").json()["summary_language"] == "da"
        revision = client.get("/v1/configuration").json()["revision"]
        response = client.patch(
            "/v1/configuration",
            headers=headers,
            json={
                "revision": revision,
                "changes": {"summary_language": "en", "stt_model": "shared"},
            },
        )
        assert response.status_code == 200
        create(client, "Bob")
        for runtime in client.app.state.profiles.runtimes.values():
            assert runtime.memory.settings.summary_language == "en"
            assert runtime.memory.inference.settings.stt_model == "shared"
            assert runtime.memory.store.runtime_settings()["summary_language"] == "en"
        # A missing saved profile never blocks the global chooser or settings.
        stale = {"X-Omi-Profile": str(uuid4())}
        assert client.get("/v1/profiles", headers=stale).status_code == 200
        assert client.get("/v1/settings", headers=stale).json()["summary_language"] == "en"
    with TestClient(create_app(settings)) as client:
        for profile in client.get("/v1/profiles").json()["profiles"]:
            result = client.get(
                "/v1/configuration", headers={"X-Omi-Profile": profile["id"]}
            ).json()
            assert result["values"]["stt_model"] == "shared"
            assert result["values"]["summary_language"] == "en"


def test_concurrent_clients_do_not_change_each_others_selection(client):
    profiles = ["default", create(client)]
    keys = [str(uuid4()), str(uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda pair: upload(client, *pair), zip(profiles, keys)))
    assert all(result.status_code == 200 for result in responses)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda profile: client.get("/v1/transcripts", headers={"X-Omi-Profile": profile}),
                profiles * 5,
            )
        )
    for index, response in enumerate(responses):
        assert [item["id"] for item in response.json()["items"]] == [keys[index % 2]]
        assert response.headers["X-Omi-Profile"] == profiles[index % 2]
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, "Alice\nBob"])
def test_invalid_names_are_rejected(client, name):
    assert client.post("/v1/profiles", json={"name": name}).status_code == 422


def test_duplicate_profile_names_are_rejected_without_extra_archives(client):
    create(client)
    assert client.post("/v1/profiles", json={"name": " aLiCe "}).status_code == 409
    assert len(client.get("/v1/profiles").json()["profiles"]) == 2


def test_renaming_preserves_archive_identity_and_survives_restart(settings):
    with TestClient(create_app(settings)) as client:
        profile = create(client)
        source = upload(client, profile).json()["id"]
        response = client.patch(f"/v1/profiles/{profile}", json={"name": "  Work  "})
        assert response.status_code == 200
        assert response.json() == {"id": profile, "name": "Work"}
        # Reusing the same name and renaming the original archive are both supported.
        assert client.patch(f"/v1/profiles/{profile}", json={"name": "Work"}).status_code == 200
        assert client.patch("/v1/profiles/default", json={"name": "Personal"}).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/profiles").json() == {
            "default_profile_id": "default",
            "profiles": [{"id": "default", "name": "Personal"}, {"id": profile, "name": "Work"}],
        }
        assert client.get(f"/v1/sources/{source}?profile_id={profile}").status_code == 200
        assert client.get(f"/v1/sources/{source}").status_code == 404


def test_profile_edits_validate_names_and_leave_conflicts_unchanged(client):
    profile = create(client)
    for name in ("", "   ", "x" * 81, "Alice\nBob"):
        assert client.patch(f"/v1/profiles/{profile}", json={"name": name}).status_code == 422
    assert client.patch(f"/v1/profiles/{profile}", json={"name": " dEfAuLt "}).status_code == 409
    assert client.patch("/v1/profiles/missing", json={"name": "Work"}).status_code == 404
    assert client.get("/v1/profiles").json()["profiles"][1] == {"id": profile, "name": "Alice"}


def test_profile_management_is_global_even_with_a_stale_selection(client):
    profile = create(client)
    response = client.patch(
        f"/v1/profiles/{profile}", json={"name": "Work"}, headers={"X-Omi-Profile": "missing"}
    )
    assert response.status_code == 200
    # Removal is intentionally unsupported.
    assert client.delete(f"/v1/profiles/{profile}").status_code == 405
    assert len(client.get("/v1/profiles").json()["profiles"]) == 2


def test_streaming_chat_history_stays_with_its_profile(client):
    profile = create(client)
    headers = {"X-Omi-Profile": profile}
    response = client.post(
        "/v1/chat/stream", headers=headers, json={"question": "Any recorded evidence?"}
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    result = next(event["result"] for event in events if event["type"] == "done")
    session = result["session_id"]
    assert client.get(f"/v1/chat/sessions/{session}", headers=headers).status_code == 200
    assert client.get(f"/v1/chat/sessions/{session}").status_code == 404
    assert client.get("/v1/chat/sessions").json()["items"] == []
    assert len(client.get("/v1/chat/sessions", headers=headers).json()["items"]) == 1


def test_workers_process_unselected_profiles_and_stop_cleanly(settings, monkeypatch):
    async def transcribe(self, data, on_progress=None):
        return {"text": "Captured speech", "segments": []}

    monkeypatch.setattr(Inference, "transcribe_result", transcribe)
    settings.worker_enabled = True
    settings.silence_threshold = 0
    with TestClient(create_app(settings)) as client:
        profile = create(client)
        source = upload(client, profile).json()["id"]
        default_source = upload(client, "default").json()["id"]
        for _ in range(100):
            state = client.get(f"/v1/sources/{source}?profile_id={profile}").json()["state"]
            default_state = client.get(f"/v1/sources/{default_source}").json()["state"]
            if state == default_state == "done":
                break
            time.sleep(0.05)
        assert state == default_state == "done"
        tasks = [
            task
            for runtime in client.app.state.profiles.runtimes.values()
            for task in (runtime.worker, runtime.weekly_worker)
        ]
    assert all(task.done() for task in tasks)
