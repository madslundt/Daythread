"""Finished imports are explicitly scoped meetings without replaying live gestures."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_manual_upload import AT, audio_part, manifest, prepare, send_planned
from test_manual_upload import client as client

from omi_local.domain import ButtonEvent
from omi_local.store import Store


def test_import_choice_is_strict_and_ordinary_recordings_stay_ordinary(client):
    data = audio_part()
    for invalid in ("true", 1, None):
        response = client.post("/v1/recording-uploads/prepare",
                               json=manifest([data], treat_as_meeting=invalid))
        assert response.status_code == 422
    plan = prepare(client, manifest([data]))
    assert plan["treat_as_meeting"] is False
    assert send_planned(client, plan, data).status_code == 200
    assert client.get("/v1/timeline").json()["meetings"] == []


def test_old_interrupted_plan_explicitly_returns_ordinary_classification(client):
    body = manifest([audio_part()])
    plan = prepare(client, body)
    with client.app.state.memory.store.connect() as db:
        legacy = dict(plan)
        legacy.pop("treat_as_meeting")
        db.execute("UPDATE recording_uploads SET plan=?", (json.dumps(legacy),))
    resumed = prepare(client, body | {"treat_as_meeting": True})
    assert resumed["recording_id"] == plan["recording_id"]
    assert resumed["treat_as_meeting"] is False


def test_import_meeting_waits_for_all_parts_and_preserves_choice_across_retries(client):
    parts = [audio_part(120), audio_part()]
    body = manifest(parts, treat_as_meeting=True)
    plan = prepare(client, body)
    assert send_planned(client, plan, parts[1], 1).status_code == 200
    assert client.get("/v1/timeline").json()["meetings"] == []
    restarted = Store(client.app.state.memory.store.root)
    from omi_local.recording_uploads import PrepareUpload

    resumed = restarted.prepare_recording_upload(PrepareUpload.model_validate(
        body | {"treat_as_meeting": False, "source_id": "renamed.wav"},
    ))
    assert resumed["treat_as_meeting"] is True
    assert resumed["recording_id"] == plan["recording_id"]
    assert resumed["source_id"] == plan["source_id"]
    assert send_planned(client, resumed, parts[0]).status_code == 200
    meetings = client.get("/v1/timeline").json()["meetings"]
    assert len(meetings) == 1
    assert meetings[0]["id"] == plan["recording_id"]
    assert meetings[0]["started_at"] == body["started_at"]
    assert meetings[0]["ended_at"] == body["ended_at"]
    assert meetings[0]["source_ids"] == [part["id"] for part in plan["parts"]]
    assert prepare(client, body)["status"] == "duplicate"
    assert client.get("/v1/timeline").json()["meetings"] == meetings


def test_short_import_is_closed_and_does_not_interfere_with_live_meeting(client):
    memory = client.app.state.memory
    live_id = uuid4()
    memory.store.append_event(ButtonEvent(
        event_id=live_id, device_id="omi", occurred_at=AT - timedelta(minutes=1),
        action="meeting_toggle", meeting_intent="start",
    ), 120, 60)
    before = memory.timeline()
    data = audio_part()
    plan = prepare(client, manifest([data], treat_as_meeting=True))
    assert send_planned(client, plan, data).status_code == 200
    timeline = memory.timeline()
    assert timeline["active_meeting"]["id"] == before["active_meeting"]["id"]
    assert timeline["active_meeting"]["ended_at"] is None
    imported = next(m for m in timeline["meetings"] if m["id"] == plan["recording_id"])
    assert imported["status"] == "closed"
    assert imported["timing"] == "recorded"
    assert imported["end_reason"] == "imported_recording"
    assert imported["context_start"] == imported["started_at"]
    assert imported["context_end"] == imported["ended_at"]
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert memory.speakers.resolve("source", plan["parts"][0]["id"], db)["scope"] == {
            "kind": "meeting", "id": plan["recording_id"],
        }


def test_import_meeting_scopes_summary_chat_and_speakers_to_its_own_sources(client):
    memory = client.app.state.memory
    data = audio_part()
    plan = prepare(client, manifest([data], treat_as_meeting=True))
    assert send_planned(client, plan, data).status_code == 200
    member = plan["parts"][0]["id"]
    memory.store.complete(member, "Harbor release is Friday.", segments=[])
    other = str(uuid4())
    memory.store.ingest(other, "manual-web", AT, audio_part(level=2000), 1,
                        source_kind="manual_upload", source_id="unrelated.wav")
    memory.store.complete(other, "Harbor release is cancelled. Unrelated private audio.",
                          segments=[])

    class Inference:
        async def summarize(self, sources, purpose):
            assert [source["id"] for source in sources] == [member]
            return f"Release is Friday. [{member}]"

    memory.inference = Inference()
    response = client.post(f"/v1/meetings/{plan['recording_id']}/recap")
    assert response.status_code == 200, response.text
    assert [source["id"] for source in response.json()["sources"]] == [member]
    for question in ("What was discussed?", "Harbor release"):
        result = asyncio.run(memory.retrieve(question, 12, meeting_id=plan["recording_id"]))
        assert [source["id"] for source in result["sources"]] == [member]
    with memory.store.connect() as db:
        scope = memory.speakers.resolve("meeting", plan["recording_id"], db)
        assert [row["id"] for row in scope["rows"]] == [member]
        assert memory.speakers.resolve("source", member, db)["scope"] == {
            "kind": "meeting", "id": plan["recording_id"],
        }
        assert memory.speakers.resolve("source", other, db)["scope"]["kind"] == "source"
    sources = memory.window_data(AT, AT + timedelta(seconds=2))["sources"]
    assert next(s for s in sources if s["id"] == member)["meeting_hint_ids"] == [
        plan["recording_id"],
    ]
    assert next(s for s in sources if s["id"] == other)["meeting_hint_ids"] == []


@pytest.mark.parametrize("remove", ["delete", "expire"])
def test_import_meeting_importance_and_lifetime_follow_source(client, remove):
    data = audio_part()
    plan = prepare(client, manifest([data], treat_as_meeting=True))
    assert send_planned(client, plan, data).status_code == 200
    key = plan["parts"][0]["id"]
    target = {"kind": "meeting", "id": plan["recording_id"], "important": True}
    assert client.put("/v1/importance", json=target).status_code == 200
    if remove == "delete":
        assert client.delete(f"/v1/audio/{key}").status_code == 200
    else:
        with client.app.state.memory.store.connect() as db:
            db.execute("UPDATE audio SET state='expired',text=NULL WHERE id=?", (key,))
    assert client.get("/v1/timeline").json()["meetings"] == []
    assert client.get("/v1/importance").json()["items"] == []
    assert client.put("/v1/importance", json=target).status_code == 404
    assert client.get(f"/v1/speakers/meeting/{plan['recording_id']}").status_code == 404
    assert client.post(f"/v1/meetings/{plan['recording_id']}/recap").status_code == 404


def test_audio_expiry_keeps_transcript_backed_imported_meeting(client):
    data = audio_part()
    plan = prepare(client, manifest([data], treat_as_meeting=True))
    assert send_planned(client, plan, data).status_code == 200
    key = plan["parts"][0]["id"]
    store = client.app.state.memory.store
    store.complete(key, "Release Friday.", segments=[])
    with store.connect() as db:
        db.execute("UPDATE audio SET audio_available=0 WHERE id=?", (key,))
    assert client.get("/v1/timeline").json()["meetings"][0]["id"] == plan["recording_id"]
