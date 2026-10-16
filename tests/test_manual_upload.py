import io
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import ButtonEvent, stamp
from omi_local.settings import Settings

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
