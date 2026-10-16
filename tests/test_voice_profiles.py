"""Only current, explicitly confirmed voices may influence identity suggestions."""

import io
import json
import wave
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import utc_now
from omi_local.settings import Settings
from omi_local.store import Store

MODEL = {"id": "community-1-test", "revision": "a" * 64,
         "dimension": 2, "normalization": "l2-v1"}


def wav(seconds=20, sample=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * round(seconds * 16000))
    return output.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(
        data_dir=tmp_path, worker_enabled=False, diarization_enabled=True,
        voice_recognition_enabled=True, _env_file=None,
    ))) as client:
        yield client


def result(vector=(1.0, 0.0), *, seconds=15, overlap=0, clipped=0, model=None):
    return {"model": "community-1", "embedding_model": model or MODEL,
            "turns": [{"start": 0, "end": 20, "speaker": "a"}],
            "voices": [{"speaker": "a", "embedding": list(vector),
                        "clean_seconds": seconds, "overlap_ratio": overlap,
                        "clipped_ratio": clipped}]}


def scope(client, vector=(1.0, 0.0), *, callback=None, response=None):
    memory = client.app.state.memory
    key = str(uuid4())
    memory.store.ingest(key, "manual-web", utc_now() - timedelta(hours=1),
                        wav(sample=int(key[:4], 16) % 30000 + 1), 20,
                        source_kind="manual_upload", source_id=key + ".wav")
    memory.store.complete(key, "A confirmed voice")

    async def detect(data, num_speakers=None):
        if callback:
            callback()
        return response or result(vector)

    memory.speakers.detect_turns = detect
    path = f"/v1/speakers/source/{key}"
    detected = client.post(path)
    assert detected.status_code == 200, detected.text
    speaker = detected.json()["speakers"][0]
    return key, path, speaker["id"], detected.json()


def remember(client, path, speaker, data, **identity):
    return client.post(f"{path}/{speaker}/remember", json={"version": data["version"], **identity})


def directory(client):
    response = client.get("/v1/voice-profiles")
    assert response.status_code == 200, response.text
    return response.json()


def test_detection_and_plain_names_never_train_and_suggestions_start_unvalidated(client):
    _, path, speaker, data = scope(client)
    assert data["recognition"]["state"] == "unvalidated"
    assert data["speakers"][0]["suggestion"] is None
    client.patch(f"{path}/{speaker}", json={"version": data["version"], "name": "Mads"})
    assert directory(client)["profiles"] == []
    assert client.get(path).json()["speakers"][0]["voice_profile_id"] is None
    assert client.post(f"{path}/{speaker}/confirm", json={
        "version": client.get(path).json()["version"], "profile_id": str(uuid4()),
    }).status_code == 404


def test_explicit_remember_creates_persistent_identity_and_clean_example(client):
    key, path, speaker, data = scope(client)
    response = remember(client, path, speaker, data, name="Mads")
    assert response.status_code == 200, response.text
    named = response.json()["speakers"][0]
    assert named["name"] == "Mads"
    assert named["voice_profile_id"]
    assert named["remembered_example_id"]
    voices = directory(client)
    profile = voices["profiles"][0]
    assert profile["id"] == named["voice_profile_id"]
    assert profile["conversation_count"] == profile["example_count"] == 1
    assert profile["examples"][0]["scope"] == {"kind": "source", "id": key}
    assert "embedding" not in json.dumps(voices)
    assert "path" not in json.dumps(voices)
    with Store(client.app.state.memory.store.root).connect() as db:
        assert db.execute("SELECT count(*) FROM voice_examples").fetchone()[0] == 1
        vector = json.loads(db.execute("SELECT embedding FROM voice_examples").fetchone()[0])
        assert vector == [1.0, 0.0]


def test_existing_profile_explicitly_accumulates_independent_conversations(client):
    _, path, speaker, data = scope(client)
    named = remember(client, path, speaker, data, name="Mads").json()
    profile_id = named["speakers"][0]["voice_profile_id"]
    _, second, other, data = scope(client)
    response = remember(client, second, other, data, profile_id=profile_id)
    assert response.status_code == 200, response.text
    assert directory(client)["profiles"][0]["conversation_count"] == 2
    _, third, another, data = scope(client)
    assert client.get(third).json()["speakers"][0]["suggestion"] is None
    assert directory(client)["recognition"]["state"] == "unvalidated"
    # Identical freeform names cannot silently merge different identities.
    assert remember(client, third, another, data, name="Mads").status_code == 200
    assert len(directory(client)["profiles"]) == 2


