"""Automatic-name API behavior with synthetic fixtures, never accuracy evidence."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from test_speakers import wav
from test_voice_suggestions import (
    MODEL,
    confirmed_gallery,
    detect_source,
    directory,
    temporary_software_report,
)
from test_voice_suggestions import client as client

from omi_local.domain import utc_now
from omi_local.speaker_worker import discover, process_one


def configure(client, **changes):
    configuration = client.get("/v1/configuration").json()
    response = client.patch("/v1/configuration", json={
        "revision": configuration["revision"], "changes": changes,
    })
    assert response.status_code == 200, response.text


def test_opt_in_applies_new_names_without_training_or_designating_self(client, tmp_path):
    owners = confirmed_gallery(client)
    own = client.patch("/v1/voice-profiles/self", json={
        "profile_id": owners["Mads"], "revision": directory(client)["revision"],
    })
    assert own.status_code == 200, own.text
    temporary_software_report(client, tmp_path, owners["Mads"])
    original_path, _, original = detect_source(client, 100)
    assert original["speakers"][0]["name"] is None
    assert original["speakers"][0].get("automatic_match") is None

    configure(client, voice_recognition_auto_assign=True)
    # Existing detections need an explicit reset before their saved names can change.
    assert client.get(original_path).json()["speakers"][0]["name"] is None
    before = directory(client)
    path, _, detected = detect_source(client, 101)
    for view in (detected, client.get(path).json(), client.post(path).json()):
        speaker = view["speakers"][0]
        assert speaker["name"] == "Mads"
        assert speaker["automatic_match"] == {"profile_id": owners["Mads"], "name": "Mads"}
        assert speaker["voice_profile_id"] is None
        assert speaker["remembered_example_id"] is None
        assert speaker["is_self"] is False
    assert directory(client)["revision"] == before["revision"]
    assert directory(client)["profiles"] == before["profiles"]


def test_only_explicit_confirmation_saves_an_automatic_match_as_an_example(client, tmp_path):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    configure(client, voice_recognition_auto_assign=True)
    path, speaker_id, detected = detect_source(client, 100)
    assert detected["speakers"][0]["automatic_match"]["profile_id"] == owners["Mads"]
    confirmed = client.post(f"{path}/{speaker_id}/confirm", json={
        "version": detected["version"], "profile_id": owners["Mads"],
    })
    assert confirmed.status_code == 200, confirmed.text
    speaker = confirmed.json()["speakers"][0]
    assert speaker["name"] == "Mads"
    assert speaker.get("automatic_match") is None
    assert speaker["voice_profile_id"] == owners["Mads"]
    assert speaker["remembered_example_id"]
    assert speaker["suggestion"] is None
    counts = {profile["name"]: profile["example_count"]
              for profile in directory(client)["profiles"]}
    assert counts == {"Mads": 3, "Alice": 2}


@pytest.mark.parametrize("name", ["Corrected name", None])
def test_manual_name_or_cleared_label_is_not_replaced_on_subsequent_reads(client, tmp_path, name):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    configure(client, voice_recognition_auto_assign=True)
    path, speaker_id, detected = detect_source(client, 100)
    corrected = client.patch(f"{path}/{speaker_id}", json={
        "version": detected["version"], "name": name,
    })
    assert corrected.status_code == 200, corrected.text
    for view in (corrected.json(), client.get(path).json(), client.post(path).json()):
        assert view["speakers"][0]["name"] == name
        assert view["speakers"][0].get("automatic_match") is None


@pytest.mark.parametrize("case", ["unvalidated", "unknown", "model_changed", "recognition_off"])
def test_unvalidated_unknown_and_incompatible_voices_keep_anonymous_labels(client, tmp_path, case):
    owners = confirmed_gallery(client)
    configure(client, voice_recognition_auto_assign=True)
    if case != "unvalidated":
        temporary_software_report(client, tmp_path, owners["Mads"])
    if case == "recognition_off":
        configure(client, voice_recognition_enabled=False)
    model = {**MODEL, "revision": "b" * 64} if case == "model_changed" else None
    vector = (-1.0, 0.0) if case == "unknown" else (1.0, 0.0)
    path, _, detected = detect_source(client, 100, vector=vector, model=model)
    for view in (detected, client.get(path).json()):
        assert view["speakers"][0]["name"] is None
        assert view["speakers"][0].get("automatic_match") is None


def test_switching_off_preserves_existing_names_and_stops_new_ones(client, tmp_path):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    configure(client, voice_recognition_auto_assign=True)
    original_path, _, original = detect_source(client, 100)
    assert original["speakers"][0]["name"] == "Mads"
    configure(client, voice_recognition_auto_assign=False)
    assert client.get(original_path).json()["speakers"][0]["name"] == "Mads"
    _, _, new = detect_source(client, 101)
    assert new["speakers"][0]["name"] is None
    assert new["speakers"][0]["suggestion"]["name"] == "Mads"


@pytest.mark.parametrize("action", ["reassign", "merge"])
def test_changed_turns_clear_machine_names_that_need_review(client, tmp_path, action):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    configure(client, voice_recognition_auto_assign=True)
    path, _, _ = detect_source(client, 100)

    async def detector(audio, num_speakers=None):
        return {"model": "software-fixture", "embedding_model": MODEL,
                "turns": [{"start": 0, "end": 10, "speaker": "a"},
                          {"start": 10, "end": 20, "speaker": "b"}],
                "voices": [{"speaker": key, "embedding": vector, "clean_seconds": 10,
                            "overlap_ratio": 0, "clipped_ratio": 0}
                           for key, vector in (("a", [1.0, 0.0]), ("b", [0.0, 1.0]))]}

    client.app.state.memory.speakers.detect_turns = detector
    response = client.post(path, json={"reset": True})
    assert response.status_code == 200, response.text
    detected = response.json()
    first, second = detected["speakers"]
    assert [first["name"], second["name"]] == ["Mads", "Alice"]
    if action == "reassign":
        response = client.patch(f"{path}/turns/{detected['turns'][0]['id']}", json={
            "version": detected["version"], "speaker_id": second["id"],
        })
    else:
        response = client.post(path + "/merge", json={
            "version": detected["version"], "source_id": first["id"], "target_id": second["id"],
        })
    assert response.status_code == 200, response.text
    assert all(s["name"] is None and not s.get("automatic_match")
               for s in response.json()["speakers"])


def test_background_detection_uses_the_same_automatic_naming_policy(client, tmp_path):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    configure(client, voice_recognition_auto_assign=True)
    memory = client.app.state.memory
    _, _, detected = detect_source(client, 100)
    assert detected["speakers"][0]["name"] == "Mads"
    key = str(uuid4())
    memory.store.ingest(key, "software-only", utc_now() - timedelta(hours=2),
                        wav(101, seconds=20), 20, source_kind="manual_upload",
                        source_id=key + ".wav")
    memory.store.complete(key, "Software fixture speech")
    queued_path = f"/v1/speakers/source/{key}"
    assert client.get(queued_path).json()["state"] == "idle"
    assert discover(memory) == 1
    assert asyncio.run(process_one(memory))
    ready = client.get(queued_path).json()
    assert ready["state"] == "ready"
    assert ready["speakers"][0]["name"] == "Mads"


@pytest.mark.parametrize("review", ["rename", "confirm"])
def test_utterances_preserve_automatic_name_origin_until_review(client, tmp_path, review):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    configure(client, voice_recognition_auto_assign=True)
    path, _, _ = detect_source(client, 100)
    source_id = path.rsplit("/", 1)[-1]
    client.app.state.memory.store.complete(source_id, "alpha beta", segments=[
        {"start": 0.1, "end": 0.3, "text": "alpha", "start_offset": 0,
         "end_offset": 5, "kind": "word"},
        {"start": 0.5, "end": 0.8, "text": " beta", "start_offset": 5,
         "end_offset": 10, "kind": "word"},
    ])
    detected_response = client.post(path, json={"reset": True})
    assert detected_response.status_code == 200, detected_response.text
    detected = detected_response.json()
    for view in (detected, client.get(path).json()):
        assert "".join(item["text"] for item in view["utterances"]) == "alpha beta"
        assert all(item["attribution"] == "attributed" and item["speaker_name"] == "Mads"
                   and item["speaker_name_origin"] == "automatic" for item in view["utterances"])

    speaker_id = detected["speakers"][0]["id"]
    if review == "rename":
        response = client.patch(f"{path}/{speaker_id}", json={
            "version": detected["version"], "name": "Reviewed Mads",
        })
        expected_name = "Reviewed Mads"
    else:
        response = client.post(f"{path}/{speaker_id}/confirm", json={
            "version": detected["version"], "profile_id": owners["Mads"],
        })
        expected_name = "Mads"
    assert response.status_code == 200, response.text
    for view in (response.json(), client.get(path).json()):
        assert all(item["attribution"] == "attributed"
                   and item["speaker_name"] == expected_name
                   and "speaker_name_origin" not in item for item in view["utterances"])
