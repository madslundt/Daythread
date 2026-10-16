"""Speaker assignments stay local to a conversation and respect evidence lifetime."""

import io
import wave
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import ButtonEvent, utc_now
from omi_local.settings import Settings
from omi_local.store import Store


def wav(sample=100, seconds=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(sample.to_bytes(2, "little", signed=True) * (16000 * seconds))
    return output.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(
        data_dir=tmp_path, worker_enabled=False, diarization_enabled=True, _env_file=None,
    ))) as client:
        yield client


def recordings(client, *, filename="meeting.wav", start=None):
    store = client.app.state.memory.store
    start = start or utc_now() - timedelta(minutes=10)
    keys = [str(uuid4()), str(uuid4())]
    for i, key in enumerate(keys):
        store.ingest(key, "manual-web", start + timedelta(seconds=i * 2), wav(100 + i), 2,
                     source_kind="manual_upload", source_id=filename)
        store.complete(key, f"Sentence {i}", segments=[
            {"start": 0, "end": 1, "text": f"First {i}"},
            {"start": 1, "end": 2, "text": f"Second {i}"},
        ])
    return keys, f"/v1/speakers/source/{keys[0]}"


def detector(client, callback=None):
    calls = []

    async def detect(data, num_speakers=None):
        with wave.open(io.BytesIO(data)) as stream:
            calls.append(stream.getnframes())
        if callback:
            callback()
        return {"model": "test", "turns": [
            {"start": 0, "end": 1, "speaker": "voice0"},
            {"start": 1, "end": 3, "speaker": "voice1"},
            {"start": 3, "end": 4, "speaker": "voice0"},
        ]}

    client.app.state.memory.speakers.detect_turns = detect
    return calls


def test_detect_names_and_playback_share_a_whole_upload_and_persist(client):
    keys, path = recordings(client)
    calls = detector(client)
    original = client.get(f"/v1/transcripts/{keys[0]}").json()
    assert client.get(path).json()["state"] == "idle"
    assert calls == []
    response = client.post(path)
    assert response.status_code == 200, response.text
    result = response.json()
    assert calls == [64000]
    assert result["source_count"] == 2
    assert [s["label"] for s in result["speakers"]] == ["A", "B"]
    assert [s["seconds"] for s in result["speakers"]] == [2, 2]
    assert len(result["turns"]) == 4
    a = result["speakers"][0]["id"]
    named = client.patch(f"{path}/{a}", json={"name": " Mads ", "version": result["version"]})
    assert named.status_code == 200, named.text
    assert named.json()["speakers"][0]["name"] == "Mads"
    assert client.get(f"/v1/speakers/source/{keys[1]}").json() == named.json()
    playback = client.get(f"{path}/{a}/audio")
    assert playback.status_code == 200
    with wave.open(io.BytesIO(playback.content)) as stream:
        assert stream.getnframes() == 32000
        assert stream.readframes(16000) == (100).to_bytes(2, "little") * 16000
        assert stream.readframes(16000) == (101).to_bytes(2, "little") * 16000
    assert client.get(f"/v1/transcripts/{keys[0]}").json() == original
    assert client.post(path).json() == named.json()
    assert len(calls) == 1
    with Store(client.app.state.memory.store.root).connect() as db:
        assert db.execute("SELECT count(*) FROM speaker_events").fetchone()[0] == 2


