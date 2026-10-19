"""Filtered recap synthesis uses matching originals, independently of full-day history."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_conversation_tags import edit, source_path
from test_location_zones import point
from test_location_zones import save as save_zone
from test_recaps import AT, close_after, insert
from test_recaps import client as client

from omi_local.scoped_recap_cache import ScopedRecapCache
from omi_local.summary_filter import NO_CONTENT

DAY = "2026-10-16"
PATH = f"/v1/recaps/{DAY}"


def tagged(client, key, name="Project"):
    response = edit(client, source_path({"id": key}), action="add", name=name)
    assert response.status_code == 200, response.text
    return response.json()["tags"][0]["id"]


def located(client, key, latitude=55.6761, longitude=12.5683):
    with client.app.state.memory.store.connect() as db:
        db.execute("UPDATE audio SET source_metadata=? WHERE id=?", (
            json.dumps({"location": {"latitude": latitude, "longitude": longitude}}), key,
        ))


def generate(client, **filters):
    response = client.post(PATH, params=filters)
    assert response.status_code == 200, response.text
    return response.json()


def test_scoped_synthesis_never_reuses_mixed_day_notes_or_overwrites_diary(client):
    selected = insert(client, text="Selected contract renewal", kind="manual_upload")
    excluded = insert(client, AT + timedelta(minutes=3), "Private excluded topic", "manual_upload")
    tag = tagged(client, selected)
    memory = client.app.state.memory
    full = generate(client)
    diary = memory.store.diary(DAY)
    versions = memory.store.diary_versions(DAY)
    assert excluded in full["summary"]
    memory.inference.calls.clear()
    filtered = generate(client, tag_id=tag)
    assert filtered["scope"] == {"tag_id": tag, "zone_id": None}
    assert {s["id"] for s in filtered["sources"]} == {selected}
    assert "Selected contract renewal" in filtered["summary"]
    assert excluded not in filtered["summary"]
    assert len(memory.inference.calls) == 2  # Fresh scoped notes, then scoped synthesis.
    for sources, _ in memory.inference.calls:
        assert {source["id"] for source in sources} <= {selected}
        assert all("Private excluded" not in source["text"] for source in sources)
    assert filtered["sources"][0]["started_at"] == full["sources"][0]["started_at"]
    assert filtered["sources"][0]["transcript_version"] == full["sources"][0]["transcript_version"]
    assert memory.store.diary(DAY) == diary
    assert memory.store.diary_versions(DAY) == versions
    assert client.get(PATH).json()["summary"] == full["summary"]
    before = len(memory.inference.calls)
    assert generate(client, tag_id=tag)["summary"] == filtered["summary"]
    assert len(memory.inference.calls) == before


def test_tag_and_zone_intersect_all_originals_before_timeline_paging(client):
    both = insert(client, text="Both filters match", kind="manual_upload")
    tag_only = insert(client, AT + timedelta(minutes=3), "Elsewhere", "manual_upload")
    place_only = insert(client, AT + timedelta(minutes=6), "Other project", "manual_upload")
    tag = tagged(client, both)
    assert tagged(client, tag_only) == tag
    for key in (both, place_only):
        located(client, key)
    zone = save_zone(client).json()["id"]
    result = generate(client, tag_id=tag, zone_id=zone)
    assert {s["id"] for s in result["sources"]} == {both}
    assert result["completeness"] is None
    assert result["no_matching_sources"] is False
    assert {s["id"] for s in generate(client, tag_id=tag)["sources"]} == {both, tag_only}
    assert {s["id"] for s in generate(client, zone_id=zone)["sources"]} == {both, place_only}
    timeline = client.get("/v1/recording-groups", params={
        "day": DAY, "limit": 1, "tag_id": tag, "zone_id": zone,
    }).json()
    assert {s["id"] for s in timeline["items"][0]["sources"]} == {both}


def test_matching_duplicate_is_not_lost_by_unfiltered_deduplication(client):
    first = insert(client, text="Duplicate evidence", kind="manual_upload")
    store = client.app.state.memory.store
    duplicate = str(uuid4())
    store.ingest(duplicate, "omi", AT, store.read_audio(first), 60,
                 source_kind="manual_upload")
    store.complete(duplicate, "Duplicate evidence")
    tag = tagged(client, duplicate)
    result = generate(client, tag_id=tag)
    assert {s["id"] for s in result["sources"]} == {duplicate}


def test_original_conversation_readiness_is_preserved_after_filtering(client):
    selected = insert(client, text="Selected conversation start")
    excluded = insert(client, AT + timedelta(minutes=1), "Excluded continuation")
    tag = tagged(client, selected)
    pending = client.get(PATH, params={"tag_id": tag}).json()
    assert pending["summary"] is None and pending["stale"] is False
    assert pending["no_matching_sources"] is False
    assert pending["waiting_for_pause"] == 1 and pending["generation_job_id"] is None
    assert client.app.state.memory.inference.calls == []
    close_after(client, AT + timedelta(minutes=2))
    completed = generate(client, tag_id=tag)
    assert {source["id"] for source in completed["sources"]} == {selected}
    assert excluded not in completed["summary"]


def test_membership_and_zone_definition_changes_hide_cached_prose(client):
    first = insert(client, text="First scope", kind="manual_upload")
    second = insert(client, AT + timedelta(minutes=3), "Replacement scope", "manual_upload")
    tag = tagged(client, first)
    old = generate(client, tag_id=tag)
    edit(client, source_path({"id": first}), action="remove", tag_id=tag)
    assert tagged(client, second) == tag
    fresh = client.get(PATH, params={"tag_id": tag}).json()
    assert fresh["summary"] is None and fresh["sources"] == [] and fresh["stale"] is True
    assert fresh["scope_fingerprint"] != old["scope_fingerprint"]
    assert generate(client, tag_id=tag)["sources"][0]["id"] == second
    located(client, first)
    zone = save_zone(client).json()
    old = generate(client, zone_id=zone["id"])
    save_zone(client, key=zone["id"], expected_version=1, locations=[point(0, 0)])
    fresh = client.get(PATH, params={"zone_id": zone["id"]}).json()
    assert fresh["summary"] is None and fresh["no_matching_sources"] is True
    assert fresh["scope_fingerprint"] != old["scope_fingerprint"]


def test_no_matches_no_content_and_invalid_filters_never_fall_back(client):
    key = insert(client, kind="manual_upload")
    tagged(client, key)
    zone = save_zone(client).json()["id"]
    full = generate(client)
    calls = len(client.app.state.memory.inference.calls)
    empty = client.get(PATH, params={"zone_id": zone})
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json()["no_matching_sources"] is True
    assert empty.json()["summary"] is None and empty.json()["generation_job_id"] is None
    assert len(client.app.state.memory.inference.calls) == calls
    for method in (client.get, client.post):
        assert method(PATH, params={"tag_id": uuid4()}).status_code == 404
        assert method(PATH, params={"zone_id": uuid4()}).status_code == 404
        assert method(PATH, params={"tag_id": "bad"}).status_code == 422
    tag = tagged(client, key)

    async def no_content(*args):
        return NO_CONTENT

    client.app.state.memory.inference.summarize = no_content
    quiet = generate(client, tag_id=tag)
    assert quiet["summary"] is None and quiet["no_recap_content"] is True
    assert [s["id"] for s in quiet["sources"]] == [key]
    assert client.get(PATH).json()["summary"] == full["summary"]


@pytest.mark.parametrize("mutation", ["membership", "zone", "transcript"])
def test_scope_changes_during_synthesis_cannot_commit_a_result(client, mutation):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    located(client, key)
    zone = save_zone(client).json()
    memory = client.app.state.memory
    original = memory.inference.summarize

    async def changing(sources, purpose):
        answer = await original(sources, purpose)
        if len(memory.inference.calls) == 2:
            if mutation == "membership":
                with memory.store.connect() as db:
                    db.execute("DELETE FROM source_tags WHERE source_id=? AND tag_id=?", (key, tag))
            elif mutation == "zone":
                with memory.store.connect() as db:
                    db.execute("UPDATE location_zones SET version=version+1,locations=? WHERE id=?",
                               (json.dumps([point(0, 0)]), zone["id"]))
            else:
                memory.store.complete(key, "Corrected original")
        return answer

    memory.inference.summarize = changing
    response = client.post(PATH, params={"tag_id": tag, "zone_id": zone["id"]})
    assert response.status_code == 409, response.text
    assert memory.store.diary(DAY) is None
    with memory.store.connect() as db:
        assert db.execute(
            "SELECT count(*) FROM scoped_recap_cache WHERE fingerprint LIKE 'day:%'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["correct", "delete", "expire"])
def test_source_privacy_changes_purge_disposable_scope_text(client, mutation):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    generate(client, tag_id=tag)
    store = client.app.state.memory.store
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM scoped_recap_cache").fetchone()[0] == 2
        if mutation == "delete":
            db.execute("DELETE FROM audio WHERE id=?", (key,))
        elif mutation == "expire":
            db.execute("UPDATE audio SET state='expired',text=NULL WHERE id=?", (key,))
        else:
            db.execute("UPDATE audio SET text='Corrected' WHERE id=?", (key,))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM scoped_recap_cache").fetchone()[0] == 0


def test_scope_cache_is_bounded_and_survives_archive_reopen(client):
    from omi_local.store import Store

    store = client.app.state.memory.store
    cache = ScopedRecapCache(store)
    for index in range(70):
        cache.save(str(index), {"summary": str(index)}, [], lambda db: None)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM scoped_recap_cache").fetchone()[0] == 64
    assert ScopedRecapCache(Store(store.root)).get("69") == {"summary": "69"}


def test_scope_uses_timezone_day_overlap_with_original_timestamps(client, monkeypatch):
    memory = client.app.state.memory
    memory.settings.timezone = "Europe/Copenhagen"
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT + timedelta(days=1))
    # Copenhagen's capture day starts at 22:00 UTC the preceding date in October.
    outside = insert(client, AT - timedelta(hours=14, minutes=1), "Before day", "manual_upload")
    crossing = insert(client, AT - timedelta(hours=14, seconds=30), "Crosses boundary",
                      "manual_upload")
    start = insert(client, AT - timedelta(hours=14), "At day start", "manual_upload")
    late = insert(client, AT + timedelta(hours=9, minutes=59), "At day end", "manual_upload")
    after = insert(client, AT + timedelta(hours=10), "Next day", "manual_upload")
    tag = tagged(client, crossing)
    for key in (outside, start, late, after):
        assert tagged(client, key) == tag
    result = generate(client, tag_id=tag)
    assert {s["id"] for s in result["sources"]} == {crossing, start, late}
    assert result["start"] == "2026-10-15T22:00:00.000000+00:00"
    assert result["end"] == "2026-10-16T22:00:00.000000+00:00"
    assert result["sources"][0]["started_at"] == memory.store.get_audio(crossing)["started_at"]


def test_scope_identity_includes_inference_language_and_timezone(client):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    old = generate(client, tag_id=tag)
    memory = client.app.state.memory
    memory.settings.summary_language = "da"
    changed = memory.cached_recap(AT.date(), tag_id=tag)
    assert changed["summary"] is None and changed["stale"] is True
    assert changed["scope_fingerprint"] != old["scope_fingerprint"]
    memory.settings.summary_language = "en"
    memory.settings.timezone = "Europe/Copenhagen"
    changed = memory.cached_recap(AT.date(), tag_id=tag)
    assert changed["scope_fingerprint"] != old["scope_fingerprint"]


def test_saved_place_override_uses_same_zone_membership_as_timeline(client):
    key = insert(client, kind="manual_upload")
    located(client, key, latitude=0, longitude=0)
    zone = save_zone(client).json()
    assert client.get(PATH, params={"zone_id": zone["id"]}).json()["no_matching_sources"] is True
    response = client.put(f"/v1/sources/{key}/location", json={
        "expected_version": 0,
        "location": {"zone_id": zone["id"], "location_id": zone["locations"][0]["id"]},
    })
    assert response.status_code == 200, response.text
    result = generate(client, zone_id=zone["id"])
    assert [s["id"] for s in result["sources"]] == [key]
    assert result["sources"][0]["source_metadata"]["location"]["latitude"] == 0