@pytest.mark.parametrize("change", ["correct", "expire", "audio_only", "delete", "rename",
                                    "redetect", "reassign"])
def test_learning_follows_source_lifetime_and_manual_corrections(client, change):
    key, path, speaker, data = scope(client)
    saved = remember(client, path, speaker, data, name="Mads").json()
    store = client.app.state.memory.store
    if change == "correct":
        store.correct_transcript(key, "Different words")
    elif change in {"expire", "audio_only"}:
        store.apply_retention(1, 1 if change == "expire" else 0,
                              utc_now() + timedelta(days=2))
    elif change == "delete":
        store.delete_audio(key)
    elif change == "rename":
        client.patch(f"{path}/{speaker}", json={"version": saved["version"], "name": "Lars"})
    elif change == "redetect":
        assert client.post(path, json={"reset": True}).status_code == 200
    else:
        turn = saved["turns"][0]["id"]
        client.patch(f"{path}/turns/{turn}", json={
            "version": saved["version"], "speaker_id": speaker,
        })
    assert directory(client)["profiles"][0]["example_count"] == 0
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM voice_example_sources").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM voice_events").fetchone()[0] == 0


def test_forgetting_examples_and_people_preserves_conversation_names(client):
    _, path, speaker, data = scope(client)
    saved = remember(client, path, speaker, data, name="Mads").json()
    profile_id = saved["speakers"][0]["voice_profile_id"]
    voices = directory(client)
    example_id = voices["profiles"][0]["examples"][0]["id"]
    assert client.delete(f"/v1/voice-profiles/{profile_id}?revision=old").status_code == 409
    deleted = client.delete(f"/v1/voice-profiles/{profile_id}/examples/{example_id}",
                            params={"revision": voices["revision"]})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["profiles"][0]["example_count"] == 0
    assert client.get(path).json()["speakers"][0]["name"] == "Mads"
    deleted = client.delete(f"/v1/voice-profiles/{profile_id}",
                            params={"revision": deleted.json()["revision"]})
    assert deleted.status_code == 200
    assert deleted.json()["profiles"] == []
    assert client.get(path).json()["speakers"][0]["voice_profile_id"] is None


@pytest.mark.parametrize("response", [
    result(seconds=9), result(overlap=0.11), result(clipped=0.02),
    result(vector=(0.0, 0.0)), result(vector=(2.0, 0.0)),
    result(vector=(float("nan"), 0.0)), result(vector=(True, 0.0)),
    result(model={**MODEL, "revision": "latest"}),
    {"turns": [{"start": 0, "end": 20, "speaker": "a"}], "model": "old-service"},
])
def test_unusable_voice_quality_or_unsupported_service_never_learns(client, response):
    _, path, speaker, data = scope(client, response=response)
    rejected = remember(client, path, speaker, data, name="Mads")
    assert rejected.status_code == 409, rejected.text
    assert directory(client)["profiles"] == []


def test_disabled_learning_cannot_write_but_can_still_forget(client):
    _, path, speaker, data = scope(client)
    saved = remember(client, path, speaker, data, name="Mads").json()
    client.app.state.memory.settings.voice_recognition_enabled = False
    assert client.get(path).json()["recognition"]["state"] == "disabled"
    assert remember(client, path, speaker, saved, name="Other").status_code == 409
    voices = directory(client)
    profile_id = voices["profiles"][0]["id"]
    assert client.delete(f"/v1/voice-profiles/{profile_id}",
                         params={"revision": voices["revision"]}).status_code == 200


def test_saved_voices_are_isolated_between_archives(client):
    _, path, speaker, data = scope(client)
    remember(client, path, speaker, data, name="Mads")
    profile = client.post("/v1/profiles", json={"name": "Other archive"}).json()["id"]
    response = client.get("/v1/voice-profiles", headers={"x-omi-profile": profile})
    assert response.json()["profiles"] == []
    assert client.get(path, headers={"x-omi-profile": profile}).status_code == 404


