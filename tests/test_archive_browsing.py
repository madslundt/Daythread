from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_recording_groups import client as client
from test_recording_groups import listing, recording


def test_archive_marks_local_days_and_lists_pending_expired_and_retained_text(client):
    assert client.patch("/v1/settings", json={"timezone": "Europe/Copenhagen"}).is_success
    start = datetime(2026, 10, 15, 22, tzinfo=UTC)
    pending = recording(client, start, text="", kind="manual_upload", level=1)
    expired = recording(client, start + timedelta(hours=1), kind="manual_upload", level=2)
    retained = recording(client, start + timedelta(hours=2), kind="manual_upload", level=3)
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute("UPDATE audio SET state='queued' WHERE id=?", (pending,))
        db.execute("UPDATE audio SET state='expired',text=NULL,audio_available=0 WHERE id=?",
                   (expired,))
        db.execute("UPDATE audio SET audio_available=0 WHERE id=?", (retained,))
    month = client.get("/v1/calendar?month=2026-10&archive=true").json()
    assert month["days"] == [
        {"day": "2026-10-16", "has_summary": False, "has_recordings": True},
    ]
    groups = listing(client, archive=True)["items"]
    assert [group["id"] for group in groups] == [pending, expired, retained]
    assert [group["states"] for group in groups] == [["queued"], ["expired"], ["done"]]
    assert groups[0]["has_audio"] and not groups[0]["has_transcript"]
    assert groups[2]["has_transcript"] and not groups[2]["has_audio"]
    assert listing(client, archive=True, day="2026-10-17")["items"] == []


def test_archive_search_deep_links_are_versioned_and_work_after_audio_expires(client):
    key = recording(client, text="An old conversation about café logistics.")
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute("UPDATE audio SET audio_available=0 WHERE id=?", (key,))
    response = client.get("/v1/archive/search", params={"q": "cafe"})
    assert response.is_success
    item, = response.json()["items"]
    assert item["id"] == key and "café logistics" in item["match"]["text"]
    match = item["match"]
    params = {"version": item["transcript_version"], "start": match["start_offset"],
              "end": match["end_offset"]}
    path = f"/v1/archive/sources/{key}"
    evidence = client.get(path, params=params).json()
    assert not evidence["source"]["audio_available"]
    assert evidence["source"]["text"][params["start"]:params["end"]] == match["text"]
    store.correct_transcript(key, "The corrected transcript uses entirely new wording.")
    assert client.get(path, params=params).status_code == 409
    assert client.get("/v1/archive/search", params={"q": "cafe"}).json()["items"] == []


def test_rewind_uses_local_day_and_elapsed_minutes_across_dst(client):
    assert client.patch("/v1/settings", json={"timezone": "Europe/Copenhagen"}).is_success
    key = recording(client, datetime(2026, 3, 29, 0, 55, tzinfo=UTC), duration=1)
    response = client.post("/v1/rewind", json={
        "day": "2026-03-29", "local_time": "03:05", "minutes": 15, "summarize": False,
    })
    assert response.is_success, response.text
    result = response.json()
    assert result["start"] == "2026-03-29T00:50:00.000000+00:00"
    assert {s["id"] for s in result["sources"]} == {key}
    assert client.get("/v1/rewind/audio", params={"end": result["end"], "minutes": 15}).is_success
    assert client.post("/v1/rewind", json={
        "day": "2026-03-29", "local_time": "02:05", "summarize": False,
    }).status_code == 422


def test_recording_chat_scope_excludes_neighbors_and_cannot_change(client):
    key = recording(client, text="Cobalt launch schedule for the requested conversation.")
    recording(client, text="Cobalt launch schedule for a different conversation.")
    store = client.app.state.memory.store
    session = store.ensure_session(None, "Cobalt", recording_id=key)
    response = client.post("/v1/search", json={"question": "Cobalt", "session_id": session["id"]})
    assert response.is_success, response.text
    assert response.json()["recording_id"] == key
    assert [s["id"] for s in response.json()["sources"]] == [key]
    assert client.post("/v1/search", json={
        "question": "Cobalt", "session_id": session["id"], "day": "2026-10-16",
    }).status_code == 409
    assert client.post("/v1/search", json={
        "question": "Cobalt", "recording_id": str(uuid4()),
    }).status_code == 404
