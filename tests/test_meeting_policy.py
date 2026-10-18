"""Meetings end on verified quiet, never on elapsed duration or missing uploads."""

import io
import json
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.configuration import descriptors
from omi_local.conversation_state import conversations, meeting_timeline
from omi_local.domain import ButtonEvent, project_events, stamp
from omi_local.settings import Settings
from omi_local.voice_activity import VERSION

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


def event(at=AT, **fields):
    return {"event_id": str(uuid4()), "device_id": "omi", "occurred_at": stamp(at),
            "action": "meeting_toggle", "max_minutes": 1, "context_seconds": 0,
            "hint_policy": "{}", **fields}


def row(start, end, *, device="omi", kind="", text=False, status="observed", speech=None):
    return {"id": str(uuid4()), "device_id": device, "started_at": stamp(start),
            "ended_at": stamp(end), "state": "done", "source_kind": kind,
            "text": text, "source_version": 1, "transcription_details": json.dumps({
                "voice_activity": {"version": VERSION, "status": status,
                                   "speech": speech or [],
                                   "non_speech": [[0, (end - start).total_seconds()]]},
            })}


def timeline(rows, *, events=None, gaps=None, minutes=75):
    return meeting_timeline({"events": events or [event()], "rows": rows, "gaps": gaps or []},
                            AT + timedelta(minutes=minutes))


def test_meeting_has_no_duration_cap_and_fixed_policy_ignores_old_snapshots():
    start = event(hint_policy=json.dumps({"meeting_min_seconds": 3600}))
    state = project_events([start], AT + timedelta(minutes=180))
    assert state["active_meeting"]["auto_stop_at"] is None
    assert state["active_meeting"]["qualifies_at"] == stamp(AT + timedelta(seconds=60))
    assert state["meetings"][0]["ended_at"] is None


def test_ten_minutes_quiet_counts_only_after_first_hour():
    quiet = row(AT + timedelta(minutes=50), AT + timedelta(minutes=70))
    assert timeline([quiet], minutes=69)["active_meeting"]
    state = timeline([quiet])
    assert state["active_meeting"] is None
    assert state["meetings"][0]["ended_at"] == stamp(AT + timedelta(minutes=70))
    assert state["meetings"][0]["end_reason"] == "observed_non_speech"


def test_contiguous_quiet_chunks_join_and_speech_resets_the_wait():
    rows = [row(AT + timedelta(minutes=n), AT + timedelta(minutes=n + 2))
            for n in range(60, 72, 2)]
    voiced = row(AT + timedelta(minutes=65), AT + timedelta(minutes=66), text=True)
    assert timeline(rows + [voiced])["active_meeting"]
    rows += [row(AT + timedelta(minutes=72), AT + timedelta(minutes=76))]
    state = timeline(rows + [voiced], minutes=77)
    assert state["meetings"][0]["ended_at"] == stamp(AT + timedelta(minutes=76))


@pytest.mark.parametrize("case", ["absent", "gap", "upload_hole", "unknown", "pending",
                                 "webhook", "import", "other_device", "voiced_overlap"])
def test_unknown_or_unrelated_intervals_never_prove_continuous_quiet(case):
    start, end = AT + timedelta(minutes=60), AT + timedelta(minutes=70)
    rows, gaps = [row(start, end)], []
    if case == "absent":
        rows = []
    elif case == "gap":
        gaps = [{"device_id": "omi", "started_at": stamp(start + timedelta(minutes=5)),
                 "ended_at": stamp(start + timedelta(minutes=6))}]
    elif case == "upload_hole":
        rows = [row(start, start + timedelta(minutes=5)),
                row(start + timedelta(minutes=6), end)]
    elif case == "unknown":
        rows[0]["transcription_details"] = "{}"
    elif case == "pending":
        rows[0]["state"] = "queued"
    elif case == "webhook":
        rows[0]["source_kind"] = "omi_audio_webhook"
    elif case == "import":
        rows[0]["source_kind"] = "manual_upload"
    elif case == "other_device":
        rows[0]["device_id"] = "another_phone"
    else:
        rows += [row(start + timedelta(minutes=5), start + timedelta(minutes=6),
                     speech=[[0, 60]])]
    assert timeline(rows, gaps=gaps)["active_meeting"]


def test_manual_end_anytime_and_explicit_end_after_auto_close_never_reopens():
    start = event(hint_policy=json.dumps({"meeting_intent": "start"}))
    end = event(AT + timedelta(minutes=72), hint_policy=json.dumps({
        "meeting_intent": "end", "meeting_id": start["event_id"],
    }))
    state = timeline([row(AT + timedelta(minutes=60), AT + timedelta(minutes=70))],
                     events=[end, start])
    assert len(state["meetings"]) == 1
    assert state["active_meeting"] is None
    assert state["meetings"][0]["end_reason"] == "observed_non_speech"
    end["occurred_at"] = stamp(AT + timedelta(minutes=2))
    manual = timeline([], events=[end, start])
    assert manual["meetings"][0]["end_reason"] == "button"