def test_reassignment_merge_and_version_conflicts(client):
    _, path = recordings(client)
    detector(client)
    result = client.post(path).json()
    a, b = [s["id"] for s in result["speakers"]]
    turn = result["turns"][0]["id"]
    changed = client.patch(f"{path}/turns/{turn}", json={
        "speaker_id": b, "version": result["version"],
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["turns"][0]["speaker_id"] == b
    assert client.patch(f"{path}/{a}", json={
        "name": "Stale", "version": result["version"],
    }).status_code == 409
    merged = client.post(f"{path}/merge", json={
        "source_id": b, "target_id": a, "version": changed.json()["version"],
    })
    assert merged.status_code == 200, merged.text
    assert len(merged.json()["speakers"]) == 1
    assert all(t["speaker_id"] == a for t in merged.json()["turns"])
    assert client.get(f"{path}/{a}/audio?version={result['version']}").status_code == 409


@pytest.mark.parametrize("action", ["correct", "delete", "expire", "audio_only", "arrival"])
def test_source_lifetime_and_late_chunks(client, action):
    keys, path = recordings(client)
    store = client.app.state.memory.store
    detector(client)
    result = client.post(path).json()
    if action == "correct":
        store.correct_transcript(keys[1], "Corrected words")
    elif action == "delete":
        store.delete_audio(keys[1])
    elif action in {"expire", "audio_only"}:
        store.apply_retention(1, 1 if action == "expire" else 0, utc_now() + timedelta(days=2))
    else:
        last = store.get_audio(keys[1])
        store.ingest(str(uuid4()), "manual-web", datetime.fromisoformat(last["ended_at"]),
                     wav(), 2, source_kind="manual_upload", source_id="meeting.wav")
    state = client.get(path).json()
    a = result["speakers"][0]["id"]
    if action == "audio_only":
        assert state["state"] == "ready"
        assert state["audio_available"] is False
        assert client.get(f"{path}/{a}/audio").status_code == 409
    elif action == "expire":
        assert state["state"] == "idle"
        assert state["turns"] == []
    elif action == "delete":
        assert state["state"] == "idle"
    else:
        assert state["state"] == "stale"
        assert all("text" not in t for t in state["turns"])
        assert client.patch(f"{path}/{a}", json={
            "name": "Mads", "version": result["version"],
        }).status_code == 409
        assert client.get(f"{path}/{a}/audio").status_code == 409
        assert client.post(path).status_code == 409


def test_inflight_changes_and_bad_model_output_do_not_save(client):
    keys, path = recordings(client)
    detector(client, lambda: client.app.state.memory.store.correct_transcript(keys[1], "Changed"))
    assert client.post(path).status_code == 409
    assert client.get(path).json()["state"] == "idle"


def test_profile_isolation_disabled_and_missing_audio(client):
    keys, path = recordings(client)
    detector(client)
    client.app.state.memory.settings.diarization_enabled = False
    assert client.get(path).json()["configured"] is False
    assert client.post(path).status_code == 409
    client.app.state.memory.settings.diarization_enabled = True
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    assert client.get(path, headers={"x-omi-profile": profile}).status_code == 404
    Path(client.app.state.memory.store.get_audio(keys[0])["path"]).unlink()
    assert client.post(path).status_code == 409


def test_meeting_clips_turns_and_names_are_shared_with_its_sources(client):
    keys, _ = recordings(client)
    store = client.app.state.memory.store
    start = datetime.fromisoformat(store.get_audio(keys[0])["started_at"])
    meeting_id = uuid4()
    for event_id, at in [(meeting_id, start - timedelta(seconds=30)),
                         (uuid4(), start + timedelta(seconds=40))]:
        store.append_event(ButtonEvent(event_id=event_id, device_id="manual-web", occurred_at=at,
                                    action="meeting_toggle"), 120, 60)
    path = f"/v1/speakers/meeting/{meeting_id}"
    detector(client)
    result = client.post(path)
    assert result.status_code == 200, result.text
    assert result.json()["scope"] == {"kind": "meeting", "id": str(meeting_id)}
    assert client.get(f"/v1/speakers/source/{keys[0]}").json() == result.json()


@pytest.mark.parametrize("turns", [
    [{"start": -1, "end": 2, "speaker": "a"}],
    [{"start": 1, "end": 1, "speaker": "a"}],
    [{"start": 0, "end": 10, "speaker": "a"}],
    [{"start": 4.01, "end": 4.02, "speaker": "a"}],
    [{"start": float("nan"), "end": 2, "speaker": "a"}],
    [{"start": 0, "end": 1}],
    "invalid",
])
def test_invalid_model_output_never_creates_assignments(client, turns):
    _, path = recordings(client)

    async def detect(data, num_speakers=None):
        return {"turns": turns}

    client.app.state.memory.speakers.detect_turns = detect
    assert client.post(path).status_code == 502
    assert client.get(path).json()["state"] == "idle"


def test_redetection_requires_reset_and_does_not_overwrite_concurrent_names(client):
    keys, path = recordings(client)
    detector(client)
    result = client.post(path).json()
    a = result["speakers"][0]["id"]
    named = client.patch(f"{path}/{a}", json={"version": result["version"], "name": "Mads"}).json()
    service = client.app.state.memory.speakers
    detector(client, lambda: service.mutate("source", keys[0], named["version"], "rename", {
        "speaker_id": a, "name": "Updated Mads",
    }))
    assert client.post(path, json={"reset": True}).status_code == 409
    assert client.get(path).json()["speakers"][0]["name"] == "Updated Mads"
    detector(client)
    reset = client.post(path, json={"reset": True}).json()
    assert reset["speakers"][0]["name"] is None
    assert reset["speakers"][0]["id"] != a


def test_names_are_separate_between_conversations_and_clearable(client):
    _, path = recordings(client)
    _, other = recordings(client, filename="different.wav")
    detector(client)
    result = client.post(path).json()
    a = result["speakers"][0]["id"]
    named = client.patch(f"{path}/{a}", json={"version": result["version"], "name": "Mads"}).json()
    assert client.post(other).json()["speakers"][0]["name"] is None
    cleared = client.patch(f"{path}/{a}", json={"version": named["version"], "name": " "})
    assert cleared.json()["speakers"][0]["name"] is None
    assert cleared.json()["speakers"][0]["label"] == "A"
    assert client.patch(f"{path}/{a}", json={
        "version": cleared.json()["version"], "name": "Bad\nName",
    }).status_code == 422


def test_broad_stt_segments_do_not_attribute_multiple_speakers_words_to_one_person(client):
    keys, path = recordings(client)
    store = client.app.state.memory.store
    for key in keys:
        store.complete(key, "Two people speaking", segments=[
            {"start": 0, "end": 2, "text": "Two people speaking"},
        ])
    detector(client)
    result = client.post(path).json()
    assert all("text" not in t for t in result["turns"])


def test_overlapping_turns_are_unioned_after_merge(client):
    _, path = recordings(client)

    async def detect(data, num_speakers=None):
        return {"turns": [{"start": 0, "end": 3, "speaker": "a"},
                          {"start": 1, "end": 4, "speaker": "b"}]}

    client.app.state.memory.speakers.detect_turns = detect
    result = client.post(path).json()
    a, b = [s["id"] for s in result["speakers"]]
    merged = client.post(f"{path}/merge", json={
        "source_id": b, "target_id": a, "version": result["version"],
    }).json()
    assert merged["speakers"][0]["seconds"] == 4
    with wave.open(io.BytesIO(client.get(f"{path}/{a}/audio").content)) as stream:
        assert stream.getnframes() == 64000


def test_diarization_http_contract_and_failure_messages(client, monkeypatch):
    _, path = recordings(client)
    calls = []

    async def post(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs["files"]["file"][2] == "audio/wav"
        return httpx.Response(200, json={"model": "test", "turns": [
            {"start": 0, "end": 4, "speaker": "a"},
        ]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(client.app.state.memory.inference.client, "post", post)
    assert client.post(path, json={"num_speakers": 1}).status_code == 200
    assert calls[0][0] == "http://127.0.0.1:8790/diarize"
    assert calls[0][1]["data"] == {"num_speakers": "1"}
    assert calls[0][1]["timeout"] == 600

    async def fail(url, **kwargs):
        raise httpx.ConnectError("Private server error")

    monkeypatch.setattr(client.app.state.memory.inference.client, "post", fail)
    failure = client.post(path, json={"reset": True})
    assert failure.status_code == 502
    assert "Private server" not in failure.text
    assert client.get(path).json()["state"] == "ready"


def test_inflight_deletion_does_not_restore_speaker_names(client):
    keys, path = recordings(client)
    detector(client, lambda: client.app.state.memory.store.delete_audio(keys[0]))
    assert client.post(path).status_code == 409
    with client.app.state.memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM speaker_scopes").fetchone()[0] == 0


def test_pendant_chunks_share_names_within_one_projected_conversation(client):
    store = client.app.state.memory.store
    start = utc_now() - timedelta(minutes=10)
    keys = [str(uuid4()), str(uuid4())]
    for i, key in enumerate(keys):
        store.ingest(key, "pendant", start + timedelta(seconds=i * 2), wav(), 2)
        store.complete(key, f"Turn {i}")
    detector(client)
    path = f"/v1/speakers/source/{keys[0]}"
    result = client.post(path).json()
    assert result["source_count"] == 2
    a = result["speakers"][0]["id"]
    named = client.patch(f"{path}/{a}", json={"name": "Mads", "version": result["version"]})
    assert client.get(f"/v1/speakers/source/{keys[1]}").json() == named.json()


def test_meeting_audio_is_clipped_to_button_bounds_with_source_relative_turns(client):
    store = client.app.state.memory.store
    key, meeting_id = str(uuid4()), uuid4()
    start = utc_now() - timedelta(minutes=10)
    store.ingest(key, "pendant", start, wav(seconds=70), 70)
    store.complete(key, "Meeting")
    for event_id, at in [(meeting_id, start + timedelta(seconds=5)),
                         (uuid4(), start + timedelta(seconds=65))]:
        store.append_event(ButtonEvent(event_id=event_id, device_id="pendant", occurred_at=at,
                                       action="meeting_toggle"), 120, 60)

    async def detect(data, num_speakers=None):
        with wave.open(io.BytesIO(data)) as stream:
            assert stream.getnframes() == 60 * 16000
        return {"turns": [{"start": 0, "end": 60, "speaker": "a"}]}

    client.app.state.memory.speakers.detect_turns = detect
    path = f"/v1/speakers/meeting/{meeting_id}"
    result = client.post(path).json()
    assert result["turns"][0]["start"] == 5
    assert result["turns"][0]["end"] == 65
    assert client.get(f"/v1/speakers/source/{key}").json() == result


def test_live_meeting_does_not_become_stale_just_because_clock_advances(client, monkeypatch):
    keys, _ = recordings(client)
    store = client.app.state.memory.store
    start = datetime.fromisoformat(store.get_audio(keys[0])["started_at"])
    meeting_id = uuid4()
    store.append_event(ButtonEvent(event_id=meeting_id, device_id="manual-web",
                                   occurred_at=start - timedelta(seconds=60),
                                   action="meeting_toggle"), 120, 60)
    detector(client)
    path = f"/v1/speakers/meeting/{meeting_id}"
    result = client.post(path).json()
    later = utc_now() + timedelta(seconds=20)
    monkeypatch.setattr("omi_local.speakers.utc_now", lambda: later)
    assert client.get(path).json() == result
