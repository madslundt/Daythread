"""Capture GPS belongs to original recordings, including retries and retention."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_manual_upload import AT, upload
from test_manual_upload import client as client

from omi_local.domain import stamp


def gps(**changes):
    return {"latitude": 55.6761, "longitude": 12.5683, "horizontal_accuracy": 35,
            "location_captured_at": stamp(AT - timedelta(seconds=20)), **changes}


def capture(client, key=None, **changes):
    return upload(client, key or uuid4(), device_id="pendant", source_kind="", source_id="",
                  **changes)


def test_capture_location_survives_delayed_retry_and_source_reads(client):
    key = uuid4()
    first = capture(client, key, **gps())
    assert first.status_code == 200, first.text
    observation = first.json()["source_metadata"]["location"]
    assert observation == {"latitude": 55.6761, "longitude": 12.5683,
                           "horizontal_accuracy": 35,
                           "captured_at": "2026-10-15T11:59:40Z"}
    assert capture(client, key, **gps()).json() == first.json()
    assert client.get(f"/v1/sources/{key}").json()["source_metadata"]["location"] == observation
    assert capture(client, key, **gps(latitude=56)).status_code == 409
    assert capture(client, key).status_code == 409
    assert client.get(f"/v1/sources/{key}").json()["source_metadata"]["location"] == observation


@pytest.mark.parametrize("changes", [
    {"latitude": None}, {"longitude": None}, {"horizontal_accuracy": None},
    {"location_captured_at": None}, {"latitude": 91}, {"longitude": -181},
    {"latitude": "nan"}, {"longitude": "inf"},
    {"horizontal_accuracy": -1}, {"horizontal_accuracy": 1001},
    {"location_captured_at": stamp(AT - timedelta(seconds=121))},
    {"location_captured_at": stamp(AT + timedelta(seconds=6))},
    {"location_captured_at": "2026-10-15T12:00:00"},
])
def test_invalid_partial_or_stale_gps_is_rejected_without_saving(client, changes):
    key = uuid4()
    response = capture(client, key, **gps(**changes))
    assert response.status_code == 422, response.text
    assert client.get(f"/v1/sources/{key}").status_code == 404


def test_legacy_and_imported_recordings_do_not_get_invented_locations(client):
    assert "location" not in capture(client).json()["source_metadata"]
    assert upload(client, uuid4(), **gps()).status_code == 422


def test_location_follows_recording_profile_and_deletion(client):
    key = uuid4()
    assert capture(client, key, **gps()).status_code == 200
    other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    assert client.get(f"/v1/sources/{key}", headers={"X-Omi-Profile": other}).status_code == 404
    assert client.delete(f"/v1/audio/{key}").status_code == 200
    assert client.get(f"/v1/sources/{key}").status_code == 404


def test_transcript_retention_removes_location_even_if_audio_is_already_expired(client):
    key = uuid4()
    assert capture(client, key, **gps()).status_code == 200
    store = client.app.state.memory.store
    store.apply_retention(audio_days=1, transcript_days=0, now=AT + timedelta(days=3))
    assert "location" in client.get(f"/v1/sources/{key}").json()["source_metadata"]
    version = client.get(f"/v1/sources/{key}").json()["source_version"]
    store.apply_retention(audio_days=1, transcript_days=1, now=AT + timedelta(days=3))
    assert client.get(f"/v1/sources/{key}").json()["source_version"] != version
    assert "location" not in client.get(f"/v1/sources/{key}").json()["source_metadata"]