@pytest.mark.parametrize("change", ["correct", "delete", "retention", "disabled"])
def test_inflight_enrollment_cannot_restore_invalid_examples(client, change):
    key, path, speaker, data = scope(client)
    memory = client.app.state.memory

    async def detect(audio, num_speakers=None):
        if change == "correct":
            memory.store.correct_transcript(key, "Changed during learning")
        elif change == "delete":
            memory.store.delete_audio(key)
        elif change == "retention":
            memory.store.apply_retention(1, 0, utc_now() + timedelta(days=2))
        else:
            memory.settings.voice_recognition_enabled = False
        return result()

    memory.speakers.detect_turns = detect
    assert remember(client, path, speaker, data, name="Mads").status_code == 409
    assert directory(client)["profiles"] == []


def test_fake_or_missing_evaluation_report_cannot_enable_suggestions(client, tmp_path):
    _, path, speaker, data = scope(client)
    remember(client, path, speaker, data, name="Mads")
    report = tmp_path / "evaluation.json"
    client.app.state.memory.settings.voice_recognition_report = report
    assert directory(client)["recognition"]["state"] == "unvalidated"
    report.write_text(json.dumps({"passed": True, "precision": 1.0, "threshold": 0.5}))
    assert directory(client)["recognition"]["state"] == "unvalidated"


def test_request_requires_one_explicit_identity_and_current_version(client):
    _, path, speaker, data = scope(client)
    for identity in ({}, {"name": "Mads", "profile_id": str(uuid4())},
                     {"name": " "}, {"name": "Bad\nName"}):
        assert remember(client, path, speaker, data, **identity).status_code == 422
    assert client.post(f"{path}/{speaker}/remember", json={
        "version": "outdated", "name": "Mads",
    }).status_code == 409


@pytest.mark.parametrize("bad_turn", [
    {"start": -1, "end": 20, "speaker": "a"},
    {"start": 10, "end": 9, "speaker": "a"},
    {"start": 0, "end": 40, "speaker": "a"},
    {"start": float("nan"), "end": 20, "speaker": "a"},
    {"start": 0, "end": "invalid", "speaker": "a"},
])
def test_invalid_enrollment_timing_fails_without_learning(client, bad_turn):
    _, path, speaker, data = scope(client)

    async def detect(audio, num_speakers=None):
        return {**result(), "turns": [bad_turn]}

    client.app.state.memory.speakers.detect_turns = detect
    assert remember(client, path, speaker, data, name="Mads").status_code == 502
    assert directory(client)["profiles"] == []


def test_impossible_clean_duration_and_mixed_voice_sample_never_learn(client):
    _, path, speaker, data = scope(client)

    async def detect(audio, num_speakers=None):
        return result(seconds=1000)

    client.app.state.memory.speakers.detect_turns = detect
    assert remember(client, path, speaker, data, name="Mads").status_code == 409

    async def mixed(audio, num_speakers=None):
        response = result()
        response["turns"].append({"start": 10, "end": 15, "speaker": "b"})
        return response

    client.app.state.memory.speakers.detect_turns = mixed
    assert remember(client, path, speaker, data, name="Mads").status_code == 409
    assert directory(client)["profiles"] == []


def test_disabled_recognition_does_not_store_query_voice_vectors(client):
    client.app.state.memory.settings.voice_recognition_enabled = False
    scope(client)
    with client.app.state.memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM voice_vectors").fetchone()[0] == 0


def test_empty_report_environment_value_is_unconfigured():
    assert Settings(voice_recognition_report="", _env_file=None).voice_recognition_report is None


def test_model_upgrades_leave_voice_management_available_without_combining_examples(client):
    _, path, speaker, data = scope(client)
    saved = remember(client, path, speaker, data, name="Mads").json()
    profile = saved["speakers"][0]["voice_profile_id"]
    _, other, another, detected = scope(client, response=result(model={
        **MODEL, "revision": "b" * 64,
    }))
    assert remember(client, other, another, detected, profile_id=profile).status_code == 200
    voices = directory(client)
    assert voices["profiles"][0]["example_count"] == 2
    assert voices["profiles"][0]["conversation_count"] == 1
    assert voices["recognition"]["state"] == "unvalidated"
    assert client.delete(f"/v1/voice-profiles/{profile}",
                         params={"revision": voices["revision"]}).status_code == 200
