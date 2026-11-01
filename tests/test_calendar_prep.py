"""Calendar context stays ephemeral and distinct from recorded evidence."""

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.calendar_prep import MAX_BYTES, parse_calendar
from omi_local.settings import Settings


def calendar(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "\r\n".join(events) + "\r\nEND:VCALENDAR"


def event(*properties):
    return "BEGIN:VEVENT\r\n" + "\r\n".join(properties) + "\r\nEND:VEVENT"


def test_timezone_folded_names_and_excluded_cancelled_declined():
    data = calendar(
        event("UID:meeting", "DTSTART;TZID=Europe/Copenhagen:20261103T100000",
              "DTEND;TZID=Europe/Copenhagen:20261103T110000", "SUMMARY:Project\\, garden",
              'ATTENDEE;CN="Alex: Hansen":mailto:alex@example.test',
              'ATTENDEE;CN="Long name\r\n continued":mailto:other@example.test',
              "ATTENDEE;CN=Sam;PARTSTAT=DECLINED:mailto:sam@example.test",
              "BEGIN:VALARM", "SUMMARY:Ignore nested alarm", "END:VALARM"),
        event("DTSTART:20261103T090000Z", "SUMMARY:Cancelled", "STATUS:CANCELLED"),
    )
    result = parse_calendar(data, "UTC")
    assert len(result["events"]) == 1
    row = result["events"][0]
    assert row["started_at"] == "2026-11-03T10:00:00+01:00"
    assert row["title"] == "Project, garden"
    assert row["attendee_candidates"] == ["Alex: Hansen", "Long namecontinued"]
    assert result["skipped"] == 1


def test_recurrences_are_disclosed_and_all_day_end_is_exclusive():
    result = parse_calendar(calendar(event(
        "UID:series", "DTSTART;VALUE=DATE:20261101", "DTEND;VALUE=DATE:20261102",
        "SUMMARY:Planning", "RRULE:FREQ=WEEKLY;COUNT=10",
    )), "Europe/Copenhagen")
    assert result["recurring"] == 1
    assert result["events"][0]["all_day"]
    assert result["events"][0]["recurrence_not_expanded"]
    assert result["events"][0]["ended_at"] == "2026-11-02T00:00:00+01:00"


@pytest.mark.parametrize("stamp", ["20260329T023000", "20261025T023000"])
def test_ambiguous_or_nonexistent_floating_instants_are_not_guessed(stamp):
    result = parse_calendar(calendar(event(
        f"DTSTART;TZID=Europe/Copenhagen:{stamp}", "SUMMARY:Ambiguous meeting",
    )), "UTC")
    assert result["events"] == [] and result["skipped"] == 1


def test_file_and_instance_limits_and_no_recurrence_explosion():
    with pytest.raises(ValueError, match="2 MiB"):
        parse_calendar("x" * (MAX_BYTES + 1), "UTC")
    rows = [event(f"UID:{i}", "DTSTART:20261101T090000Z", "SUMMARY:Meeting")
            for i in range(101)]
    result = parse_calendar(calendar(*rows), "UTC")
    assert len(result["events"]) == 100
    assert result["total"] == 101 and result["omitted"] == 1
    with pytest.raises(ValueError, match="1,000"):
        parse_calendar(calendar(*(rows * 10)), "UTC")


def test_route_imports_context_without_writing_archive_or_chat(tmp_path):
    with TestClient(create_app(Settings(
        data_dir=tmp_path, worker_enabled=False, _env_file=None,
    ))) as client:
        before = client.get("/v1/labels").json()["revision"]
        response = client.post("/v1/conversation-prep/calendar/import", json={
            "content": calendar(event("DTSTART:20261101T090000Z", "SUMMARY:Garden meeting")),
        })
        assert response.status_code == 200, response.text
        assert response.json()["events"][0]["title"] == "Garden meeting"
        assert response.headers["Cache-Control"] == "no-store"
        assert client.get("/v1/labels").json()["revision"] == before
        assert client.get("/v1/transcripts").json()["total"] == 0
        assert client.get("/v1/chat/sessions").json()["items"] == []
        assert client.post("/v1/conversation-prep/calendar/import", json={
            "content": "not an ICS file",
        }).status_code == 422
