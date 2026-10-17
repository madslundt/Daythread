import asyncio
import hashlib
import io
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import ButtonEvent, stamp
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as session:
        yield session


def upload(client, key, **metadata):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\x00" * 16000)
    return client.put(
        f"/v1/audio/{key}",
        params={
            "device_id": "manual-web", "started_at": stamp(AT),
            "source_kind": "manual_upload", "source_id": "Voice memo æ & notes.m4a",
            **metadata,
        },
        content=output.getvalue(),
        headers={"Content-Type": "audio/wav"},
    )


def test_manual_upload_preserves_provenance_audio_and_retry_identity(client):
    key = uuid4()
    original = upload(client, key)
    assert original.status_code == 200
    receipt = original.json()
    assert receipt["id"] == str(key)
    assert receipt["device_id"] == "manual-web"
    assert receipt["source_kind"] == "manual_upload"
    assert receipt["source_id"] == "Voice memo æ & notes.m4a"
    assert receipt["started_at"] == stamp(AT)
    assert receipt["ended_at"] == stamp(AT + timedelta(seconds=1))
    assert receipt["state"] == "queued"
    assert client.get(f"/v1/audio/{key}").status_code == 200
    assert upload(client, key).json() == receipt
    assert client.get(f"/v1/sources/{key}").json()["source_id"] == receipt["source_id"]
    assert client.get("/v1/status").json()["audio"] == {"queued": 1}


@pytest.mark.parametrize("metadata", [
    {"source_kind": ""}, {"source_id": "different.m4a"}, {"device_id": "manual-ios"},
])
def test_manual_upload_rejects_conflicting_retry_provenance(client, metadata):
    key = uuid4()
    assert upload(client, key).status_code == 200
    assert upload(client, key, **metadata).status_code == 409
    assert client.get(f"/v1/sources/{key}").json()["source_id"] == "Voice memo æ & notes.m4a"


@pytest.mark.parametrize("metadata", [
    {"source_kind": "omi_api"}, {"source_id": "x" * 256},
])
def test_manual_upload_rejects_invalid_provenance_before_ingestion(client, metadata):
    assert upload(client, uuid4(), **metadata).status_code == 422
    assert client.get("/v1/status").json()["audio"] == {}


def test_manual_upload_does_not_receive_wearable_gesture_hints(client):
    key = uuid4()
    assert upload(client, key).status_code == 200
    memory = client.app.state.memory
    memory.store.append_event(ButtonEvent(
        event_id=uuid4(), device_id="omi", occurred_at=AT, action="mark",
    ), 120, 60)
    for at in (AT - timedelta(seconds=1), AT + timedelta(seconds=60)):
        memory.store.append_event(ButtonEvent(
            event_id=uuid4(), device_id="omi", occurred_at=at, action="meeting_toggle",
        ), 120, 60)
    result = memory.window_data(AT - timedelta(seconds=1), AT + timedelta(minutes=2))
    assert len(result["markers"]) == 1
    assert len(result["meetings"]) == 1
    assert result["sources"][0]["important"] is False
    assert result["sources"][0]["meeting_hint_ids"] == []
    assert memory.store.contiguous_context(result["sources"][0], None, None) == []


def audio_part(seconds=1, level=1000):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(level.to_bytes(2, "little", signed=True) * 16000 * seconds)
    return output.getvalue()


