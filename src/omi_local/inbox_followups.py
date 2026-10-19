"""Ephemeral calendar/reminder proposals grounded in current inbox evidence."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .decision_calendar import calendar_file
from .decision_followups import (
    TASK as FOLLOWUP_TASK,
)
from .decision_followups import (
    CalendarExportRequest,
    FollowUpOutput,
    FollowUpRequest,
    evidence_excerpts,
    validate_suggestions,
)
from .decisions import Anchor
from .domain import EvidenceChanged
from .inbox_domain import ACTIONABLE_KINDS
from .inbox_jobs import InboxJobs
from .inbox_store import decode
from .inference import InferenceError, for_task

TASK = FOLLOWUP_TASK.replace(
    "following the supplied decision.", "following the supplied inbox action point."
).replace(
    "supported by the selected choice/context", "supported by the selected action point"
) + """
The item is an inbox commitment, deadline, reminder, event or open question, not a confirmed
decision. An event is an agreed/booked activity; a reminder is an explicit request to remember
an action. An open question may warrant a reminder to follow up; it does not establish an
agreed event or answer.
The effective item incorporates separately supplied user_overrides and an accepted deadline.
Respect explicit user edits to title, owner and deadline, including a deadline cleared to null;
these are owner instructions about this action, not original speech or proof of a commitment.
Never turn unrelated edited text into a new commitment. Explain user-edited details as such in
notes/reason. Do not restore or reinterpret a cleared deadline. For an accepted/edited deadline,
use exactly that date/instant; never replace it with a new interpretation of the original phrase.
Source evidence must still establish the underlying action. User edits require no fabricated quote.
Only propose actions for this selected item; nearby speech supplies context, not extra action items.
An action's deadline is not necessarily an event's start: a deadline to book a meeting does not
establish when that meeting takes place. Keep event scheduling separate from the action deadline.
"""


def current_item(archive, key: str, expected_version: int, db) -> tuple[dict, list[Anchor]]:
    row = db.execute("SELECT * FROM inbox_items WHERE id=?", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    if row["version"] != expected_version:
        raise EvidenceChanged("Action point changed; refresh before creating follow-ups")
    if row["status"] != "pending" or row["health"] != "current" or row["duplicate_of"]:
        raise EvidenceChanged("Follow-ups require a current, pending action point")
    item = decode(row)
    if item["kind"] not in ACTIONABLE_KINDS:
        raise EvidenceChanged("Follow-ups require an actionable item")
    anchors: dict[tuple, Anchor] = {}
    jobs = InboxJobs(archive)
    for occurrence in db.execute(
        "SELECT j.*,o.evidence FROM inbox_occurrences o JOIN inbox_jobs j ON j.id=o.job_id "
        "WHERE o.item_id=? AND j.state='done'",
        (key,),
    ).fetchall():
        # Context changes can invalidate the interpretation even when its cited words match.
        if jobs.sources(db, dict(occurrence)) is None:
            raise EvidenceChanged("Action point context changed; rescan its recordings")
        for span in json.loads(occurrence["evidence"]):
            source = db.execute(
                "SELECT a.*,s.revision FROM audio a JOIN inbox_sources s ON s.source_id=a.id "
                "WHERE a.id=?",
                (span["source_id"],),
            ).fetchone()
            if (
                not source
                or source["revision"] != span["revision"]
                or source["text"] is None
                or not 0 <= span["start"] < span["end"] <= len(source["text"])
                or source["text"][span["start"] : span["end"]] != span["quote"]
            ):
                raise EvidenceChanged("Action point evidence changed; rescan its recordings")
            anchor = Anchor(
                source_id=span["source_id"],
                source_version=source["source_version"],
                start=span["start"],
                end=span["end"],
                quote=span["quote"],
            )
            anchors[(span["source_id"], span["start"], span["end"])] = anchor
    if not anchors:
        raise EvidenceChanged("Follow-ups require current original evidence")
    return item, list(anchors.values())


def respect_reviewed_deadline(output: FollowUpOutput, item: dict) -> None:
    """Owner-reviewed reminder deadlines cannot be replaced by another model interpretation."""
    if "deadline" not in item["overrides"] and item["accepted_deadline"] is None:
        return
    deadline, due_at, all_day = item["deadline"] or {}, None, False
    if deadline.get("precision") == "date":
        due_at = datetime.combine(
            date.fromisoformat(deadline["day"]), time.min, ZoneInfo(deadline["timezone"])
        )
        all_day = True
    elif deadline.get("precision") == "datetime":
        due_at = datetime.fromisoformat(deadline["instant"])
    for suggestion in output.suggestions:
        if suggestion.kind == "reminder":
            suggestion.due_at, suggestion.all_day = due_at, all_day
            suggestion.start_at = suggestion.end_at = None


async def suggest_followups(memory, key: str, body: FollowUpRequest) -> dict:
    timezone = memory.settings.timezone
    with memory.store.connect() as db:
        db.execute("BEGIN")
        item, anchors = current_item(memory.store, key, body.expected_version, db)
        sources = evidence_excerpts(db, anchors, timezone)
    payload = {
        "timezone": timezone,
        "item": {field: item[field] for field in ("kind", "title", "owner", "deadline")},
        "proposal": item["proposal"],
        "user_overrides": {
            field: value for field, value in item["overrides"].items() if field != "user_note"
        },
        "accepted_deadline": item["accepted_deadline"],
        "evidence": [anchor.model_dump(mode="json") for anchor in anchors],
        "sources": sources,
    }
    raw = await for_task(memory.inference, "extraction").generate_json(
        TASK, json.dumps(payload, ensure_ascii=False)
    )
    try:
        output = FollowUpOutput.model_validate_json(raw)
    except (ValueError, ValidationError) as exc:
        raise InferenceError("Invalid structured action-point follow-ups") from exc
    with memory.store.connect() as db:
        db.execute("BEGIN")
        try:
            _, current_anchors = current_item(memory.store, key, body.expected_version, db)
        except KeyError as exc:
            raise EvidenceChanged("Action point was removed while suggesting follow-ups") from exc
        if {anchor.model_dump_json() for anchor in current_anchors} != {
            anchor.model_dump_json() for anchor in anchors
        }:
            raise EvidenceChanged("Original evidence changed while suggesting follow-ups")
        if memory.settings.timezone != timezone:
            raise EvidenceChanged("Timezone changed while suggesting follow-ups; retry")
        validate_suggestions(output, sources)
        respect_reviewed_deadline(output, item)
    return {
        "item_id": key,
        "item_version": item["version"],
        "timezone": timezone,
        "suggestions": [
            suggestion.model_dump(mode="json", exclude={"evidence"})
            for suggestion in output.suggestions
        ],
    }


def export_followup(memory, key: str, body: CalendarExportRequest) -> dict:
    with memory.store.connect() as db:
        db.execute("BEGIN")
        item, _ = current_item(memory.store, key, body.expected_version, db)
        return calendar_file(
            key, item["version"], body, memory.settings.timezone, context="action point"
        )
