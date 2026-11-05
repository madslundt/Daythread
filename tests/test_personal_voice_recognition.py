"""Personal assignment flow with synthetic voices; these tests measure no accuracy."""

import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient
from test_voice_suggestions import MODEL, detect_source, directory, runtime

from omi_local.api import create_app
from omi_local.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, worker_enabled=False, diarization_enabled=True,
                    _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def assign(client, path, speaker, data, **identity):
    response = client.post(f"{path}/{speaker}/remember", json={
        "version": data["version"], "auto_assign": True, **identity,
    })
    assert response.status_code == 200, response.text
    return response.json()


def preferences(client, *, enabled, auto_assign, revision=None, headers=None):
    current = client.get("/v1/voice-profiles", headers=headers).json()
    return client.patch("/v1/voice-profiles/preferences", headers=headers, json={
        "revision": revision or current["revision"], "enabled": enabled, "auto_assign": auto_assign,
    })


def test_one_assignment_enables_later_automatic_names_without_settings_or_report(client):
    before = directory(client)
    assert before["preferences"] == {"enabled": False, "auto_assign": False, "mode": "evaluated"}
    path, speaker, data = detect_source(client, 100)
    assigned = assign(client, path, speaker, data, name="Mads")
    assert assigned["speakers"][0]["name"] == "Mads"
    saved = directory(client)
    assert saved["preferences"] == {"enabled": True, "auto_assign": True, "mode": "personal"}
    assert saved["recognition"]["state"] == "ready"
    assert saved["profiles"][0]["example_count"] == saved["profiles"][0]["conversation_count"] == 1
    profile_id = saved["profiles"][0]["id"]
    path, _, next_recording = detect_source(client, 101)
    named = next_recording["speakers"][0]
    assert named["name"] == "Mads"
    assert named["automatic_match"] == {"profile_id": profile_id, "name": "Mads"}
    assert named["remembered_example_id"] is None
    assert client.get(path).json()["speakers"][0]["name"] == "Mads"
    assert directory(client) == saved


@pytest.mark.parametrize("case", ["unknown", "below_threshold", "ambiguous"])
def test_personal_matching_keeps_unknown_and_ambiguous_voices_anonymous(client, case):
    path, speaker, data = detect_source(client, 100)
    assign(client, path, speaker, data, name="Mads")
    if case == "ambiguous":
        path, speaker, data = detect_source(client, 101)
        assign(client, path, speaker, data, name="Similar voice")
    vector = {"unknown": (-1.0, 0.0), "below_threshold": (0.8, 0.6),
              "ambiguous": (1.0, 0.0)}[case]
    _, _, unknown = detect_source(client, 102, vector)
    assert unknown["speakers"][0]["name"] is None
    assert unknown["speakers"][0].get("automatic_match") is None
    assert unknown["speakers"][0]["suggestion"] is None


def test_personal_matching_uses_only_examples_from_the_exact_query_model(client):
    path, speaker, data = detect_source(client, 100)
    assign(client, path, speaker, data, name="Mads")
    second_model = {**MODEL, "revision": "b" * 64}
    path, speaker, data = detect_source(client, 101, (0.0, 1.0), second_model)
    assert data["speakers"][0]["name"] is None
    assign(client, path, speaker, data, name="Alice")
    _, _, old_model = detect_source(client, 102)
    _, _, new_model = detect_source(client, 103, (0.0, 1.0), second_model)
    assert old_model["speakers"][0]["name"] == "Mads"
    assert new_model["speakers"][0]["name"] == "Alice"
    _, _, unknown_model = detect_source(client, 104, model={**MODEL, "revision": "c" * 64})
    assert unknown_model["speakers"][0]["name"] is None


def test_preferences_support_suggestions_and_reject_stale_changes(client):
    path, speaker, data = detect_source(client, 100)
    assign(client, path, speaker, data, name="Mads")
    before = directory(client)
    response = preferences(client, enabled=True, auto_assign=False)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] != before["revision"]
    assert response.json()["preferences"] == {
        "enabled": True, "auto_assign": False, "mode": "personal",
    }
    rejected = preferences(client, enabled=False, auto_assign=False, revision=before["revision"])
    assert rejected.status_code == 409, rejected.text
    _, _, suggested = detect_source(client, 101)
    assert suggested["speakers"][0]["name"] is None
    assert suggested["speakers"][0]["suggestion"]["name"] == "Mads"