def test_remote_meeting_uses_explicit_capture_association_preserving_button_provenance():
    start = event(device_id="web", hint_policy=json.dumps({
        "meeting_intent": "start", "meeting_capture_device_id": "omi",
    }))
    quiet = row(AT + timedelta(minutes=60), AT + timedelta(minutes=70))
    state = timeline([quiet], events=[start])
    assert state["active_meeting"] is None
    assert state["meetings"][0]["device_id"] == "web"
    assert state["meetings"][0]["meeting_capture_device_id"] == "omi"
    assert timeline([quiet], events=[event(device_id="web")])["active_meeting"]


def test_remote_associated_open_meeting_keeps_speaker_conversation_together():
    start = event(device_id="web", hint_policy=json.dumps({
        "meeting_intent": "start", "meeting_capture_device_id": "omi",
    }))
    snapshot = {"events": [start], "rows": [
        row(AT, AT + timedelta(minutes=1), text=True),
        row(AT + timedelta(minutes=1), AT + timedelta(minutes=8)),
    ], "gaps": []}
    groups = conversations(snapshot, AT + timedelta(minutes=9), 300)
    assert len(groups) == 1
    assert groups[0].closed_at is None
    ordinary = conversations(snapshot, AT + timedelta(minutes=9), 300,
                             respect_open_meeting=False)
    assert ordinary[0].closed_at == stamp(AT + timedelta(minutes=6))


def test_retired_preferences_are_absent_from_configuration():
    keys = {field["key"] for field in descriptors()}
    assert not {key for key in keys if key.startswith(("meeting_", "marker_"))}
    assert not {"capture_paused", "recap_quiet_minutes", "recap_hour",
                "recap_interval_minutes"} & keys


@pytest.mark.parametrize("legacy_minutes", [1, 3, 15, 30])
def test_legacy_quiet_preferences_cannot_change_fixed_ten_minute_rule(legacy_minutes):
    snapshot = {"events": [event(hint_policy=json.dumps({
        "meeting_quiet_minutes": legacy_minutes,
    }))],
                "rows": [row(AT, AT + timedelta(minutes=75))], "gaps": []}
    assert meeting_timeline(snapshot, AT + timedelta(minutes=70))["active_meeting"]
    closed = meeting_timeline(snapshot, AT + timedelta(minutes=71))
    assert closed["meetings"][0]["ended_at"] == stamp(AT + timedelta(minutes=70))
    assert Settings(meeting_quiet_minutes=legacy_minutes, _env_file=None).hint_policy == (
        Settings(_env_file=None).hint_policy
    )
    settings = Settings(meeting_quiet_minutes=legacy_minutes, _env_file=None)
    assert settings.meeting_quiet_minutes == 10


def test_api_projects_quiet_and_preserves_offline_intent_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT + timedelta(minutes=75))
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        start = {"event_id": str(uuid4()), "device_id": "web", "occurred_at": stamp(AT),
                 "action": "meeting_toggle", "meeting_intent": "start",
                 "meeting_capture_device_id": "omi"}
        response = client.post("/v1/events?receipt_only=true", json=start)
        assert response.status_code == 200
        assert response.json()["meeting_intent"] == "start"
        assert response.json()["meeting_capture_device_id"] == "omi"
        store = client.app.state.memory.store
        audio = io.BytesIO()
        with wave.open(audio, "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
            stream.writeframes(b"\0" * 32000)
        for minute in range(60, 70, 2):
            key = str(uuid4())
            store.ingest(key, "omi", AT + timedelta(minutes=minute), audio.getvalue(), 120)
            store.complete(key, "", details={"voice_activity": {
                "version": VERSION, "status": "observed", "speech": [],
                "non_speech": [[0, 120]],
            }})
        state = client.get("/v1/timeline").json()
        assert state["active_meeting"] is None
        assert state["meetings"][0]["end_reason"] == "observed_non_speech"
        assert client.patch("/v1/settings", json={"meeting_quiet_minutes": 15}).status_code == 200
        assert client.get("/v1/settings").json()["meeting_quiet_minutes"] == 10
        # Old clients' preference edits cannot change the rule or reopen a meeting.
        assert client.get("/v1/timeline").json()["active_meeting"] is None
        newer = start | {"event_id": str(uuid4()),
                         "occurred_at": stamp(AT + timedelta(minutes=71))}
        assert client.post("/v1/events", json=newer).json()["active_meeting"][
            "meeting_quiet_minutes"
        ] == 10
        new_end = newer | {"event_id": str(uuid4()), "meeting_intent": "end",
                           "meeting_id": newer["event_id"],
                           "occurred_at": stamp(AT + timedelta(minutes=73))}
        new_end.pop("meeting_capture_device_id")
        assert client.post("/v1/events", json=new_end).status_code == 200
        assert client.patch("/v1/settings", json={"meeting_quiet_minutes": 10}).status_code == 200
        assert client.patch("/v1/settings", json={"meeting_quiet_minutes": 0}).status_code == 422
        end = start | {"event_id": str(uuid4()), "occurred_at": stamp(AT + timedelta(minutes=72)),
                       "meeting_intent": "end", "meeting_id": start["event_id"]}
        end.pop("meeting_capture_device_id")
        for _ in range(2):
            response = client.post("/v1/events", json=end)
            assert response.status_code == 200
            assert response.json()["active_meeting"] is None
            assert response.json()["meeting_id"] == start["event_id"]
        assert client.post("/v1/events", json=end | {"meeting_id": str(uuid4())}).status_code == 409
        with pytest.raises(ValueError):
            ButtonEvent.model_validate(start | {"meeting_intent": "end"})
