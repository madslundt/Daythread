"""Explicit finished-file meetings, independent of live capture gesture state."""

from datetime import datetime

from .domain import stamp


def imported_meetings(snapshot: dict, now: datetime) -> list[dict]:
    rows = {row["id"]: row for row in snapshot["rows"]}
    result = []
    for plan in snapshot.get("meeting_uploads", []):
        parts = plan["parts"]
        # A partial upload never claims the complete recording has arrived. Audio
        # retention can remove playback without removing a transcript-backed meeting.
        if (not plan.get("treat_as_meeting") or not parts or plan["ended_at"] > stamp(now)
                or any(part["id"] not in rows or rows[part["id"]]["state"] == "expired"
                       or rows[part["id"]]["digest"] != part["digest"] for part in parts)):
            continue
        result.append({
            "id": plan["recording_id"], "device_id": "manual-web",
            "meeting_capture_device_id": None,
            "started_at": plan["started_at"], "ended_at": plan["ended_at"],
            "context_start": plan["started_at"], "context_end": plan["ended_at"],
            "qualifies_at": plan["started_at"], "status": "closed",
            "auto_stop_at": None, "meeting_quiet_minutes": 10,
            "end_reason": "imported_recording", "context_padding_seconds": 0,
            "timing": "recorded", "source_ids": [part["id"] for part in parts],
        })
    return result


def contains_source(meeting: dict, source: dict) -> bool:
    if "source_ids" in meeting:
        return source["id"] in meeting["source_ids"]
    return (source["started_at"] < meeting["context_end"]
            and source["ended_at"] > meeting["context_start"])
