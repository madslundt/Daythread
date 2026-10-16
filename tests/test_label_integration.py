"""Labels coexist with the existing decision, source-version and cache contracts."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_labels import AT, apply, command, note, person, source, timeline
from test_labels import client as labels_client


@pytest.fixture
def client(tmp_path):
    yield from labels_client.__wrapped__(tmp_path)


def register_decision(client, src):
    text = src["text"]
    response = client.post(
        "/v1/decisions",
        json={
            "command_id": str(uuid4()),
            "content": {
                "choice": {
                    "text": "Plant an oak tree",
                    "evidence": [
                        {
                            "source_id": src["id"],
                            "source_version": src["source_version"],
                            "quote": text,
                            "start": 0,
                            "end": len(text),
                        }
                    ],
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_label_uses_shared_tokens_and_links_live_register_without_changing_decision(client):
    entity, src = person(client), source(client)
    decision = register_decision(client, src)
    association = apply(
        client,
        "decision_link.save",
        entity_id=entity,
        decision_id=decision["id"],
        decision_version=decision["version"],
    )
    entry = timeline(client, entity)["items"][0]
    assert entry["entry_type"] == "decision" and entry["decision"]["review_status"] == "proposed"
    assert entry["sources"][0]["source_version"] == src["source_version"]
    confirmed = client.post(
        f"/v1/decisions/{decision['id']}/events",
        json={
            "command_id": str(uuid4()),
            "expected_version": decision["version"],
            "type": "confirm",
        },
    ).json()
    assert confirmed["review_status"] == "confirmed"
    assert timeline(client, entity)["items"][0]["needs_review"]
    apply(
        client,
        "decision_link.save",
        association,
        entity_id=entity,
        decision_id=decision["id"],
        decision_version=confirmed["version"],
    )
    assert not timeline(client, entity)["items"][0]["needs_review"]
    with client.app.state.memory.store.connect() as db:
        assert "revision" not in {r["name"] for r in db.execute("PRAGMA table_info(audio)")}
        assert db.execute("SELECT count(*) FROM label_notes").fetchone()[0] == 0
    apply(client, "entity.delete", entity)
    assert client.get(f"/v1/decisions/{decision['id']}").json()["content"] is not None


def test_linked_decision_deletion_and_source_purge_cannot_leave_copied_register_content(client):
    entity, src = person(client), source(client)
    decision = register_decision(client, src)
    apply(
        client,
        "decision_link.save",
        entity_id=entity,
        decision_id=decision["id"],
        decision_version=decision["version"],
    )
    assert client.delete(f"/v1/audio/{src['id']}").status_code == 200
    assert not timeline(client, entity)["items"]
    assert client.get(f"/v1/decisions/{decision['id']}").json()["content"] is None


def test_older_recordings_are_reachable_by_source_picker_pages(client):
    for index in range(55):
        source(client, AT - timedelta(minutes=index))
    first = client.get("/v1/transcripts", params={"q": "Alex", "limit": 50}).json()
    older = client.get("/v1/transcripts", params={"q": "Alex", "limit": 50, "offset": 50}).json()
    assert first["total"] == older["total"] == 55
    assert len(first["items"]) == 50 and len(older["items"]) == 5
    assert not {s["id"] for s in first["items"]} & {s["id"] for s in older["items"]}
    entity = person(client)
    note_id = note(client, entity, [first["items"][0], older["items"][-1]], "conversation")
    assert (
        len(next(i for i in timeline(client, entity)["items"] if i["id"] == note_id)["sources"])
        == 2
    )


def test_sensitive_label_reads_and_rejected_writes_are_not_cached(client):
    entity, src = person(client), source(client)
    for path in ["/v1/labels", f"/v1/labels/{entity}/timeline", f"/v1/labels/sources/{src['id']}"]:
        assert client.get(path).headers["cache-control"] == "no-store"
    body = command(client, "entity.save", entity, value={"kind": "person", "name": "New"})
    body["expected_revision"] = "0" * 32
    assert client.post("/v1/labels/mutations", json=body).headers["cache-control"] == "no-store"
    schema = client.get("/v1/openapi").json()
    assert schema["paths"]["/v1/labels"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("DirectoryResponse")