def manifest(parts, **changes):
    samples = sum((len(part) - 44) // 2 for part in parts)
    return {
        "file_digest": hashlib.sha256(b"original file").hexdigest(),
        "digests": [hashlib.sha256(part).hexdigest() for part in parts],
        "sample_count": samples, "source_id": "meeting.m4a",
        "started_at": stamp(AT), "ended_at": stamp(AT + timedelta(seconds=samples / 16000)),
        **changes,
    }


def prepare(client, body, **kwargs):
    response = client.post("/v1/recording-uploads/prepare", json=body, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def send_planned(client, plan, part, index=0, **kwargs):
    params = {
        "device_id": "manual-web", "source_kind": "manual_upload", "source_id": plan["source_id"],
        "started_at": stamp(datetime.fromisoformat(plan["started_at"])
                            + timedelta(seconds=index * 120)),
        "recording_id": plan["recording_id"], "part_index": index,
        "part_count": len(plan["parts"]), "recording_started_at": plan["started_at"],
        "recording_ended_at": plan["ended_at"],
    }
    if plan.get("conversation_source_id"):
        params["conversation_source_id"] = plan["conversation_source_id"]
    return client.put(f"/v1/audio/{plan['parts'][index]['id']}", params=params,
                      content=part, **kwargs)


def test_duplicate_is_skipped_before_upload_even_when_renamed_or_retimed(client):
    data = audio_part()
    body = manifest([data])
    plan = prepare(client, body)
    assert send_planned(client, plan, data).status_code == 200
    duplicate = prepare(client, body | {
        "source_id": "renamed.wav", "started_at": stamp(AT + timedelta(days=1)),
        "ended_at": stamp(AT + timedelta(days=1, seconds=1)),
    })
    assert duplicate == {"status": "duplicate", "source_id": "meeting.m4a"}
    check = client.post("/v1/recording-uploads/check", json={"digest": body["file_digest"]})
    assert check.json() == {"duplicate": True, "source_id": "meeting.m4a"}
    assert client.get("/v1/status").json()["audio"] == {"queued": 1}


def test_partial_upload_resumes_original_ids_and_provenance_across_store_restarts(client):
    parts = [audio_part(120), audio_part()]
    body = manifest(parts)
    plan = prepare(client, body)
    assert send_planned(client, plan, parts[0]).status_code == 200
    check = client.post("/v1/recording-uploads/check", json={"digest": body["file_digest"]})
    assert check.json() == {"duplicate": False}
    store = Store(client.app.state.memory.store.root)
    assert store.check_recording_file(body["file_digest"]) == {"duplicate": False}
    resumed = prepare(client, body | {"source_id": "copy.wav"})
    assert resumed["recording_id"] == plan["recording_id"]
    assert resumed["source_id"] == "meeting.m4a"
    assert [p["id"] for p in resumed["parts"]] == [p["id"] for p in plan["parts"]]
    assert [p["uploaded"] for p in resumed["parts"]] == [True, False]
    assert send_planned(client, resumed, parts[1], 1).status_code == 200
    assert prepare(client, body)["status"] == "duplicate"
    assert client.get("/v1/status").json()["audio"] == {"queued": 2}


def test_concurrent_uploads_use_one_audio_id_and_only_transcribe_once(client):
    class Inference:
        calls = 0

        async def transcribe(self, data):
            self.calls += 1
            return "One recording"

    data = audio_part()
    body = manifest([data])
    with ThreadPoolExecutor(max_workers=4) as pool:
        plans = list(pool.map(lambda _: prepare(client, body), range(8)))
        responses = list(pool.map(lambda plan: send_planned(client, plan, data), plans))
    assert len({plan["parts"][0]["id"] for plan in plans}) == 1
    assert all(response.status_code == 200 for response in responses)
    assert client.get("/v1/status").json()["audio"] == {"queued": 1}
    memory = client.app.state.memory
    memory.inference = Inference()
    assert asyncio.run(process_one(memory)) is True
    assert asyncio.run(process_one(memory)) is False
    assert memory.inference.calls == 1


def test_shared_first_chunk_does_not_make_different_recordings_duplicates(client):
    parts = [audio_part(120), audio_part()]
    body = manifest(parts)
    plan = prepare(client, body)
    for index, data in enumerate(parts):
        assert send_planned(client, plan, data, index).status_code == 200
    other = manifest([parts[0], audio_part(level=2000)],
                     file_digest=hashlib.sha256(b"different file").hexdigest())
    assert prepare(client, other)["status"] == "upload"


def test_duplicate_check_matches_existing_grouped_and_legacy_uploads(client):
    data = audio_part()
    key = uuid4()
    response = client.put(f"/v1/audio/{key}", params={
        "device_id": "web", "started_at": stamp(AT),
    }, content=data)
    assert response.status_code == 200
    assert prepare(client, manifest([data]))["status"] == "duplicate"
    assert client.put(f"/v1/audio/{key}", params={
        "device_id": "web", "started_at": stamp(AT),
    }, content=data).status_code == 200  # Existing clients retain their retry contract.
    assert client.get("/v1/status").json()["audio"] == {"queued": 1}


def test_duplicate_check_matches_complete_recordings_uploaded_before_upgrade(client):
    parts = [audio_part(120), audio_part()]
    old = {"recording_id": str(uuid4()), "source_id": "old.wav", "started_at": stamp(AT),
           "ended_at": stamp(AT + timedelta(seconds=121)),
           "parts": [{"id": str(uuid4())}, {"id": str(uuid4())}]}
    for index, data in enumerate(parts):
        assert send_planned(client, old, data, index).status_code == 200
    assert prepare(client, manifest(parts)) == {"status": "duplicate", "source_id": "old.wav"}
    assert send_planned(client, old, parts[0]).status_code == 200


def test_fingerprints_are_verified_against_uploaded_audio(client):
    plan = prepare(client, manifest([audio_part()]))
    response = send_planned(client, plan, audio_part(level=2000))
    assert response.status_code == 409
    assert "upload plan" in response.json()["detail"]
    assert client.get("/v1/status").json()["audio"] == {}


def test_missing_file_is_repaired_and_deleted_audio_can_be_imported_again(client):
    data = audio_part()
    body = manifest([data])
    plan = prepare(client, body)
    key = plan["parts"][0]["id"]
    assert send_planned(client, plan, data).status_code == 200
    store = client.app.state.memory.store
    path = store.get_audio(key)["path"]
    Path(path).unlink()
    repair = prepare(client, body)
    assert repair["parts"][0]["id"] == key
    assert repair["parts"][0]["uploaded"] is False
    assert send_planned(client, repair, data).status_code == 200
    assert client.delete(f"/v1/audio/{key}").status_code == 200
    fresh = prepare(client, body)
    assert fresh["status"] == "upload"
    assert fresh["parts"][0]["id"] != key


def test_upload_fingerprints_are_profile_scoped(client):
    data = audio_part()
    body = manifest([data])
    first = prepare(client, body)
    assert send_planned(client, first, data).status_code == 200
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    headers = {"X-Omi-Profile": profile}
    assert client.post("/v1/recording-uploads/check", json={"digest": body["file_digest"]},
                       headers=headers).json() == {"duplicate": False}
    second = prepare(client, body, headers=headers)
    assert second["status"] == "upload"
    assert second["recording_id"] != first["recording_id"]


@pytest.mark.parametrize("changes", [
    {"digests": ["not a digest"]}, {"sample_count": 32000},
    {"ended_at": stamp(AT)}, {"digests": []},
])
def test_invalid_recording_manifest_is_rejected(client, changes):
    response = client.post("/v1/recording-uploads/prepare", json=manifest([audio_part()]) | changes)
    assert response.status_code == 422
