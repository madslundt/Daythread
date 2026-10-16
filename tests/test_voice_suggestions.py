"""SOFTWARE-ONLY API integration fixtures; no production accuracy report is installed.

Temporary synthetic reports exercise the real builder, gate and matcher together.
The vectors/audio here are intentionally artificial and establish no voice accuracy.
"""

import io
import json
import math
import wave
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import utc_now
from omi_local.settings import Settings
from omi_local.voice_matching import build_report, canonical_hash, validate_report

MODEL = {"id": "community-1-software-fixture", "revision": "a" * 64,
         "dimension": 2, "normalization": "l2-v1"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(
        data_dir=tmp_path / "SOFTWARE_ONLY_archive", worker_enabled=False,
        diarization_enabled=True, voice_recognition_enabled=True, _env_file=None,
    ))) as client:
        yield client


def runtime(vector, model=None):
    return {"model": "software-fixture", "embedding_model": model or MODEL,
            "turns": [{"start": 0, "end": 20, "speaker": "a"}],
            "voices": [{"speaker": "a", "embedding": list(vector), "clean_seconds": 15,
                        "overlap_ratio": 0, "clipped_ratio": 0}]}


def detect_source(client, sample, vector=(1.0, 0.0), model=None):
    """Distinct samples prevent independent-enrollment fixtures sharing audio digests."""
    memory = client.app.state.memory
    key = str(uuid4())
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * (20 * 16000))
    memory.store.ingest(key, "software-only", utc_now() - timedelta(hours=1),
                        output.getvalue(), 20, source_kind="manual_upload", source_id=key + ".wav")
    memory.store.complete(key, "Software fixture speech")

    async def detect(audio, num_speakers=None):
        return runtime(vector, model)

    memory.speakers.detect_turns = detect
    path = f"/v1/speakers/source/{key}"
    response = client.post(path)
    assert response.status_code == 200, response.text
    data = response.json()
    return path, data["speakers"][0]["id"], data


def directory(client):
    response = client.get("/v1/voice-profiles")
    assert response.status_code == 200, response.text
    return response.json()


def enroll(client, path, speaker, data, **identity):
    response = client.post(f"{path}/{speaker}/remember", json={
        "version": data["version"], **identity,
    })
    assert response.status_code == 200, response.text
    return response.json()["speakers"][0]["voice_profile_id"]


def confirmed_gallery(client, *, duplicate_mads_audio=False):
    owners = {}
    for index, (name, vector) in enumerate((("Mads", (1.0, 0.0)), ("Alice", (0.0, 1.0)))):
        for conversation in range(2):
            sample = 1 + 2 * index + (0 if duplicate_mads_audio and name == "Mads"
                                      else conversation)
            path, speaker, data = detect_source(client, sample, vector)
            identity = {"profile_id": owners[name]} if name in owners else {"name": name}
            owners[name] = enroll(client, path, speaker, data, **identity)
    profiles = directory(client)["profiles"]
    assert {profile["example_count"] for profile in profiles} == {2}
    counts = {profile["id"]: profile["conversation_count"] for profile in profiles}
    assert counts == {owners["Mads"]: 1 if duplicate_mads_audio else 2, owners["Alice"]: 2}
    return owners


def temporary_software_report(client, tmp_path, owner, vector=(1.0, 0.0)):
    """Manufacture a temporary passing software fixture, never an accuracy artifact."""
    memory = client.app.state.memory
    with memory.store.connect() as db:
        gallery = memory.voices.gallery(db)
    manifest, recordings = [], []
    for split, count in (("calibration", 2), ("test", 5)):
        for index in range(count):
            # Fragmented known clusters deliberately exercise the API contract; the
            # production evaluator requires actual WAV inference and human ground truth.
            labels = {f"known-{i}": owner for i in range(6)} | {"unknown-0": None,
                                                                  "unknown-1": None}
            entry = {"path": f"SOFTWARE_ONLY_{split}_{index}.wav",
                     "conversation_id": f"SOFTWARE_ONLY_{split}_{index}",
                     "split": split, "speakers": labels,
                     "audio_sha256": canonical_hash([split, index, "wav"]),
                     "pcm_sha256": canonical_hash([split, index, "pcm"])}
            response = {"embedding_model": MODEL, "turns": [
                {"speaker": label, "start": i * 10, "end": (i + 1) * 10}
                for i, label in enumerate(labels)
            ], "voices": [
                {"speaker": label, "embedding": list(vector) if identity else
                 [-1 / math.sqrt(2), -1 / math.sqrt(2)], "clean_seconds": 10,
                 "overlap_ratio": 0, "clipped_ratio": 0}
                for label, identity in labels.items()
            ]}
            manifest.append(entry)
            recordings.append({key: entry[key] for key in (
                "conversation_id", "split", "audio_sha256", "pcm_sha256",
            )} | {"duration_seconds": 80, "response": response})
    report = build_report(manifest, recordings, gallery, MODEL)
    assert report["passed"] is True
    assert validate_report(report, gallery, MODEL)[0]
    report_path = tmp_path / "SOFTWARE_TEST_ONLY_gate.json"
    report_path.write_text(json.dumps(report))
    configuration = client.get("/v1/configuration").json()
    configured = client.patch("/v1/configuration", json={
        "revision": configuration["revision"],
        "changes": {"voice_recognition_report": str(report_path)},
    })
    assert configured.status_code == 200, configured.text
    assert directory(client)["recognition"]["state"] == "ready"
    return report_path, report


