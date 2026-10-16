"""Saved importance is manual metadata with archive lifecycle and retry guarantees."""

import importlib.util
import json
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_labels import apply, person, source
from test_labels import client as labels_client

from omi_local.domain import stamp, utc_now
from omi_local.store import Store


@pytest.fixture
def client(tmp_path):
    yield from labels_client.__wrapped__(tmp_path)


def mark(client, kind, key, important=True, section=""):
    return client.put(
        "/v1/importance",
        json={
            "kind": kind,
            "id": key,
            "important": important,
            "section": section,
        },
    )


def marks(client):
    response = client.get("/v1/importance")
    assert response.headers["cache-control"] == "no-store"
    return response.json()["items"]


def recap(client, summary="## Highlights\n- Discuss the garden\n   before Thursday."):
    key = str(uuid4())
    client.app.state.memory.store.save_diary(
        "2026-10-15",
        key,
        {
            "output_id": key,
            "day": "2026-10-15",
            "summary": summary,
            "sources": [],
        },
    )
    return key


def test_saved_source_toggle_is_idempotent_shared_and_survives_restart(client):
    src = source(client)
    revision = client.get("/v1/labels").json()["revision"]
    before = client.get(f"/v1/sources/{src['id']}").json()
    for _ in range(2):
        assert mark(client, "source", src["id"]).status_code == 200
    expected = [{"kind": "source", "id": src["id"], "section": ""}]
    assert marks(client) == expected
    assert Store(client.app.state.memory.store.root).importance_marks() == expected
    assert client.get(f"/v1/sources/{src['id']}").json() == before
    assert client.get("/v1/labels").json()["revision"] == revision
    for _ in range(2):
        assert mark(client, "source", src["id"], False).status_code == 200
    assert marks(client) == []


def test_source_marks_survive_correction_but_deleted_sources_cannot_be_resurrected(client):
    src = source(client)
    assert mark(client, "source", src["id"]).status_code == 200
    assert (
        client.patch(f"/v1/transcripts/{src['id']}", json={"text": "Corrected"}).status_code == 200
    )
    assert len(marks(client)) == 1
    assert client.delete(f"/v1/audio/{src['id']}").status_code == 200
    assert marks(client) == []
    assert mark(client, "source", src["id"]).status_code == 404
    assert marks(client) == []


def test_label_rename_keeps_mark_and_merge_transfers_then_delete_cleans_up(client):
    first, second = person(client, "First"), person(client, "Second")
    assert mark(client, "label", first).status_code == 200
    apply(client, "entity.save", first, value={"kind": "person", "name": "Renamed"})
    assert marks(client)[0]["id"] == first
    apply(client, "entity.merge", first, target_id=second)
    assert marks(client) == [{"kind": "label", "id": second, "section": ""}]
    assert mark(client, "label", first).status_code == 404
    apply(client, "entity.delete", second)
    assert marks(client) == []


def test_meeting_mark_does_not_create_or_replay_a_capture_gesture(client):
    start = utc_now() - timedelta(minutes=5)
    meeting = str(uuid4())
    for key, at in [(meeting, start), (str(uuid4()), start + timedelta(minutes=2))]:
        assert (
            client.post(
                "/v1/events",
                json={
                    "event_id": key,
                    "device_id": "test",
                    "occurred_at": stamp(at),
                    "action": "meeting_toggle",
                },
            ).status_code
            == 200
        )
    timeline = client.get("/v1/timeline").json()
    events = client.app.state.memory.store.events()
    assert mark(client, "meeting", meeting).status_code == 200
    assert client.get("/v1/timeline").json() == timeline
    assert client.app.state.memory.store.events() == events
    assert mark(client, "meeting", events[1]["event_id"]).status_code == 404
    assert mark(client, "meeting", meeting, False).status_code == 200
    assert marks(client) == []


def test_recap_section_marks_are_version_specific_and_removed_with_history(client):
    first = recap(client)
    section = "Discuss the garden before Thursday."
    assert mark(client, "recap", first, section=section).status_code == 200
    assert mark(client, "recap", first, section="Highlights").status_code == 200
    second = recap(client, "## Highlights\n- A different subject")
    assert all(item["id"] == first for item in marks(client))
    assert mark(client, "recap", second, section=section).status_code == 409
    assert mark(client, "recap", second).status_code == 200
    client.app.state.memory.store.invalidate_diaries(purge_history=True)
    assert marks(client) == []
    assert mark(client, "recap", first, section=section).status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "invalid"},
        {"kind": "unknown"},
        {"important": "true"},
        {"section": "A section on a recording"},
        {"extra": True},
    ],
)
def test_invalid_targets_and_payloads_are_rejected(client, changes):
    body = {"kind": "source", "id": str(uuid4()), "important": True, **changes}
    assert client.put("/v1/importance", json=body).status_code == 422
    assert marks(client) == []


def test_marks_are_included_in_archive_backup_restore(client, tmp_path):
    src, label, version = source(client), person(client), recap(client)
    for kind, key in [("source", src["id"]), ("label", label), ("recap", version)]:
        assert mark(client, kind, key).status_code == 200
    assert mark(client, "recap", version, section="Highlights").status_code == 200
    expected = marks(client)
    spec = importlib.util.spec_from_file_location("archive", Path("scripts/archive.py"))
    archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archive)
    backup = client.app.state.memory.store.export_archive()
    try:
        target = tmp_path / "restored"
        archive.restore(backup, target, 100_000_000)
        assert Store(target).importance_marks() == expected
    finally:
        shutil.rmtree(backup.parent)


def test_transcript_expiry_purges_recap_section_text_but_keeps_recording_mark(client):
    src, version = source(client), recap(client)
    assert mark(client, "source", src["id"]).status_code == 200
    assert mark(client, "recap", version, section="Highlights").status_code == 200
    store = client.app.state.memory.store
    store.apply_retention(0, 1, now=utc_now() + timedelta(days=100))
    assert marks(client) == [{"kind": "source", "id": src["id"], "section": ""}]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM diary_versions").fetchone()[0] == 0
        assert "Highlights" not in json.dumps(store.importance_marks())


def test_recap_sections_accept_displayed_markdown_labels_and_reject_empty_text(client):
    version = recap(client, "**To** follow **up**: share the plan, and confirm the time.")
    assert mark(client, "recap", version, section="To follow up").status_code == 200
    assert mark(client, "recap", version, section="confirm the time.").status_code == 200
    assert mark(client, "recap", version, section="   ").status_code == 422
    assert len(marks(client)) == 2
