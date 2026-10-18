"""Saved-place assignment preserves capture/retry evidence and rejects arbitrary locations."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_location_zones import point, save
from test_locations import capture, gps
from test_manual_upload import AT, upload
from test_manual_upload import client as client

from omi_local.conversation_state import Conversation
from omi_local.store import Store


@pytest.fixture
def place(client):
    zone = save(client, locations=[point()]).json()
    location = zone["locations"][0]
    reference = {"zone_id": zone["id"], "location_id": location["id"]}
    snapshot = {**reference, "zone_name": zone["name"], "label": location["label"],
                "latitude": location["latitude"], "longitude": location["longitude"]}
    return reference, snapshot


def write(client, key, location, version=0, **kwargs):
    return client.put(f"/v1/sources/{key}/location", json={
        "location": location, "expected_version": version,
    }, **kwargs)


def test_assign_change_restore_and_restart_preserve_original_and_retry_identity(client, place):
    reference, snapshot = place
    key = uuid4()
    receipt = capture(client, key, **gps()).json()
    path = f"/v1/sources/{key}/location"
    original = receipt["source_metadata"]["location"]
    assert client.get(path).headers["cache-control"] == "no-store"
    assert client.get(path).json()["location"] == original
    response = write(client, key, reference)
    assert response.status_code == 200, response.text
    assert response.json() == {"version": 1, "location": snapshot,
                               "original_location": original, "manual": True}
    source = client.get(f"/v1/sources/{key}").json()
    assert source["manual_location"] == snapshot
    assert source["source_version"] == receipt["source_version"]
    assert source["source_metadata"] == receipt["source_metadata"]
    assert capture(client, key, **gps()).status_code == 200
    assert Store(client.app.state.memory.store.root).recording_location(str(key)) == response.json()
    assert write(client, key, reference).status_code == 409
    other = save(client, name="Gym", locations=[point(latitude=0, longitude=0)]).json()
    other_ref = {"zone_id": other["id"], "location_id": other["locations"][0]["id"]}
    assert write(client, key, other_ref, version=1).json()["location"]["latitude"] == 0
    restored = write(client, key, None, version=2).json()
    assert restored["version"] == 3 and restored["location"] == original
    assert not restored["manual"]
    assert write(client, key, reference).status_code == 409
    assert "manual_location" not in client.get(f"/v1/sources/{key}").json()


def test_missing_location_can_be_assigned_and_filters_and_groups_follow_edits(client, place):
    reference, snapshot = place
    key = uuid4()
    receipt = upload(client, key).json()
    query = {"zone_id": reference["zone_id"]}
    assert client.get("/v1/transcripts", params=query).json()["total"] == 0
    assert write(client, key, reference).status_code == 200
    listed = client.get("/v1/transcripts", params=query).json()["items"]
    assert listed[0]["manual_location"] == snapshot
    groups = client.get("/v1/recording-groups", params={**query, "day": str(AT.date())}).json()
    assert groups["total_recordings"] == 1
    assert groups["items"][0]["sources"][0]["manual_location"] == snapshot
    assert upload(client, key).json() == receipt
    assert write(client, key, None, 1).json()["location"] is None
    assert client.get("/v1/transcripts", params=query).json()["total"] == 0


@pytest.mark.parametrize("location", [
    {"label": "Office", "latitude": 55, "longitude": 12},
    {"label": "Office"}, {}, {"zone_id": str(uuid4())},
    {"zone_id": "invalid", "location_id": str(uuid4())},
])
def test_arbitrary_location_is_rejected(client, location):
    key = uuid4()
    capture(client, key)
    assert write(client, key, location).status_code == 422
    assert client.get(f"/v1/sources/{key}/location").json()["version"] == 0


def test_forged_coordinates_unknown_and_deleted_places_cannot_be_assigned(client, place):
    reference, snapshot = place
    key = uuid4()
    capture(client, key)
    assert write(client, key, {**reference, "latitude": 1}).status_code == 422
    assert write(client, key, {**reference, "location_id": str(uuid4())}).status_code == 404
    assert write(client, key, {**reference, "zone_id": str(uuid4())}).status_code == 404
    assert write(client, key, reference).json()["location"] == snapshot
    # Existing assignments keep their historical snapshot; a deleted place cannot be assigned again.
    client.delete(f"/v1/location-zones/{reference['zone_id']}?expected_version=1")
    assert client.get(f"/v1/sources/{key}/location").json()["location"] == snapshot
    assert write(client, key, reference, version=1).status_code == 404


def test_places_from_another_profile_cannot_be_assigned(client, place):
    reference, _ = place
    key = uuid4()
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    headers = {"X-Omi-Profile": profile}
    capture(client, key)
    audio = client.get(f"/v1/audio/{key}").content
    response = client.put(f"/v1/audio/{key}",
                          params={"device_id": "pendant", "started_at": AT.isoformat()},
                          content=audio, headers={**headers, "Content-Type": "audio/wav"})
    assert response.status_code == 200, response.text
    assert write(client, key, reference, headers=headers).status_code == 404
    assert client.get(f"/v1/sources/{key}/location", headers=headers).json()["version"] == 0


def test_locations_are_removed_with_source_or_retention(client, place):
    reference, _ = place
    key = uuid4()
    capture(client, key)
    assert write(client, key, reference).status_code == 200
    store = client.app.state.memory.store
    store.apply_retention(audio_days=1, transcript_days=1, now=AT + timedelta(days=3))
    assert write(client, key, reference, version=1).status_code == 404
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM source_locations").fetchone()[0] == 0
    second = uuid4()
    capture(client, second)
    write(client, second, reference)
    client.delete(f"/v1/audio/{second}")
    assert client.get(f"/v1/sources/{second}/location").status_code == 404
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM source_locations").fetchone()[0] == 0


def test_summary_sources_show_current_saved_place_and_tags(client, place):
    reference, snapshot = place
    key = str(uuid4())
    receipt = capture(client, key, **gps()).json()
    memory = client.app.state.memory
    memory.store.complete(key, "Discuss the weekly plan.")
    write(client, key, reference)
    tags = client.get(f"/v1/sources/{key}/tags").json()
    tagged = client.put(f"/v1/sources/{key}/tags", json={
        "action": "add", "name": "Planning", "expected_revision": tags["revision"],
        "expected_membership": tags["membership"],
    })
    assert tagged.status_code == 200, tagged.text
    group = Conversation(
        sources=[memory.store.get_audio(key)], hints={"markers": [], "meetings": []},
    )
    source = memory.recaps.sources(group)[0]
    assert source["manual_location"] == snapshot
    assert [tag["name"] for tag in source["tags"]] == ["Planning"]
    assert source["source_metadata"] == receipt["source_metadata"]
    write(client, key, None, version=1)
    assert "manual_location" not in memory.recaps.sources(group)[0]