def test_real_api_gate_only_suggests_then_explicit_confirmation_names_and_enrolls(client, tmp_path):
    owners = confirmed_gallery(client)
    _, report = temporary_software_report(client, tmp_path, owners["Mads"])
    before = directory(client)
    path, speaker, detected = detect_source(client, 100)
    assert detected["speakers"][0]["name"] is None
    assert detected["speakers"][0]["suggestion"] == {
        "profile_id": owners["Mads"], "name": "Mads",
    }
    for _ in range(2):
        speaker_view = client.get(path).json()["speakers"][0]
        assert speaker_view["name"] is None
        assert speaker_view["voice_profile_id"] is None
        assert speaker_view["remembered_example_id"] is None
        assert speaker_view["suggestion"]["profile_id"] == owners["Mads"]
    assert directory(client)["profiles"] == before["profiles"]
    confirmed = client.post(f"{path}/{speaker}/confirm", json={
        "version": detected["version"], "profile_id": owners["Mads"],
    })
    assert confirmed.status_code == 200, confirmed.text
    named = confirmed.json()["speakers"][0]
    assert named["name"] == "Mads"
    assert named["voice_profile_id"] == owners["Mads"]
    assert named["remembered_example_id"]
    assert named["suggestion"] is None
    after = directory(client)
    counts = {profile["id"]: profile["example_count"] for profile in after["profiles"]}
    assert counts == {owners["Mads"]: 3, owners["Alice"]: 2}
    assert after["recognition"]["state"] == "unvalidated"
    with client.app.state.memory.store.connect() as db:
        changed_gallery = client.app.state.memory.voices.gallery(db)
    assert not validate_report(report, changed_gallery, MODEL)[0]
    _, _, next_detected = detect_source(client, 101)
    assert next_detected["speakers"][0]["suggestion"] is None
    assert next_detected["speakers"][0]["name"] is None


@pytest.mark.parametrize("change", ["runtime_model", "report_contents", "rehashed_contents"])
def test_mismatch_and_modified_report_disable_api_suggestions(client, tmp_path, change):
    owners = confirmed_gallery(client)
    report_path, report = temporary_software_report(client, tmp_path, owners["Mads"])
    model = None
    if change == "runtime_model":
        model = {**MODEL, "revision": "b" * 64}
    else:
        report["metrics"]["precision"] = 0.999
        if change == "rehashed_contents":
            report["report_sha256"] = canonical_hash({key: value for key, value in report.items()
                                                       if key != "report_sha256"})
        report_path.write_text(json.dumps(report))
    before = directory(client)["profiles"]
    path, speaker, detected = detect_source(client, 100, model=model)
    assert detected["recognition"]["state"] == "unvalidated"
    assert detected["speakers"][0]["suggestion"] is None
    assert detected["speakers"][0]["name"] is None
    rejected = client.post(f"{path}/{speaker}/confirm", json={
        "version": detected["version"], "profile_id": owners["Mads"],
    })
    assert rejected.status_code == 409, rejected.text
    assert directory(client)["profiles"] == before


@pytest.mark.parametrize("forget", ["example", "person"])
def test_stale_suggestion_cannot_be_confirmed_after_forgetting(client, tmp_path, forget):
    owners = confirmed_gallery(client)
    temporary_software_report(client, tmp_path, owners["Mads"])
    path, speaker, detected = detect_source(client, 100)
    assert detected["speakers"][0]["suggestion"]["profile_id"] == owners["Mads"]
    voices = directory(client)
    profile = next(profile for profile in voices["profiles"] if profile["id"] == owners["Mads"])
    suffix = "/examples/" + profile["examples"][0]["id"] if forget == "example" else ""
    forgotten = client.delete(f"/v1/voice-profiles/{owners['Mads']}{suffix}",
                              params={"revision": voices["revision"]})
    assert forgotten.status_code == 200, forgotten.text
    current = client.get(path).json()
    assert current["speakers"][0]["suggestion"] is None
    assert current["speakers"][0]["name"] is None
    rejected = client.post(f"{path}/{speaker}/confirm", json={
        "version": detected["version"], "profile_id": owners["Mads"],
    })
    assert rejected.status_code == (409 if forget == "example" else 404), rejected.text
    assert directory(client)["profiles"] == forgotten.json()["profiles"]


def test_duplicate_audio_scopes_do_not_supply_two_independent_examples(client, tmp_path):
    owners = confirmed_gallery(client, duplicate_mads_audio=True)
    # Alice has genuinely separate software samples, so a valid gallery-bound
    # report can exercise the matcher while Mads still lacks independent support.
    temporary_software_report(client, tmp_path, owners["Alice"], (0.0, 1.0))
    path, _, detected = detect_source(client, 100)
    assert detected["recognition"]["state"] == "ready"
    assert detected["speakers"][0]["suggestion"] is None
    assert client.get(path).json()["speakers"][0]["name"] is None