def test_disabled_preferences_survive_restart_and_are_isolated_per_archive(settings):
    with TestClient(create_app(settings)) as client:
        path, speaker, data = detect_source(client, 100)
        assign(client, path, speaker, data, name="Mads")
        other = client.post("/v1/profiles", json={"name": "Separate archive"})
        assert other.status_code == 201, other.text
        headers = {"X-Omi-Profile": other.json()["id"]}
        isolated = client.get("/v1/voice-profiles", headers=headers).json()
        assert isolated["profiles"] == []
        assert isolated["preferences"] == {
            "enabled": False, "auto_assign": False, "mode": "evaluated",
        }
        automatic_path, _, automatic = detect_source(client, 101)
        assert automatic["speakers"][0]["name"] == "Mads"
        disabled = preferences(client, enabled=False, auto_assign=False)
        assert disabled.status_code == 200, disabled.text
        saved = directory(client)
        assert client.get(automatic_path).json()["speakers"][0]["name"] == "Mads"
        _, _, next_recording = detect_source(client, 102)
        assert next_recording["speakers"][0]["name"] is None
        assert next_recording["recognition"]["state"] == "disabled"
    with TestClient(create_app(settings.model_copy(update={
        "voice_recognition_enabled": True, "voice_recognition_auto_assign": True,
    }))) as client:
        assert directory(client) == saved
        assert client.get(automatic_path).json()["speakers"][0]["automatic_match"]["name"] == "Mads"
        _, _, restarted = detect_source(client, 103)
        assert restarted["speakers"][0]["name"] is None
        assert restarted["recognition"]["state"] == "disabled"


@pytest.mark.parametrize("failure", ["bad_quality", "missing_embedding", "stale_evidence"])
def test_failed_assignment_does_not_enable_recognition_or_save_profiles(client, failure):
    path, speaker, data = detect_source(client, 100)
    before = directory(client)

    async def detector(audio, num_speakers=None):
        result = runtime((1.0, 0.0))
        if failure == "bad_quality":
            result["voices"][0]["clean_seconds"] = 5
        elif failure == "missing_embedding":
            result["voices"] = []
        return result

    client.app.state.memory.speakers.detect_turns = detector
    if failure == "stale_evidence":
        client.patch(f"{path}/{speaker}", json={"version": data["version"], "name": "Changed"})
    response = client.post(f"{path}/{speaker}/remember", json={
        "version": data["version"], "name": "Mads", "auto_assign": True,
    })
    assert response.status_code == 409, response.text
    assert directory(client) == before


@pytest.mark.parametrize("enabled,auto_assign", [(False, True), ("true", True), (True, 1)])
def test_preferences_validate_strict_booleans_and_require_recognition(client, enabled, auto_assign):
    before = directory(client)
    response = preferences(client, enabled=enabled, auto_assign=auto_assign)
    assert response.status_code == 422, response.text
    assert directory(client) == before


def test_assignment_rejects_non_boolean_opt_in(client):
    path, speaker, data = detect_source(client, 100)
    response = client.post(f"{path}/{speaker}/remember", json={
        "version": data["version"], "name": "Mads", "auto_assign": "true",
    })
    assert response.status_code == 422, response.text
    assert directory(client)["preferences"]["enabled"] is False


def test_archive_backup_preserves_personal_preferences_and_voice_examples(client, tmp_path):
    path, speaker, data = detect_source(client, 100)
    assign(client, path, speaker, data, name="Mads")
    before = directory(client)
    archive_path = client.app.state.memory.store.export_archive()
    restored_root = tmp_path / "restored"
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(restored_root)
        settings = Settings(data_dir=restored_root, worker_enabled=False, diarization_enabled=True,
                            _env_file=None)
        with TestClient(create_app(settings)) as restored:
            assert directory(restored) == before
            _, _, detected = detect_source(restored, 101)
            assert detected["speakers"][0]["name"] == "Mads"
    finally:
        shutil.rmtree(archive_path.parent)
