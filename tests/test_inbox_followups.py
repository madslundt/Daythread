"""Recap actions share inbox lifecycle state and preserve reviewed calendar boundaries."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_decisions import client as client
from test_inbox import AT, TEXT, listing, mutate, proposal, run, source, span

from omi_local.inbox_jobs import InboxJobs
from omi_local.inbox_store import InboxStore


def action(client, text=TEXT, at=AT, candidate=None):
    archive = client.app.state.memory.store
    key = source(archive, text, at)
    run(archive, [candidate(key, text) if candidate else proposal(key, text)])
    item = listing(archive)["items"][0]
    return key, item


class Suggestions:
    def __init__(self, changes=None, during=None, empty=False):
        self.changes, self.during, self.empty = changes or {}, during, empty
        self.calls = 0

    async def generate_json(self, task, evidence):
        self.calls += 1
        self.task, self.payload = task, json.loads(evidence)
        if self.during:
            self.during()
        item = {
            "kind": "reminder",
            "title": "Send the résumé",
            "evidence": self.payload["evidence"],
            **self.changes,
        }
        return json.dumps({"suggestions": [] if self.empty else [item]})


def suggest(client, item, **body):
    return client.post(
        f"/v1/inbox/{item['id']}/suggestions",
        json={"expected_version": item["version"], **body},
    )


def export(client, item, **suggestion):
    return client.post(
        f"/v1/inbox/{item['id']}/calendar-export",
        json={
            "expected_version": item["version"],
            "suggestion": {"kind": "reminder", "title": "Send the résumé", **suggestion},
        },
    )


@pytest.mark.parametrize(
    "captured,day",
    [
        ("2026-10-15T21:59:59+00:00", "2026-10-15"),
        ("2026-10-15T22:00:00+00:00", "2026-10-16"),
        ("2026-10-24T22:00:00+00:00", "2026-10-25"),
        ("2026-10-25T22:59:59+00:00", "2026-10-25"),
        ("2026-10-25T23:00:00+00:00", "2026-10-26"),
        ("2026-03-28T23:00:00+00:00", "2026-03-29"),
        ("2026-03-29T21:59:59+00:00", "2026-03-29"),
        ("2026-03-29T22:00:00+00:00", "2026-03-30"),
    ],
)
def test_day_filter_uses_capture_day_in_configured_timezone(client, captured, day):
    key, item = action(client, at=datetime.fromisoformat(captured))
    response = client.get("/v1/inbox", params={"day": day})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert [i["id"] for i in response.json()["items"]] == [item["id"]]
    assert response.json()["items"][0]["source_ids"] == [key]
    assert response.json()["coverage"]["jobs"] == {"done": 1}
    assert len(response.json()["recent_jobs"]) == 1
    other_day = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
    other = client.get("/v1/inbox", params={"day": other_day}).json()
    assert other["total"] == 0
    assert other["coverage"] == {"jobs": {}, "unscanned_sources": 0}
    assert other["recent_jobs"] == []
    # Extraction/update receipt dates and due dates do not establish the capture day.
    assert client.get("/v1/inbox", params={"day": "2020-01-01"}).json()["items"] == []
    client.app.state.memory.settings.timezone = "UTC"
    utc_day = datetime.fromisoformat(captured).date().isoformat()
    assert client.get("/v1/inbox", params={"day": utc_day}).json()["total"] == 1


def test_selected_day_coverage_and_recent_jobs_exclude_unrelated_days(client):
    archive = client.app.state.memory.store
    current = source(archive, "Current day", datetime(2026, 10, 15, 23, 30, tzinfo=UTC))
    earlier = source(archive, "Earlier day", datetime(2026, 10, 14, 23, 30, tzinfo=UTC))
    jobs = InboxJobs(archive)
    jobs.discover("Europe/Copenhagen")
    with archive.connect() as db:
        current_job = db.execute(
            "SELECT job_id FROM inbox_dependencies WHERE source_id=?", (current,)
        ).fetchone()[0]
        earlier_job = db.execute(
            "SELECT j.* FROM inbox_jobs j JOIN inbox_dependencies d ON d.job_id=j.id "
            "WHERE d.source_id=?",
            (earlier,),
        ).fetchone()
        db.execute("UPDATE inbox_jobs SET state='failed' WHERE id=?", (earlier_job["id"],))
        # Twenty newer jobs on another day must not hide this day's queued job.
        for _ in range(20):
            key = str(uuid4())
            db.execute(
                "INSERT INTO inbox_jobs(id,fingerprint,inputs,timezone,state,created_at) "
                "VALUES(?,?,?,?,'failed','9999-01-01T00:00:00+00:00')",
                (key, key, earlier_job["inputs"], "Europe/Copenhagen"),
            )
            db.execute(
                "INSERT INTO inbox_dependencies "
                "SELECT ?,source_id,revision FROM inbox_dependencies WHERE job_id=?",
                (key, earlier_job["id"]),
            )
    source(archive, "Current unscanned", datetime(2026, 10, 16, 9, tzinfo=UTC))
    source(archive, "Another current unscanned", datetime(2026, 10, 16, 12, tzinfo=UTC))
    source(archive, "Earlier unscanned", datetime(2026, 10, 15, 9, tzinfo=UTC))

    selected = client.get("/v1/inbox", params={"day": "2026-10-16"}).json()
    assert selected["coverage"] == {"jobs": {"queued": 1}, "unscanned_sources": 2}
    assert [job["id"] for job in selected["recent_jobs"]] == [current_job]
    assert selected["recent_jobs"][0]["recording_start"].startswith("2026-10-15T23:30:")
    earlier_day = client.get("/v1/inbox", params={"day": "2026-10-15"}).json()
    assert earlier_day["coverage"] == {"jobs": {"failed": 21}, "unscanned_sources": 1}
    assert len(earlier_day["recent_jobs"]) == 20
    assert current_job not in {job["id"] for job in earlier_day["recent_jobs"]}
    empty = client.get("/v1/inbox", params={"day": "2026-10-17"}).json()
    assert empty["coverage"] == {"jobs": {}, "unscanned_sources": 0}
    assert empty["recent_jobs"] == []
    searched = client.get("/v1/inbox", params={"day": "2026-10-16", "q": "missing"}).json()
    assert searched["coverage"] == selected["coverage"]
    assert searched["recent_jobs"] == selected["recent_jobs"]
    global_page = client.get("/v1/inbox").json()
    assert global_page["coverage"] == {
        "jobs": {"queued": 1, "failed": 21}, "unscanned_sources": 3
    }
    assert len(global_page["recent_jobs"]) == 20
    assert current_job not in {job["id"] for job in global_page["recent_jobs"]}
    client.app.state.memory.settings.timezone = "UTC"
    utc_day = client.get("/v1/inbox", params={"day": "2026-10-15"}).json()
    assert utc_day["coverage"]["jobs"] == {"queued": 1}


def test_day_job_coverage_includes_selected_speech_used_as_neighboring_context(client):
    archive = client.app.state.memory.store
    source(archive, "Before midnight", datetime(2026, 10, 15, 21, 59, 40, tzinfo=UTC))
    source(archive, "After midnight", datetime(2026, 10, 15, 22, 0, 20, tzinfo=UTC))
    InboxJobs(archive).discover("Europe/Copenhagen")
    # Both extraction windows consume speech from each day. Report the selected day's start,
    # rather than labeling the new day's jobs with their earlier context source timestamp.
    newer = client.get("/v1/inbox", params={"day": "2026-10-16"}).json()
    assert newer["coverage"] == {"jobs": {"queued": 2}, "unscanned_sources": 0}
    assert len(newer["recent_jobs"]) == 2
    assert all(
        job["recording_start"].startswith("2026-10-15T22:00:20")
        for job in newer["recent_jobs"]
    )
    earlier = client.get("/v1/inbox", params={"day": "2026-10-15"}).json()
    assert earlier["coverage"] == {"jobs": {"queued": 2}, "unscanned_sources": 0}
    assert all(
        job["recording_start"].startswith("2026-10-15T21:59:40")
        for job in earlier["recent_jobs"]
    )


def test_topic_queries_use_effective_fields_and_cited_speech_before_pagination(client):
    archive = client.app.state.memory.store
    text = "Discuss the résumé. " + "Unrelated discussion. " * 20 + "Check the budget."
    key = source(archive, text)
    first = proposal(key, text, "Send résumé")
    first["evidence"] = [span(key, text, "Discuss the résumé.")]
    second = proposal(key, text, "Check budget")
    second["evidence"] = [span(key, text, "Check the budget.")]
    run(archive, [first, second])
    first_item = next(i for i in listing(archive)["items"] if i["title"] == "Send résumé")
    edited = mutate(archive, first_item, changes={"title": "My task", "owner": "Ada"})
    page = client.get("/v1/inbox", params={"q": "RÉSUMÉ Ada", "limit": 1}).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == edited["id"]
    assert item["source_ids"] == [key]
    assert item["evidence_spans"] == [
        {"source_id": key, "start": 0, "end": len("Discuss the résumé."), "current": True}
    ]
    # The other action shares the source, but has its own topic offsets and quoted speech.
    budget = client.get("/v1/inbox", params={"q": "budget"}).json()["items"][0]
    assert budget["source_ids"] == [key]
    assert budget["evidence_spans"][0]["start"] == text.index("Check the budget.")
    assert client.get("/v1/inbox", params={"q": "résumé", "offset": 1}).json()["items"] == []
    assert client.get("/v1/inbox", params={"q": "Unrelated"}).json()["items"] == []
    assert client.get("/v1/inbox", params={"q": "x" * 201}).status_code == 422
    assert client.get("/v1/inbox", params={"day": "invalid"}).status_code == 422


def test_all_evidence_source_ids_and_stale_span_state_are_exposed(client):
    archive = client.app.state.memory.store
    first = source(archive)
    other_text = "The résumé should include the project details."
    second = source(archive, other_text, AT + timedelta(seconds=2))
    candidate = proposal(first)
    candidate["evidence"].append(span(second, other_text))
    run(archive, [candidate])
    item = listing(archive)["items"][0]
    assert item["source_ids"] == sorted([first, second])
    assert {s["source_id"] for s in item["evidence_spans"]} == {first, second}
    archive.correct_transcript(second, other_text + " Correction.")
    stale = client.get("/v1/inbox").json()["items"][0]
    assert stale["health"] == "needs_review"
    assert all(not s["current"] for s in stale["evidence_spans"])


def test_suggestions_use_original_evidence_capture_day_and_owner_edits(client):
    key, item = action(client, at=datetime(2026, 10, 15, 23, 30, tzinfo=UTC))
    archive = client.app.state.memory.store
    item = mutate(
        archive,
        item,
        changes={
            "title": "Send revised résumé",
            "owner": "Ada",
            "deadline": {"precision": "date", "day": "2026-10-20", "timezone": "Europe/Copenhagen"},
        },
    )
    ai = Suggestions()
    client.app.state.memory.inference = ai
    response = suggest(client, item)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["item_id"] == item["id"] and value["item_version"] == item["version"]
    assert value["timezone"] == "Europe/Copenhagen"
    assert value["suggestions"][0]["due_at"] == "2026-10-20T00:00:00+02:00"
    assert value["suggestions"][0]["all_day"] is True
    assert "evidence" not in value["suggestions"][0]
    assert ai.payload["sources"][0]["id"] == key
    assert ai.payload["sources"][0]["text"] == TEXT
    assert ai.payload["sources"][0]["captured_local_date"] == "2026-10-16"
    assert ai.payload["item"]["title"] == "Send revised résumé"
    assert ai.payload["user_overrides"]["owner"] == "Ada"
    assert ai.payload["item"]["deadline"]["day"] == "2026-10-20"
    assert ai.payload["proposal"]["title"] == "Send the résumé tomorrow"
    assert "never replace" in ai.task.lower() and "do not guess" in ai.task.lower()
    assert response.headers["cache-control"] == "no-store"
    # Suggestion generation leaves accepted state, versions, and events intact.
    assert InboxStore(archive).item(item["id"]) == item


@pytest.mark.parametrize(
    "deadline,expected,all_day",
    [
        (None, None, False),
        ({"precision": "unknown"}, None, False),
        (
            {"precision": "date", "day": "2026-10-26", "timezone": "Europe/Copenhagen"},
            "2026-10-26T00:00:00+01:00",
            True,
        ),
        (
            {
                "precision": "datetime",
                "instant": "2026-10-26T10:00:00+01:00",
                "timezone": "Europe/Copenhagen",
            },
            "2026-10-26T10:00:00+01:00",
            False,
        ),
    ],
)
def test_reviewed_deadline_overrides_generated_reminder_dates(client, deadline, expected, all_day):
    _, item = action(client)
    item = mutate(client.app.state.memory.store, item, changes={"deadline": deadline})
    client.app.state.memory.inference = Suggestions({"due_at": "2030-01-01T09:00:00Z"})
    result = suggest(client, item).json()["suggestions"][0]
    assert result["due_at"] == expected
    assert result["all_day"] is all_day


@pytest.mark.parametrize(
    "kind,text",
    [
        ("commitment", "Book a meeting by tomorrow, time to be arranged."),
        ("event", "We agreed to meet on 2026-10-20; the time is still to be arranged."),
    ],
)
def test_action_deadline_and_event_date_do_not_invent_meeting_times(client, kind, text):
    _, item = action(
        client, text, candidate=lambda key, text: proposal(key, text, "Meeting review", kind)
    )
    item = mutate(
        client.app.state.memory.store,
        item,
        changes={
            "deadline": {"precision": "date", "day": "2026-10-20", "timezone": "Europe/Copenhagen"}
        },
    )
    client.app.state.memory.inference = Suggestions({"kind": "event"})
    result = suggest(client, item).json()["suggestions"][0]
    assert result["start_at"] is None and result["end_at"] is None


def test_unknown_event_times_and_empty_suggestions_remain_unknown(client):
    _, item = action(client, "Let's arrange a review sometime.")
    client.app.state.memory.inference = Suggestions({"kind": "event"})
    result = suggest(client, item).json()["suggestions"][0]
    assert result["start_at"] is None and result["end_at"] is None
    assert export(client, item, **result).status_code == 422
    client.app.state.memory.inference = Suggestions(empty=True)
    assert suggest(client, item).json()["suggestions"] == []


@pytest.mark.parametrize(
    "change", ["correct", "same_text", "delete", "edit", "complete", "dismiss", "timezone"]
)
def test_changes_during_generation_discard_followups(client, change):
    key, item = action(client)
    archive = client.app.state.memory.store

    def during():
        if change == "correct":
            archive.correct_transcript(key, "No promise was made.")
        elif change == "same_text":
            archive.correct_transcript(key, TEXT)
        elif change == "delete":
            archive.delete_audio(key)
        elif change == "timezone":
            client.app.state.memory.settings.timezone = "America/New_York"
        elif change == "edit":
            mutate(archive, item, changes={"title": "Updated"})
        else:
            mutate(archive, item, status="completed" if change == "complete" else "dismissed")

    client.app.state.memory.inference = Suggestions(during=during)
    assert suggest(client, item).status_code == 409


@pytest.mark.parametrize("change", ["correct", "complete", "dismiss", "withdraw", "version"])
def test_invalid_items_block_suggestions_and_exports_before_inference(client, change):
    key, item = action(client)
    archive = client.app.state.memory.store
    if change == "correct":
        archive.correct_transcript(key, "No promise was made.")
        item = InboxStore(archive).item(item["id"])
    elif change in {"complete", "dismiss"}:
        item = mutate(archive, item, status="completed" if change == "complete" else "dismissed")
    elif change == "withdraw":
        source(archive, "The action is no longer needed.", AT + timedelta(seconds=2))
        run(archive, [])
        item = InboxStore(archive).item(item["id"])
    else:
        mutate(archive, item, changes={"owner": "Ada"})
    ai = Suggestions()
    client.app.state.memory.inference = ai
    assert suggest(client, item).status_code == 409
    assert export(client, item).status_code == 409
    assert ai.calls == 0


@pytest.mark.parametrize("bad", ["quote", "source", "offset", "version", "naive", "location"])
def test_invalid_generated_evidence_fails_closed(client, bad):
    _, item = action(client)

    class Invalid(Suggestions):
        async def generate_json(self, task, evidence):
            value = json.loads(await super().generate_json(task, evidence))
            suggestion = value["suggestions"][0]
            anchor = suggestion["evidence"][0]
            if bad == "quote":
                anchor["quote"] = "Invented"
            elif bad == "source":
                anchor["source_id"] = str(uuid4())
            elif bad == "offset":
                anchor["start"] += 1
            elif bad == "version":
                anchor["source_version"] = "stale"
            elif bad == "naive":
                suggestion["due_at"] = "2026-10-17T09:00:00"
            else:
                suggestion.update(location="Invented place", location_trigger="arrive")
            return json.dumps(value)

    client.app.state.memory.inference = Invalid()
    assert suggest(client, item).status_code == 502


def test_followup_excerpts_are_bounded_and_keep_full_transcript_offsets(client):
    text = "Unrelated " * 2000 + TEXT

    def candidate(key, text):
        result = proposal(key, text)
        result["evidence"] = [span(key, text, TEXT)]
        result["selection"]["evidence"] = result["evidence"]
        return result

    _, item = action(client, text, candidate=candidate)
    ai = Suggestions()
    client.app.state.memory.inference = ai
    response = suggest(client, item)
    assert response.status_code == 200, response.text
    excerpt = ai.payload["sources"][0]
    assert excerpt["text_offset"] > 0 and len(excerpt["text"]) <= 500 + len(TEXT)


def test_calendar_export_is_reviewed_portable_and_never_completes_action(client):
    _, item = action(client)
    archive = client.app.state.memory.store
    before = InboxStore(archive).item(item["id"])
    undated = export(client, item)
    assert undated.status_code == 200, undated.text
    assert undated.headers["cache-control"] == "no-store"
    content = undated.json()["content"]
    assert "BEGIN:VTODO\r\n" in content and "STATUS:NEEDS-ACTION\r\n" in content
    assert "DUE" not in content
    dated = export(client, item, due_at="2026-10-26T10:00:00+01:00").json()["content"]
    assert "DUE:20261026T090000Z\r\n" in dated
    args = {
        "kind": "event",
        "title": "Proposal review",
        "start_at": "2026-10-26T10:00:00+01:00",
        "end_at": "2026-10-26T11:00:00+01:00",
    }
    first = export(client, item, **args)
    assert first.status_code == 200
    value = first.json()["content"]
    assert "DTSTART:20261026T090000Z\r\n" in value
    assert "DTEND:20261026T100000Z\r\n" in value
    second = export(client, item, **args).json()["content"]
    assert next(line for line in value.splitlines() if line.startswith("UID:")) == next(
        line for line in second.splitlines() if line.startswith("UID:")
    )
    assert "Omi action point: " + item["id"] in value.replace("\r\n ", "")
    assert InboxStore(archive).item(item["id"]) == before


def test_place_reminder_requires_native_review_and_no_date(client):
    _, item = action(client, "Pick up the bike at Nørrebro Cykler when nearby.")
    client.app.state.memory.inference = Suggestions(
        {"location": "Nørrebro Cykler", "location_trigger": "arrive"}
    )
    response = suggest(client, item)
    assert response.status_code == 200, response.text
    suggestion = response.json()["suggestions"][0]
    assert suggestion["due_at"] is None
    assert suggestion["location_trigger"] == "arrive"
    assert export(client, item, **suggestion).status_code == 422


def test_missing_items_and_invalid_versions(client):
    item = {"id": str(uuid4()), "version": 1}
    assert suggest(client, item).status_code == 404
    assert export(client, item).status_code == 404
    assert suggest(client, item, expected_version=0).status_code == 422
