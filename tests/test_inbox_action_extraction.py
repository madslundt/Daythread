"""Versioned extraction keeps actions, reflections and remembered facts grounded and distinct."""

import asyncio
import json
from datetime import timedelta

import pytest
from test_inbox import AT, listing, mutate, proposal, source, span
from test_inbox import archive as archive

from omi_local import inbox_jobs
from omi_local.inbox_jobs import InboxJobs


class Extraction:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0

    async def generate(self, task, evidence):
        self.calls += 1
        self.task, self.evidence = task, json.loads(evidence)
        return json.dumps({"candidates": self.candidates})


@pytest.mark.parametrize(
    "text,title,phrase,kind",
    [
        ("We agreed to meet Ada tomorrow.", "Meet Ada", "tomorrow", "event"),
        ("My dentist appointment is on 2026-10-20.", "Dentist appointment", "2026-10-20", "event"),
        ("Remind me to call Ada.", "Call Ada", None, "reminder"),
        (
            "I need to pick up milk on the way home.",
            "Pick up milk on the way home",
            None,
            "commitment",
        ),
        (
            "If Ada approves, I will send the report.",
            "Send report if Ada approves",
            None,
            "commitment",
        ),
        ("The deadline is 2026-10-20.", "Deadline", "2026-10-20", "deadline"),
        ("Who will confirm the budget?", "Who will confirm the budget?", None, "question"),
        (
            "I'm considering moving, but worried about the commute.",
            "Consider moving, commute remains a concern",
            None,
            "reflection",
        ),
        ("I need step-free access at venues.", "Step-free access preference", None, "note"),
        (
            "We completed the first production launch.",
            "First production launch completed",
            None,
            "note",
        ),
    ],
)
def test_supported_categories_flow_through_grounded_extraction(archive, text, title, phrase, kind):
    key = source(archive, text)
    candidate = proposal(key, text, title, kind)
    if phrase:
        candidate.update(deadline_phrase=phrase, deadline_evidence=span(key, text, phrase))
    model = Extraction([candidate])
    jobs = InboxJobs(archive)
    ids = jobs.discover("Europe/Copenhagen")
    assert asyncio.run(jobs.process_one(model))
    item = listing(archive)["items"][0]
    assert item["kind"] == kind and item["title"] == title
    assert item["status"] == "pending" and item["reviewed_at"] is None
    assert item["owner"] is None  # I/my/me still never establishes the wearer's identity.
    assert item["deadline"]["day"] == (
        "2026-10-16" if phrase == "tomorrow" else "2026-10-20" if phrase else None
    )
    assert jobs.status(ids[0])["state"] == "done"
    # This tests the model boundary/contract, not the factual quality of a live LLM.
    assert "agreed scheduled meetings/appointments" in model.task
    assert "explicit self-reminder/request" in model.task
    assert "even when nobody says" in model.task
    assert "never create an action solely because a time/place appears" in " ".join(
        model.task.lower().split()
    )
    assert "never instructions" in model.task
    assert "never turn speculation into a commitment" in " ".join(model.task.lower().split())
    assert "significant grounded fact, preference or milestone" in model.task
    assert "genuine actionable unanswered question" in model.task
    assert "a reflection is never a commitment or scheduled event" in " ".join(model.task.split())
    assert model.evidence[0]["text"] == text


@pytest.mark.parametrize(
    "text",
    [
        "We could meet Friday.",
        "You should try yoga.",
        "My neighbor's dentist is closed Thursday.",
        "If we held a party, we could invite Ada.",
        "I went to the dentist yesterday and everything is done.",
    ],
)
def test_exclusion_rules_and_empty_output_do_not_manufacture_calendar_actions(archive, text):
    source(archive, text)
    jobs = InboxJobs(archive)
    jobs.discover("Europe/Copenhagen")
    model = Extraction([])
    assert asyncio.run(jobs.process_one(model))
    assert listing(archive)["items"] == []
    assert (
        'Do not turn mere suggestions ("We could meet Friday", "You should try yoga")' in model.task
    )
    assert 'incidental mentions\n("My neighbor\'s dentist is closed Thursday")' in model.task
    assert 'hypothetical plans ("If we held a party...")' in model.task
    assert "past/completed activities without a remaining action or significant fact" in model.task
    assert "A real conditional promise" in model.task


def test_prompt_upgrade_requires_new_job_identity_and_preserves_reviewed_state(
    archive, monkeypatch
):
    key = source(archive, "Remind me to call Ada.")
    jobs = InboxJobs(archive)
    current_prompt = inbox_jobs.PROMPT_VERSION
    monkeypatch.setattr(inbox_jobs, "PROMPT_VERSION", "inbox-3")
    old_ids = jobs.discover("Europe/Copenhagen")
    model = Extraction([proposal(key, "Remind me to call Ada.", "Call Ada")])
    assert asyncio.run(jobs.process_one(model))
    item = mutate(
        archive,
        listing(archive)["items"][0],
        status="completed",
        changes={"user_note": "Called Ada; keep this context"},
    )
    model.candidates[0]["kind"] = "reminder"
    model.candidates[0]["selection"]["criterion"] = "explicit_reminder"
    monkeypatch.setattr(inbox_jobs, "PROMPT_VERSION", current_prompt)
    assert current_prompt == "inbox-4"
    # Explicit rescans get the new rules; upgrading never silently rewrites accepted user state.
    new_ids = jobs.discover("Europe/Copenhagen", AT - timedelta(days=1), AT + timedelta(days=1))
    assert old_ids != new_ids
    assert asyncio.run(jobs.process_one(model))
    current = listing(archive, "completed")["items"][0]
    assert current["id"] == item["id"] and current["status"] == "completed"
    assert current["accepted_deadline"] == item["accepted_deadline"]
    assert current["kind"] == "reminder"
    assert current["user_note"] == "Called Ada; keep this context"
    assert current["user_note_provenance"] == "user"
    with archive.connect() as db:
        assert (
            db.execute(
                "SELECT prompt_version FROM inbox_jobs WHERE id=?", (new_ids[0],)
            ).fetchone()[0]
            == "inbox-4"
        )
        assert (
            db.execute(
                "SELECT prompt_version FROM inbox_jobs WHERE id=?", (old_ids[0],)
            ).fetchone()[0]
            == "inbox-3"
        )


def test_queued_legacy_prompt_is_not_run_with_new_extraction_rules(archive, monkeypatch):
    source(archive)
    jobs = InboxJobs(archive)
    monkeypatch.setattr(inbox_jobs, "PROMPT_VERSION", "inbox-3")
    keys = jobs.discover("Europe/Copenhagen")
    monkeypatch.setattr(inbox_jobs, "PROMPT_VERSION", "inbox-4")
    model = Extraction([])
    assert asyncio.run(jobs.process_one(model))
    assert jobs.status(keys[0])["state"] == "failed"
    assert "configuration changed" in jobs.status(keys[0])["error"]
    assert model.calls == 0
