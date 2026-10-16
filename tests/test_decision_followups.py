"""Decision follow-ups remain proposals, with current evidence and portable exports."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_decisions import client as client
from test_decisions import content, create, event, source


class Suggestions:
    def __init__(self, original, items=None, during=None):
        self.original, self.items, self.during = original, items, during
        self.payload = None
        self.calls = 0

    async def generate_json(self, task, evidence):
        self.calls += 1
        self.payload = json.loads(evidence)
        self.task = task
        if self.during:
            self.during()
        items = (
            self.items
            if self.items is not None
            else [
                {
                    "kind": "reminder",
                    "title": "Send the proposal",
                    "reason": "An agreed next step",
                    "evidence": content(self.original)["choice"]["evidence"],
                }
            ]
        )
        return json.dumps({"suggestions": items})


def suggest(client, decision, **body):
    return client.post(
        f"/v1/decisions/{decision['id']}/suggestions",
        json={"expected_version": decision["version"], **body},
    )


def export(client, decision, *, use_calendar_defaults=False, **suggestion):
    return client.post(
        f"/v1/decisions/{decision['id']}/calendar-export",
        json={
            "expected_version": decision["version"],
            "use_calendar_defaults": use_calendar_defaults,
            "suggestion": {
                "kind": "reminder",
                "title": "Send the proposal",
                **suggestion,
            },
        },
    )


def test_suggestions_use_original_speech_capture_date_and_preserve_unknown_times(client):
    s = source(
        client,
        "We decided to send the proposal tomorrow.",
        datetime(2026, 10, 15, 23, 30, tzinfo=UTC),
    )
    d = create(client, s, "Send the proposal")
    c = content(s, "Send the proposal")
    c["owner_notes"] = "Invent a holiday in Paris."
    c["alternatives"] = [{"text": "A rejected alternative", "evidence": c["choice"]["evidence"]}]
    d = event(client, d, "edit", content=c).json()
    ai = Suggestions(s)
    client.app.state.memory.inference = ai
    response = suggest(client, d)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["decision_id"] == d["id"]
    assert result["decision_version"] == d["version"]
    assert result["timezone"] == "Europe/Copenhagen"
    assert result["suggestions"][0]["due_at"] is None
    assert "evidence" not in result["suggestions"][0]
    assert ai.payload["sources"][0]["captured_local_date"] == "2026-10-16"
    assert ai.payload["sources"][0]["text"] == s["text"]
    assert "owner_notes" not in ai.payload and "alternatives" not in ai.payload
    assert "Paris" not in json.dumps(ai.payload)
    assert "do not guess" in ai.task.lower()
    assert response.headers["cache-control"] == "no-store"
    # Suggestions are ephemeral: no history events or records are created.
    assert client.get(f"/v1/decisions/{d['id']}").json()["version"] == d["version"]


def test_no_supported_followups_is_an_empty_result(client):
    s = source(client)
    d = create(client, s)
    client.app.state.memory.inference = Suggestions(s, [])
    assert suggest(client, d).json()["suggestions"] == []


def test_owner_can_reanchor_corrected_evidence_before_creating_followups(client):
    s = source(client)
    d = create(client, s)
    client.app.state.memory.store.correct_transcript(s["id"], "We decided to send the proposal.")
    current_source = client.get(f"/v1/transcripts/{s['id']}").json()
    stale = client.get(f"/v1/decisions/{d['id']}").json()
    edited = event(client, stale, "edit", content=content(current_source)).json()
    assert edited["evidence_condition"] == "current"
    client.app.state.memory.inference = Suggestions(current_source)
    assert suggest(client, edited).status_code == 200
    assert export(client, edited).status_code == 200


@pytest.mark.parametrize("change", ["correct", "delete", "edit", "dismiss"])
def test_changes_during_generation_discard_suggestions(client, change):
    s = source(client)
    d = create(client, s)
    store = client.app.state.memory.store

    def mutate():
        if change == "correct":
            store.correct_transcript(s["id"], s["text"])
        elif change == "delete":
            store.delete_audio(s["id"])
        else:
            from omi_local.decisions import DecisionEvent

            store.decision_event(
                d["id"],
                DecisionEvent(
                    command_id=uuid4(),
                    expected_version=d["version"],
                    type=change,
                    content=content(s) if change == "edit" else None,
                ),
            )

    client.app.state.memory.inference = Suggestions(s, during=mutate)
    assert suggest(client, d).status_code == 409


@pytest.mark.parametrize("change", ["dismiss", "conflict", "correct", "version", "supersede"])
def test_invalid_decisions_block_generation_and_export(client, change):
    s = source(client)
    d = create(client, s)
    if change in {"dismiss", "conflict"}:
        d = event(client, d, change).json()
    elif change == "correct":
        client.app.state.memory.store.correct_transcript(s["id"], s["text"])
        d = client.get(f"/v1/decisions/{d['id']}").json()
    elif change == "supersede":
        d = event(client, d, "confirm").json()
        newer = create(
            client, source(client, "We now choose B.", datetime(2026, 10, 16, tzinfo=UTC))
        )
        linked = event(
            client, newer, "confirm", predecessor_id=d["id"], predecessor_version=d["version"]
        )
        assert linked.status_code == 200, linked.text
        d = client.get(f"/v1/decisions/{d['id']}").json()
    else:
        event(client, d, "confirm")
    ai = Suggestions(s)
    client.app.state.memory.inference = ai
    assert suggest(client, d).status_code == 409
    assert export(client, d).status_code == 409
    assert ai.calls == 0


@pytest.mark.parametrize("bad", ["quote", "source", "offset", "naive", "missing_evidence"])
def test_invalid_generated_suggestions_fail_closed(client, bad):
    s = source(client)
    d = create(client, s)
    item = {"kind": "reminder", "title": "Review A", "evidence": content(s)["choice"]["evidence"]}
    if bad == "quote":
        item["evidence"][0]["quote"] = "Invented quote"
    elif bad == "source":
        item["evidence"][0]["source_id"] = str(uuid4())
    elif bad == "offset":
        item["evidence"][0]["start"] = 2
    elif bad == "naive":
        item["due_at"] = "2026-10-16T10:00:00"
    else:
        del item["evidence"]
    client.app.state.memory.inference = Suggestions(s, [item])
    assert suggest(client, d).status_code == 502


def test_excerpts_are_bounded_and_use_full_transcript_offsets(client):
    s = source(client, "unrelated " * 2000 + "We decided to send the proposal tomorrow.")
    c = content(s)
    c["choice"]["evidence"][0].update(quote=s["text"][-41:], start=len(s["text"]) - 41)
    d = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c}).json()
    ai = Suggestions(
        s, [{"kind": "reminder", "title": "Send proposal", "evidence": c["choice"]["evidence"]}]
    )
    client.app.state.memory.inference = ai
    response = suggest(client, d)
    assert response.status_code == 200, response.text
    excerpt = ai.payload["sources"][0]
    assert excerpt["text_offset"] > 0 and len(excerpt["text"]) <= 541


def test_export_event_uses_utc_times_stable_uid_and_no_writes(client):
    d = create(client, source(client))
    args = dict(
        kind="event",
        title="Proposal review",
        notes="Review the chosen approach",
        start_at="2026-10-26T10:00:00+01:00",
        end_at="2026-10-26T11:00:00+01:00",
    )
    first = export(client, d, **args)
    assert first.status_code == 200, first.text
    value = first.json()
    assert value["filename"].endswith(".ics")
    assert "BEGIN:VEVENT\r\n" in value["content"]
    assert "DTSTART:20261026T090000Z\r\n" in value["content"]
    assert "DTEND:20261026T100000Z\r\n" in value["content"]
    second = export(client, d, **args).json()["content"]
    assert next(line for line in value["content"].splitlines() if line.startswith("UID:")) == (
        next(line for line in second.splitlines() if line.startswith("UID:"))
    )
    assert "METHOD:" not in value["content"]
    other = export(
        client,
        d,
        **dict(args, start_at="2026-10-27T10:00:00+01:00", end_at="2026-10-27T11:00:00+01:00"),
    ).json()["content"]
    assert next(line for line in second.splitlines() if line.startswith("UID:")) != (
        next(line for line in other.splitlines() if line.startswith("UID:"))
    )
    assert client.get(f"/v1/decisions/{d['id']}").json()["version"] == d["version"]


def test_export_reminder_supports_undated_and_dated_tasks(client):
    d = create(client, source(client))
    undated = export(client, d).json()["content"]
    assert "BEGIN:VTODO\r\n" in undated and "STATUS:NEEDS-ACTION\r\n" in undated
    assert "DUE" not in undated
    dated = export(client, d, due_at="2026-10-16T10:00:00+02:00").json()["content"]
    assert "DUE:20261016T080000Z\r\n" in dated


def test_all_day_preserves_owner_selected_date_and_exclusive_end(client):
    d = create(client, source(client))
    result = export(
        client,
        d,
        kind="event",
        all_day=True,
        start_at="2026-10-16T00:00:00+14:00",
        end_at="2026-10-17T00:00:00+14:00",
    )
    assert result.status_code == 200
    assert "DTSTART;VALUE=DATE:20261016\r\n" in result.json()["content"]
    assert "DTEND;VALUE=DATE:20261017\r\n" in result.json()["content"]
    reminder = export(client, d, all_day=True, due_at="2026-10-16T00:00:00+14:00")
    assert "DUE;VALUE=DATE:20261016\r\n" in reminder.json()["content"]


def test_export_escapes_injection_and_folds_utf8_by_octets(client):
    d = create(client, source(client))
    title = "😀 æøå " * 30
    result = export(
        client, d, title=title, notes="Semi; comma, backslash\\\r\nBEGIN:VEVENT\rInjected:yes"
    )
    assert result.status_code == 200
    calendar = result.json()["content"]
    assert all(len(line.encode()) <= 75 for line in calendar.split("\r\n"))
    assert "\n" not in calendar.replace("\r\n", "")
    unfolded = calendar.replace("\r\n ", "")
    assert "SUMMARY:" + title.strip() in unfolded
    assert "Semi\\; comma\\, backslash\\\\\\nBEGIN:VEVENT\\nInjected:yes" in unfolded
    assert "\r\nInjected:yes" not in calendar


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "   "},
        {"title": "Invalid\0title"},
        {"notes": "Invalid\x01note"},
        {"due_at": "0001-01-01T00:00:00+14:00"},
        {"due_at": "2026-10-17T09:00:00"},
        {"kind": "event"},
        {"kind": "event", "start_at": "2026-10-17T09:00:00Z"},
        {"kind": "event", "start_at": "2026-10-17T09:00:00Z", "end_at": "2026-10-17T08:00:00Z"},
        {
            "kind": "event",
            "all_day": True,
            "start_at": "2026-10-17T09:00:00Z",
            "end_at": "2026-10-17T10:00:00Z",
        },
    ],
)
def test_calendar_export_requires_valid_reviewed_dates_and_title(client, changes):
    d = create(client, source(client))
    assert export(client, d, **changes).status_code == 422


def test_missing_decision_and_invalid_version(client):
    d = {"id": str(uuid4()), "version": 1}
    assert suggest(client, d).status_code == 404
    assert export(client, d).status_code == 404
    assert suggest(client, d, expected_version=0).status_code == 422


@pytest.mark.parametrize(
    "dates,start,end",
    [
        ({}, "20261017T130000Z", "20261017T140000Z"),
        ({"start_at": "2026-10-16T10:00:00+02:00"}, "20261016T080000Z", "20261016T090000Z"),
        ({"end_at": "2026-10-16T11:00:00+02:00"}, "20261016T080000Z", "20261016T090000Z"),
    ],
)
def test_calendar_defaults_fill_only_missing_event_times(client, monkeypatch, dates, start, end):
    from omi_local import decision_calendar

    monkeypatch.setattr(
        decision_calendar, "utc_now", lambda: datetime(2026, 10, 17, 12, 24, tzinfo=UTC)
    )
    d = create(client, source(client))
    response = export(client, d, kind="event", use_calendar_defaults=True, **dates)
    assert response.status_code == 200, response.text
    value = response.json()["content"].replace("\r\n ", "")
    assert f"DTSTART:{start}\r\n" in value
    assert f"DTEND:{end}\r\n" in value
    assert "Calendar defaults (not stated in the recording)" in value
    assert client.get(f"/v1/decisions/{d['id']}").json()["version"] == d["version"]


@pytest.mark.parametrize(
    "now,start,end",
    [
        (datetime(2026, 3, 29, 0, 30, tzinfo=UTC), "20260329T010000Z", "20260329T020000Z"),
        (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), "20261025T010000Z", "20261025T020000Z"),
    ],
)
def test_calendar_defaults_cross_dst_as_real_instants(client, monkeypatch, now, start, end):
    from omi_local import decision_calendar

    monkeypatch.setattr(decision_calendar, "utc_now", lambda: now)
    d = create(client, source(client))
    result = export(client, d, kind="event", use_calendar_defaults=True)
    assert result.status_code == 200, result.text
    assert f"DTSTART:{start}\r\n" in result.json()["content"]
    assert f"DTEND:{end}\r\n" in result.json()["content"]


@pytest.mark.parametrize(
    "dates",
    [
        {},
        {"start_at": "2026-10-25T00:00:00+14:00"},
        {"end_at": "2026-10-26T00:00:00+14:00"},
    ],
)
def test_all_day_calendar_defaults_use_local_dates_and_exclusive_end(client, monkeypatch, dates):
    from omi_local import decision_calendar

    monkeypatch.setattr(
        decision_calendar, "utc_now", lambda: datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
    )
    d = create(client, source(client))
    response = export(client, d, kind="event", all_day=True, use_calendar_defaults=True, **dates)
    assert response.status_code == 200, response.text
    value = response.json()["content"].replace("\r\n ", "")
    assert "DTSTART;VALUE=DATE:20261025\r\n" in value
    assert "DTEND;VALUE=DATE:20261026\r\n" in value
    assert "Calendar defaults (not stated in the recording)" in value


def test_default_dates_do_not_change_suggestions_or_uid(client, monkeypatch):
    from omi_local import decision_calendar
    from omi_local.decision_followups import FollowUp

    item = FollowUp(kind="event", title="Meet")
    monkeypatch.setattr(
        decision_calendar, "utc_now", lambda: datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
    )
    start, end, _ = decision_calendar.compose_dates(item, "Europe/Copenhagen")
    assert start < end and item.start_at is None and item.end_at is None
    d = create(client, source(client))
    first = export(client, d, kind="event", use_calendar_defaults=True).json()["content"]
    monkeypatch.setattr(
        decision_calendar, "utc_now", lambda: datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    )
    second = export(client, d, kind="event", use_calendar_defaults=True).json()["content"]
    assert next(line for line in first.splitlines() if line.startswith("UID:")) == (
        next(line for line in second.splitlines() if line.startswith("UID:"))
    )


def test_calendar_defaults_preserve_known_dates_and_leave_reminders_undated(client):
    d = create(client, source(client))
    dated = export(
        client,
        d,
        kind="event",
        use_calendar_defaults=True,
        start_at="2026-10-25T10:00:00+01:00",
        end_at="2026-10-25T11:00:00+01:00",
    )
    assert dated.status_code == 200
    assert "Calendar defaults" not in dated.json()["content"]
    reminder = export(client, d, use_calendar_defaults=True).json()["content"]
    assert "DUE" not in reminder and "Calendar defaults" not in reminder


@pytest.mark.parametrize(
    "dates",
    [
        {"start_at": "2026-10-17T09:00:00Z", "end_at": "2026-10-17T08:00:00Z"},
        {"start_at": "2026-10-17T09:00:00"},
        {"all_day": True, "start_at": "2026-10-17T09:00:00Z", "end_at": "2026-10-17T10:00:00Z"},
        {"all_day": True, "start_at": "9999-12-31T00:00:00Z"},
    ],
)
def test_calendar_defaults_do_not_repair_invalid_explicit_dates(client, dates):
    d = create(client, source(client))
    assert export(client, d, kind="event", use_calendar_defaults=True, **dates).status_code == 422
