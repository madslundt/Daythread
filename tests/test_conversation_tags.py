"""Manual tags categorize saved conversation evidence without changing capture location."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_backend import wav
from test_location_zones import save as save_zone
from test_locations import capture, gps
from test_manual_upload import AT
from test_manual_upload import client as client

from omi_local.domain import stamp


def source_path(source):
    return f"/v1/sources/{source['id']}/tags"


def edit(client, path, *, snapshot=None, **action):
    snapshot = snapshot or client.get(path).json()
    return client.put(
        path,
        json={
            "expected_revision": snapshot["revision"],
            "expected_membership": snapshot["membership"],
            **action,
        },
    )


def group_sources(client, locations):
    result = []
    for index, location in enumerate(locations):
        at = AT + timedelta(seconds=index * 30)
        data = gps(latitude=location[0], longitude=location[1], location_captured_at=stamp(at))
        result.append(capture(client, started_at=stamp(at), **data).json())
    return result


def test_tags_preserve_gps_transcript_versions_and_upload_retry_identity(client):
    source = capture(client, **gps()).json()
    store = client.app.state.memory.store
    store.complete(source["id"], "We discussed a training plan at the office.")
    before = client.get(f"/v1/sources/{source['id']}").json()
    result = edit(client, source_path(source), action="add", name="Training")
    assert result.status_code == 200, result.text
    assert result.json()["tags"][0]["name"] == "Training"
    after = client.get(f"/v1/sources/{source['id']}").json()
    for field in ["source_metadata", "source_version", "text", "started_at", "ended_at"]:
        assert after[field] == before[field]
    assert after["tags"][0]["name"] == "Training"
    assert capture(client, source["id"], **gps()).status_code == 200
    tag_id = result.json()["tags"][0]["id"]
    removed = edit(client, source_path(source), action="remove", tag_id=tag_id)
    assert removed.status_code == 200
    assert removed.json()["tags"] == []
    assert (
        client.get(f"/v1/sources/{source['id']}").json()["source_metadata"]
        == before["source_metadata"]
    )


def test_reusable_casefolded_tags_and_incremental_edits(client):
    a, b = group_sources(client, [(55.6761, 12.5683), (0, 0)])
    first = edit(client, source_path(a), action="add", name="  Family   plans  ").json()
    second = edit(client, source_path(b), action="add", name="family plans").json()
    assert first["tags"][0]["id"] == second["tags"][0]["id"]
    assert len(client.get("/v1/tags").json()["items"]) == 1
    added = edit(client, source_path(a), action="add", name="Planning").json()
    assert {tag["name"] for tag in added["tags"]} == {"Family plans", "Planning"}
    directory = client.get("/v1/tags")
    assert directory.headers["cache-control"] == "no-store"
    assert directory.json()["items"][0]["source_count"] == 2


def test_conversation_tagging_includes_filtered_and_unpaged_members(client):
    sources = group_sources(client, [(55.6761, 12.5683), (0, 0), (55.6761, 12.5683)])
    zone = save_zone(client).json()
    params = {"day": str(AT.date()), "zone_id": zone["id"], "limit": 1}
    group = client.get("/v1/recording-groups", params=params).json()["items"][0]
    path = f"/v1/recording-groups/{group['id']}/tags?day={AT.date()}"
    snapshot = client.get(path).json()
    assert snapshot["source_count"] == 3
    result = edit(client, path, snapshot=snapshot, action="add", name="Work discussion")
    assert result.status_code == 200, result.text
    assert result.json()["tags"][0]["source_count"] == 3
    for source in sources:
        assert client.get(source_path(source)).json()["tags"][0]["name"] == "Work discussion"


def test_duplicate_upload_times_do_not_bridge_separate_conversations_for_tagging(client):
    store = client.app.state.memory.store
    ids = [str(uuid4()) for _ in range(3)]
    for key, minute, level in zip(ids, [0, 4, 8], [7, 7, 8], strict=True):
        store.ingest(
            key,
            "web",
            AT + timedelta(minutes=minute),
            wav([level]),
            1 / 16000,
            source_kind="manual_upload",
        )
        store.complete(key, "A discussion")
    groups = client.get("/v1/recording-groups", params={"day": str(AT.date())}).json()
    assert groups["total"] == 2
    path = f"/v1/recording-groups/{ids[0]}/tags?day={AT.date()}"
    result = edit(client, path, action="add", name="Planning")
    assert result.status_code == 200, result.text
    assert result.json()["source_count"] == 2
    assert client.get(f"/v1/sources/{ids[1]}/tags").json()["tags"][0]["name"] == "Planning"
    assert client.get(f"/v1/sources/{ids[2]}/tags").json()["tags"] == []
    assert client.get(f"/v1/recording-groups/{ids[1]}/tags?day={AT.date()}").json() == result.json()
    edit(client, f"/v1/sources/{ids[1]}/tags", action="add", name="Alternate only")
    groups = client.get("/v1/recording-groups", params={"day": str(AT.date())}).json()
    first_group = next(group for group in groups["items"] if group["id"] == ids[0])
    assert {tag["name"] for tag in first_group["tags"]} == {"Planning", "Alternate only"}


def test_late_recording_blocks_stale_membership_without_creating_a_tag(client):
    sources = group_sources(client, [(55.6761, 12.5683)])
    path = f"/v1/recording-groups/{sources[0]['id']}/tags?day={AT.date()}"
    snapshot = client.get(path).json()
    at = AT + timedelta(seconds=30)
    capture(client, started_at=stamp(at), **gps(location_captured_at=stamp(at)))
    assert edit(client, path, snapshot=snapshot, action="add", name="Planning").status_code == 409
    assert client.get("/v1/tags").json()["items"] == []


def test_later_members_show_partial_coverage_without_inheriting_tags(client):
    first = group_sources(client, [(55.6761, 12.5683)])[0]
    edit(client, source_path(first), action="add", name="Planning")
    at = AT + timedelta(seconds=30)
    second = capture(client, started_at=stamp(at), **gps(location_captured_at=stamp(at))).json()
    path = f"/v1/recording-groups/{first['id']}/tags?day={AT.date()}"
    value = client.get(path).json()
    assert value["source_count"] == 2 and value["tags"][0]["source_count"] == 1
    assert client.get(source_path(second)).json()["tags"] == []
    assert (
        edit(client, path, action="add", tag_id=value["tags"][0]["id"]).json()["tags"][0][
            "source_count"
        ]
        == 2
    )


def test_conflicting_edits_fail_without_overwriting_existing_tags(client):
    source = capture(client).json()
    snapshot = client.get(source_path(source)).json()
    assert (
        edit(
            client, source_path(source), snapshot=snapshot, action="add", name="Family"
        ).status_code
        == 200
    )
    assert (
        edit(
            client, source_path(source), snapshot=snapshot, action="add", name="Planning"
        ).status_code
        == 409
    )
    assert client.get("/v1/tags").json()["items"][0]["name"] == "Family"


@pytest.mark.parametrize(
    "action",
    [
        {"action": "add", "name": "  "},
        {"action": "add", "name": "bad\x00tag"},
        {"action": "add", "name": "x" * 81},
        {"action": "add"},
        {"action": "remove", "name": "Family"},
        {"action": "replace", "name": "Family"},
        {"action": "add", "name": "Family", "tag_id": str(uuid4())},
    ],
)
def test_invalid_tags_are_rejected(client, action):
    source = capture(client).json()
    assert edit(client, source_path(source), **action).status_code == 422
    assert client.get("/v1/tags").json()["items"] == []


def test_tags_and_location_filters_combine_before_paging_and_group_expansion(client):
    a, b, c = group_sources(client, [(55.6761, 12.5683), (0, 0), (55.6761, 12.5683)])
    added = edit(client, source_path(a), action="add", name="Training").json()
    tag_id = added["tags"][0]["id"]
    edit(client, source_path(b), action="add", tag_id=tag_id)
    zone = save_zone(client).json()
    for deduplicate in [False, True]:
        params = {"zone_id": zone["id"], "tag_id": tag_id, "deduplicate": deduplicate, "limit": 1}
        result = client.get("/v1/transcripts", params=params).json()
        assert result["total"] == 1 and result["items"][0]["id"] == a["id"]
    for offset in [0, 1]:
        result = client.get(
            "/v1/transcripts", params={"tag_id": tag_id, "limit": 1, "offset": offset}
        ).json()
        assert result["total"] == 2 and result["items"][0]["id"] in {a["id"], b["id"]}
    params = {"day": str(AT.date()), "tag_id": tag_id, "zone_id": zone["id"]}
    groups = client.get("/v1/recording-groups", params=params).json()
    assert groups["total_recordings"] == 1
    group = groups["items"][0]
    assert group["tags"][0]["id"] == tag_id
    members = client.get(f"/v1/recording-groups/{group['id']}/sources", params=params).json()
    assert [s["id"] for s in members["items"]] == [a["id"]]
    assert client.get(source_path(c)).json()["tags"] == []
    assert client.get("/v1/transcripts", params={"tag_id": str(uuid4())}).status_code == 404


def test_tags_survive_correction_but_expiry_and_deletion_remove_assignments(client):
    source = capture(client, **gps()).json()
    store = client.app.state.memory.store
    store.complete(source["id"], "Original transcript")
    edit(client, source_path(source), action="add", name="Family")
    store.correct_transcript(source["id"], "Corrected transcript")
    assert client.get(source_path(source)).json()["tags"][0]["name"] == "Family"
    store.apply_retention(1, 0, now=AT + timedelta(days=3))
    assert client.get(source_path(source)).json()["tags"]
    store.apply_retention(1, 1, now=AT + timedelta(days=3))
    assert client.get(source_path(source)).status_code == 404
    assert client.get("/v1/tags").json()["items"][0]["source_count"] == 0
    second = capture(client).json()
    edit(client, source_path(second), action="add", name="Family")
    client.delete(f"/v1/audio/{second['id']}")
    assert client.get("/v1/tags").json()["items"][0]["source_count"] == 0


def test_profile_isolation_and_source_stability_after_regrouping(client):
    a, b = group_sources(client, [(55.6761, 12.5683), (0, 0)])
    path = f"/v1/recording-groups/{a['id']}/tags?day={AT.date()}"
    tag = edit(client, path, action="add", name="Planning").json()["tags"][0]
    other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    headers = {"X-Omi-Profile": other}
    assert client.get("/v1/tags", headers=headers).json()["items"] == []
    assert client.get(source_path(a), headers=headers).status_code == 404
    assert (
        client.get("/v1/transcripts", params={"tag_id": tag["id"]}, headers=headers).status_code
        == 404
    )
    client.delete(f"/v1/audio/{a['id']}")
    regrouped = client.get("/v1/recording-groups", params={"day": str(AT.date())}).json()
    assert regrouped["items"][0]["id"] == b["id"]
    assert regrouped["items"][0]["tags"][0]["name"] == "Planning"


def test_archive_backup_restores_tags_without_moving_the_recording(client, tmp_path):
    import zipfile

    from omi_local.store import Store

    source = capture(client, **gps()).json()
    edit(client, source_path(source), action="add", name="Training")
    backup = client.app.state.memory.store.export_archive()
    destination = tmp_path / "restored-tags"
    destination.mkdir()
    try:
        with zipfile.ZipFile(backup) as archive:
            archive.extractall(destination)
        restored = Store(destination)
        value = restored.get_audio(source["id"])
        assert value["tags"][0]["name"] == "Training"
        assert restored.conversation_tags(source["id"])["source_count"] == 1
        assert (
            value["source_metadata"]
            == client.app.state.memory.store.get_audio(source["id"])["source_metadata"]
        )
    finally:
        backup.unlink()
        backup.parent.rmdir()


def test_group_add_is_atomic_when_one_recording_has_reached_its_tag_limit(client):
    first, second = group_sources(client, [(55.6761, 12.5683), (0, 0)])
    for index in range(50):
        assert (
            edit(client, source_path(first), action="add", name=f"Tag {index}").status_code == 200
        )
    group = f"/v1/recording-groups/{first['id']}/tags?day={AT.date()}"
    assert edit(client, group, action="add", name="Another tag").status_code == 422
    assert client.get(source_path(second)).json()["tags"] == []
    assert len(client.get("/v1/tags").json()["items"]) == 50
