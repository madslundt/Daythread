"""Zones group GPS observations without rewriting capture evidence or leaking filter scope."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_locations import capture, gps
from test_manual_upload import AT
from test_manual_upload import client as client

from omi_local.domain import stamp
from omi_local.location_zones import contains
from omi_local.store import Store


def point(latitude=55.6761, longitude=12.5683, radius_m=200, **changes):
    return {
        "id": str(uuid4()),
        "label": "Office",
        "latitude": latitude,
        "longitude": longitude,
        "radius_m": radius_m,
        **changes,
    }


def save(client, *, key=None, name="Work", locations=None, expected_version=0, **kwargs):
    return client.put(
        f"/v1/location-zones/{key or uuid4()}",
        json={
            "name": name,
            "locations": locations if locations is not None else [point()],
            "expected_version": expected_version,
        },
        **kwargs,
    )


def test_multiple_centers_radii_and_names_persist_after_restart(client):
    locations = [point(radius_m=100), point(56, 10, 500, label="Second office")]
    response = save(client, name="  Work  ", locations=locations)
    assert response.status_code == 200, response.text
    zone = response.json()
    assert zone["name"] == "Work" and zone["version"] == 1
    assert zone["locations"] == locations
    reopened = Store(client.app.state.memory.store.root)
    assert reopened.location_zones() == [zone]
    assert client.get("/v1/location-zones").headers["cache-control"] == "no-store"
    assert save(client, name="work").status_code == 409
    assert save(client, key=zone["id"], expected_version=0).status_code == 409
    changed = save(
        client,
        key=zone["id"],
        name="Work",
        expected_version=1,
        locations=[{**locations[0], "radius_m": 350}, locations[1]],
    )
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert client.delete(f"/v1/location-zones/{zone['id']}?expected_version=1").status_code == 409
    assert client.delete(f"/v1/location-zones/{zone['id']}?expected_version=2").status_code == 204


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "  "},
        {"name": "Bad\x00name"},
        {"locations": []},
        {"locations": [point(latitude=91)]},
        {"locations": [point(longitude=-181)]},
        {"locations": [point(radius_m=9)]},
        {"locations": [point(radius_m=100001)]},
        {"locations": [point(label="")]},
        {"expected_version": True},
    ],
)
def test_invalid_zones_fail_without_saving(client, changes):
    assert save(client, **changes).status_code == 422
    assert client.get("/v1/location-zones").json() == {"items": []}


def test_zone_filter_matches_any_center_before_paging_and_preserves_original_gps(client):
    zone = save(client, locations=[point(radius_m=100), point(56, 10, 500)]).json()
    sources = [
        capture(client, **data).json()
        for data in [
            gps(),
            gps(latitude=56, longitude=10),
            gps(latitude=0, longitude=0),
            {},
        ]
    ]
    ids = {s["id"] for s in sources[:2]}
    query = {"zone_id": zone["id"], "limit": 1}
    first = client.get("/v1/transcripts", params=query).json()
    second = client.get("/v1/transcripts", params={**query, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert {first["items"][0]["id"], second["items"][0]["id"]} == ids
    for deduplicate in [False, True]:
        result = client.get(
            "/v1/transcripts", params={"zone_id": zone["id"], "deduplicate": deduplicate}
        ).json()
        assert {s["id"] for s in result["items"]} == ids
    original = sources[0]["source_metadata"]
    assert client.delete(f"/v1/location-zones/{zone['id']}?expected_version=1").status_code == 204
    assert client.get(f"/v1/sources/{sources[0]['id']}").json()["source_metadata"] == original
    assert client.get("/v1/transcripts", params=query).status_code == 404


def test_groups_and_expanded_sources_keep_the_same_zone_filter(client):
    zone = save(client).json()
    expected = set()
    for index, data in enumerate([gps(), gps(), gps(latitude=0, longitude=0), {}]):
        at = AT + timedelta(seconds=index * 30)
        if data:
            data["location_captured_at"] = stamp(at)
        source = capture(client, started_at=stamp(at), **data).json()
        if index < 2:
            expected.add(source["id"])
    params = {"day": str(AT.date()), "zone_id": zone["id"]}
    response = client.get("/v1/recording-groups", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total_recordings"] == 2
    assert result["total"] == 1
    group = result["items"][0]
    members = client.get(
        f"/v1/recording-groups/{group['id']}/sources", params={**params, "limit": 1}
    ).json()
    more = client.get(
        f"/v1/recording-groups/{group['id']}/sources", params={**params, "offset": 1}
    ).json()
    assert members["total"] == 2
    assert {s["id"] for s in members["items"] + more["items"]} == expected


def test_editing_radius_reclassifies_history_and_overlapping_zones_both_match(client):
    source = capture(client, **gps()).json()
    center = point(latitude=55.6779, radius_m=100)
    zone = save(client, locations=[center]).json()
    assert client.get("/v1/transcripts", params={"zone_id": zone["id"]}).json()["total"] == 0
    assert (
        save(
            client, key=zone["id"], locations=[{**center, "radius_m": 500}], expected_version=1
        ).status_code
        == 200
    )
    assert client.get("/v1/transcripts", params={"zone_id": zone["id"]}).json()["total"] == 1
    overlap = save(client, name="Gym").json()
    assert client.get("/v1/transcripts", params={"zone_id": overlap["id"]}).json()["total"] == 1
    assert (
        client.get(f"/v1/sources/{source['id']}").json()["source_metadata"]
        == source["source_metadata"]
    )


def test_zones_and_filters_are_profile_scoped(client):
    zone = save(client).json()
    other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    headers = {"X-Omi-Profile": other}
    assert client.get("/v1/location-zones", headers=headers).json() == {"items": []}
    assert (
        client.get("/v1/transcripts", headers=headers, params={"zone_id": zone["id"]}).status_code
        == 404
    )
    assert save(client, key=zone["id"], headers=headers).status_code == 200
    assert (
        client.delete(
            f"/v1/location-zones/{zone['id']}?expected_version=1", headers=headers
        ).status_code
        == 204
    )
    assert len(client.get("/v1/location-zones").json()["items"]) == 1


def test_distance_handles_antimeridian_poles_and_exact_boundary():
    assert contains(0, -179.9999, json.dumps([point(0, 179.9999, 30)])) == 1
    assert contains(90, 100, json.dumps([point(90, -100, 10)])) == 1
    assert contains(0, 0, json.dumps([point(0, 0, 10)])) == 1
    assert contains(None, None, json.dumps([point(0, 0, 10)])) == 0


def test_archive_backup_restores_zones_and_their_matching_behavior(client, tmp_path):
    import zipfile

    zone = save(client).json()
    capture(client, **gps())
    store = client.app.state.memory.store
    backup = store.export_archive()
    target = tmp_path / "restored-zone-archive"
    target.mkdir()
    try:
        with zipfile.ZipFile(backup) as archive:
            archive.extractall(target)
        restored = Store(target)
        assert restored.location_zones() == [zone]
        rows, count = restored.transcripts(None, None, "", 50, 0, zone_id=zone["id"])
        assert count == 1 and rows[0]["source_metadata"]
    finally:
        backup.unlink()
        backup.parent.rmdir()


def test_circle_boundary_is_inclusive_and_just_outside_is_excluded():
    import math

    circle = json.dumps([point(0, 0, 100)])
    assert contains(0, math.degrees(100 / 6371008.8), circle) == 1
    assert contains(0, math.degrees(100.01 / 6371008.8), circle) == 0
