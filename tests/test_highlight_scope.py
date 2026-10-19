"""Global day filters bound highlights to the same original evidence as recordings."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_conversation_tags import edit
from test_decisions import AT, content, create, source
from test_decisions import client as client
from test_inbox import TEXT, Model, proposal
from test_location_zones import point, save
from test_recording_locations import write

from omi_local.inbox_jobs import InboxJobs


def tag(client, recording, name="Work"):
    response = edit(client, f"/v1/sources/{recording['id']}/tags", action="add", name=name)
    assert response.status_code == 200, response.text
    return response.json()["tags"][0]["id"]


def setup_scope(client):
    a = source(client, TEXT)
    b = source(client, TEXT, AT + timedelta(hours=1))
    c = source(client, TEXT, AT + timedelta(hours=2))
    tag_id = tag(client, a)
    tag(client, b)
    tag(client, c, "Family")
    zone = save(client).json()
    for recording in (a, c):
        assert write(client, recording["id"], {
            "zone_id": zone["id"], "location_id": zone["locations"][0]["id"],
        }).status_code == 200
    return (a, b, c), {"tag_id": tag_id, "zone_id": zone["id"], "day": str(AT.date())}


def test_decisions_combine_filters_before_count_and_paging_and_recheck_location(client):
    recordings, scope = setup_scope(client)
    decisions = [create(client, s) for s in recordings]
    response = client.get("/v1/decisions", params={**scope, "limit": 1})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["total"] == 1
    assert [d["id"] for d in response.json()["items"]] == [decisions[0]["id"]]
    assert client.get("/v1/decisions", params={**scope, "offset": 1}).json()["items"] == []
    assert client.get("/v1/decisions", params={"tag_id": scope["tag_id"]}).json()["total"] == 2
    assert client.get("/v1/decisions", params={"zone_id": scope["zone_id"]}).json()["total"] == 2
    assert write(client, recordings[0]["id"], None, version=1).status_code == 200
    assert client.get("/v1/decisions", params=scope).json() == {"items": [], "total": 0}
    assert client.get("/v1/decisions").json()["total"] == 3


def test_decision_context_outside_selection_does_not_leak_through_matching_choice(client):
    (a, b, _), scope = setup_scope(client)
    mixed = content(a)
    mixed["context"] = content(b)["choice"]
    response = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": mixed})
    assert response.status_code == 200, response.text
    assert client.get("/v1/decisions", params=scope).json()["total"] == 0
    assert client.get("/v1/decisions", params={"tag_id": scope["tag_id"]}).json()["total"] == 1


def test_inbox_scope_bounds_items_counts_coverage_and_unscanned_sources(client):
    (a, b, c), scope = setup_scope(client)
    archive = client.app.state.memory.store
    # Each extraction window has one source; the model only returns the current window's item.
    jobs = InboxJobs(archive)
    jobs.discover("Europe/Copenhagen")
    for recording in (a, b, c):
        assert asyncio.run(jobs.process_one(Model([proposal(recording["id"])])))
    all_items = client.get("/v1/inbox").json()["items"]
    expected = next(i for i in all_items if i["source_ids"] == [a["id"]])
    with archive.connect() as db:
        db.execute(
            "UPDATE inbox_jobs SET state='failed' WHERE id IN "
            "(SELECT job_id FROM inbox_dependencies WHERE source_id=?)", (c["id"],),
        )
        db.execute("UPDATE inbox_sources SET scanned_revision='' WHERE source_id=?", (b["id"],))
    response = client.get("/v1/inbox", params={**scope, "limit": 1})
    assert response.status_code == 200, response.text
    value = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert [i["id"] for i in value["items"]] == [expected["id"]]
    assert value["total"] == value["pending"] == 1
    assert value["coverage"] == {"jobs": {"done": 1}, "unscanned_sources": 0}
    assert len(value["recent_jobs"]) == 1
    assert client.get("/v1/inbox", params={**scope, "offset": 1}).json()["items"] == []
    assert client.get("/v1/inbox", params={"tag_id": scope["tag_id"]}).json()["total"] == 2
    assert client.get("/v1/inbox").json()["coverage"]["jobs"]["failed"] == 1
    # Moving a zone reclassifies retained evidence without rewriting transcript versions.
    assert save(client, key=scope["zone_id"], expected_version=1,
                locations=[point(latitude=0, longitude=0)]).status_code == 200
    empty = client.get("/v1/inbox", params=scope).json()
    assert empty["items"] == [] and empty["total"] == empty["pending"] == 0
    assert empty["coverage"] == {"jobs": {}, "unscanned_sources": 0}
    assert empty["recent_jobs"] == []


def test_inbox_mixed_evidence_card_is_not_presented_as_selected_scope(client):
    (a, b, _), scope = setup_scope(client)
    archive = client.app.state.memory.store
    # A single proposal can summarize a multi-recording exchange.
    jobs = InboxJobs(archive)
    jobs.discover("Europe/Copenhagen")
    assert asyncio.run(jobs.process_one(Model([proposal(a["id"])])))
    item = client.get("/v1/inbox").json()["items"][0]
    with archive.connect() as db:
        row = db.execute(
            "SELECT * FROM inbox_occurrences WHERE item_id=?", (item["id"],)
        ).fetchone()
        evidence = json.loads(row["evidence"])
        evidence.append(evidence[0] | {"source_id": b["id"]})
        db.execute("UPDATE inbox_occurrences SET evidence=? WHERE item_id=?",
                   (json.dumps(evidence), item["id"]))
    assert client.get("/v1/inbox", params=scope).json()["total"] == 0
    assert client.get("/v1/inbox", params={"tag_id": scope["tag_id"]}).json()["total"] == 1


@pytest.mark.parametrize("path", ["/v1/inbox", "/v1/decisions"])
@pytest.mark.parametrize("field", ["tag_id", "zone_id"])
def test_unknown_deleted_and_other_profile_filters_fail_closed(client, path, field):
    recordings, scope = setup_scope(client)
    assert client.get(path, params={field: "invalid"}).status_code == 422
    assert client.get(path, params={field: str(uuid4())}).status_code == 404
    other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    assert client.get(path, params=scope, headers={"X-Omi-Profile": other}).status_code == 404
    if field == "zone_id":
        assert client.delete(
            f"/v1/location-zones/{scope[field]}?expected_version=1"
        ).status_code == 204
    else:
        for recording in recordings[:2]:
            assert edit(client, f"/v1/sources/{recording['id']}/tags", action="remove",
                        tag_id=scope[field]).status_code == 200
        # Reusable empty tags remain in the directory, but cannot widen the selection.
        value = client.get(path, params=scope).json()
        assert value["items"] == [] and value["total"] == 0
        return
    assert client.get(path, params=scope).status_code == 404
