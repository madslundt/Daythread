"""Exercise the real one-response relevance gate with mixed noisy and useful proposals."""

import asyncio
import json
from datetime import timedelta

import pytest
from test_inbox import AT, listing, mutate, source, span
from test_inbox import archive as archive
from test_inbox_action_extraction import Extraction

from omi_local.inbox_domain import validate_candidates
from omi_local.inbox_jobs import InboxJobs
from omi_local.inbox_store import InboxStore


def candidate(key, full, quote, title, kind, criterion, reason):
    anchor = span(key, full, quote)
    return {
        "kind": kind,
        "title": title,
        "evidence": [anchor],
        "selection": {"criterion": criterion, "reason": reason, "evidence": [anchor.copy()]},
    }


def extract(archive, candidates, *, model="unspecified"):
    jobs = InboxJobs(archive)
    jobs.discover("Europe/Copenhagen", AT - timedelta(days=1), AT + timedelta(days=1), model=model)
    inference = Extraction(
        candidates
    )  # Return EVERY supplied candidate; the backend does selection.
    while asyncio.run(jobs.process_one(inference)):
        pass
    return inference


def sources_for(archive):
    jobs = InboxJobs(archive)
    jobs.discover("Europe/Copenhagen")
    job = jobs.claim()
    with archive.connect() as db:
        return jobs.sources(db, job)


def test_mixed_candidates_keep_contextual_utility_and_drop_conversational_noise(archive):
    points = [
        (
            "commitment",
            "Pass salt",
            "Pass the salt, please.",
            "none",
            "Only an immediate table action.",
        ),
        (
            "commitment",
            "Grab coffee",
            "I'll grab coffee now.",
            "none",
            "Fleeting action in this conversation.",
        ),
        (
            "note",
            "Blue preference",
            "I like blue.",
            "none",
            "Casual preference without stated later use.",
        ),
        (
            "question",
            "Weather",
            "How is the weather? It's sunny.",
            "none",
            "The question is already answered.",
        ),
        (
            "question",
            "Thursday color",
            "What color is Thursday?",
            "none",
            "Idle question without a next step.",
        ),
        (
            "reflection",
            "Something someday",
            "Maybe I'll do something someday.",
            "none",
            "No concrete unresolved consideration.",
        ),
        (
            "reminder",
            "Try yoga",
            "You should try yoga.",
            "none",
            "Generic suggestion, not a deliberate reminder.",
        ),
        (
            "event",
            "Hypothetical party",
            "If we held a party, maybe Ada could come.",
            "none",
            "No agreed event or meaningful unresolved choice.",
        ),
        (
            "note",
            "Step-free entrance",
            "For future visits use Oak Street; the step-free door is on the east side.",
            "durable_reference",
            "The stated access instructions are useful on future visits.",
        ),
        (
            "note",
            "Accessible venues",
            "Please avoid upstairs venues: I require step-free access.",
            "durable_reference",
            "The stated access requirement guides future venue choices.",
        ),
        (
            "reminder",
            "Buy coffee",
            "Remind me to buy coffee tomorrow.",
            "explicit_reminder",
            "The speaker deliberately requests a reminder for tomorrow.",
        ),
        (
            "reminder",
            "Buy salt",
            "Remind me to buy salt tomorrow.",
            "explicit_reminder",
            "A deliberate reminder request remains useful after dinner.",
        ),
        (
            "commitment",
            "Submit grant report",
            "I will submit the grant report by 2026-11-01; lateness delays funding.",
            "lasting_follow_up",
            "Submission remains owed after this conversation and delay affects funding.",
        ),
        (
            "deadline",
            "Renew contract",
            "The contract expires on 2026-11-01; renewal is needed to retain support.",
            "lasting_follow_up",
            "The stated expiry requires renewal to retain support.",
        ),
        (
            "event",
            "Project review",
            "We agreed to a project review on Tuesday; bring the draft for approval.",
            "agreed_event",
            "An agreed review requires later attendance with the draft.",
        ),
        (
            "question",
            "Contract signer",
            "Who must sign the contract before we can submit on Friday?",
            "consequential_question",
            "The unresolved signer blocks Friday's contract submission.",
        ),
        (
            "reflection",
            "Supplier risk",
            "I'm considering a second supplier: this vendor's outages could block production.",
            "meaningful_reflection",
            "The unresolved supplier tradeoff affects the stated production risk.",
        ),
    ]
    text = "\n".join(point[2] for point in points)
    key = source(archive, text)
    candidates = [
        candidate(key, text, quote, title, kind, criterion, reason)
        for kind, title, quote, criterion, reason in points
    ]
    inference = extract(archive, candidates)
    assert inference.calls == 1
    expected = {title for _, title, _, criterion, _ in points if criterion != "none"}
    items = listing(archive)["items"]
    assert {item["title"] for item in items} == expected
    assert len(candidates) == 17 and len(items) == 9
    assert all(item["owner"] is None for item in items)
    assert all("selection" not in item["proposal"] for item in items)
    assert {item["title"] for item in items if item["kind"] == "reminder"} == {
        "Buy coffee",
        "Buy salt",
    }
    assert all(InboxStore(archive).item(item["id"])["evidence"] for item in items)


@pytest.mark.parametrize("bad", ["missing", "blank", "incompatible"])
def test_missing_or_malformed_selection_is_retryable_instead_of_scanning_away_useful_items(
    archive, bad
):
    text = "I'll grab coffee now. Remind me to submit the grant report tomorrow."
    key = source(archive, text)
    valid = candidate(
        key,
        text,
        "Remind me to submit the grant report tomorrow.",
        "Submit grant report",
        "reminder",
        "explicit_reminder",
        "An explicit reminder is requested.",
    )
    noisy = candidate(
        key, text, "I'll grab coffee now.", "Grab coffee", "commitment", "lasting_follow_up", ""
    )
    if bad == "missing":
        noisy.pop("selection")
    elif bad == "incompatible":
        noisy["selection"].update(
            criterion="durable_reference", reason="It is a stated preference."
        )
    jobs = InboxJobs(archive)
    ids = jobs.discover("Europe/Copenhagen")
    assert asyncio.run(jobs.process_one(Extraction([noisy, valid])))
    state = jobs.status(ids[0])
    assert state["state"] == "queued" and state["attempts"] == 1
    assert state["error"].startswith("Extraction failed:")
    assert listing(archive)["items"] == []


@pytest.mark.parametrize("broken", ["quote", "source", "offset"])
def test_a_positive_utility_claim_needs_exact_original_support(archive, broken):
    text = "Submit the grant report tomorrow to retain funding."
    key = source(archive, text)
    proposed = candidate(
        key,
        text,
        text,
        "Submit grant report",
        "commitment",
        "lasting_follow_up",
        "The report remains due tomorrow and affects funding.",
    )
    support = proposed["selection"]["evidence"][0]
    if broken == "quote":
        support["quote"] = "Invented importance"
    elif broken == "source":
        from uuid import uuid4

        support["source_id"] = str(uuid4())
    else:
        support["start"] += 1
    with pytest.raises(ValueError):
        validate_candidates(
            json.dumps({"candidates": [proposed]}), sources_for(archive), "Europe/Copenhagen"
        )
    assert listing(archive)["items"] == []


def test_utility_can_be_grounded_in_context_outside_the_short_action_quote(archive):
    text = "The grant deadline is Friday; missing it cancels funding. I will send the report."
    key = source(archive, text)
    proposed = candidate(
        key,
        text,
        "I will send the report.",
        "Send report",
        "commitment",
        "lasting_follow_up",
        "The nearby context establishes a funding deadline.",
    )
    proposed["selection"]["evidence"] = [
        span(key, text, "I will send the report."),
        span(key, text, "The grant deadline is Friday; missing it cancels funding."),
    ]
    extract(archive, [proposed])
    assert listing(archive)["items"][0]["title"] == "Send report"


def test_noise_cannot_borrow_a_separate_real_obligations_utility(archive):
    text = "Pass the salt. Submit the grant report tomorrow or funding is cancelled."
    key = source(archive, text)
    noisy = candidate(
        key,
        text,
        "Pass the salt.",
        "Pass salt",
        "commitment",
        "lasting_follow_up",
        "The grant report affects funding.",
    )
    noisy["selection"]["evidence"] = [
        span(key, text, "Submit the grant report tomorrow or funding is cancelled.")
    ]
    with pytest.raises(ValueError, match="own point"):
        validate_candidates(
            json.dumps({"candidates": [noisy]}), sources_for(archive), "Europe/Copenhagen"
        )


def test_no_quota_keeps_all_supported_useful_points_and_all_noise_can_yield_empty(archive):
    quotes = [f"Remind me to send report {index} to its client tomorrow." for index in range(31)]
    text = "\n".join(quotes)
    key = source(archive, text)
    proposals = [
        candidate(
            key,
            text,
            quote,
            f"Send report {index}",
            "reminder",
            "explicit_reminder",
            f"The speaker explicitly requests a reminder for client report {index}.",
        )
        for index, quote in enumerate(quotes)
    ]
    assert extract(archive, proposals).calls == 1
    assert len(listing(archive)["items"]) == 31
    for proposed in proposals:
        proposed["selection"]["criterion"] = "none"
    extract(archive, proposals, model="new-selection")
    assert all(item["health"] == "withdrawn" for item in listing(archive)["items"])


def test_same_point_consolidates_across_kinds_without_losing_distinct_actions(archive):
    text = "Remind me to send the budget tomorrow and send the status tomorrow."
    key = source(archive, text)
    first = candidate(
        key,
        text,
        "Remind me to send the budget tomorrow",
        "Send budget",
        "commitment",
        "explicit_reminder",
        "The budget remains to be sent tomorrow.",
    )
    duplicate = candidate(
        key,
        text,
        "Remind me to send the budget tomorrow",
        "SEND BUDGET!",
        "reminder",
        "explicit_reminder",
        "The budget reminder is explicitly requested.",
    )
    distinct = candidate(
        key,
        text,
        "send the status tomorrow",
        "Send status",
        "commitment",
        "lasting_follow_up",
        "The separate status report also remains to be sent.",
    )
    extract(archive, [first, duplicate, distinct])
    items = listing(archive)["items"]
    assert {(item["title"], item["kind"]) for item in items} == {
        ("SEND BUDGET!", "reminder"),
        ("Send status", "commitment"),
    }
    budget = next(item for item in items if item["kind"] == "reminder")
    assert len(InboxStore(archive).item(budget["id"])["evidence"]) == 1


def test_selection_rescan_preserves_reviewed_dismissed_notes_and_event_history(archive):
    text = "I'll grab coffee now. I will send the grant report tomorrow to retain funding."
    key = source(archive, text)
    noisy = candidate(
        key,
        text,
        "I'll grab coffee now.",
        "Grab coffee",
        "commitment",
        "lasting_follow_up",
        "Legacy broad interpretation of a future action.",
    )
    useful = candidate(
        key,
        text,
        "I will send the grant report tomorrow to retain funding.",
        "Send grant report",
        "commitment",
        "lasting_follow_up",
        "The report affects funding.",
    )
    extract(archive, [noisy, useful])
    original = {item["title"]: item for item in listing(archive)["items"]}
    dismissed = mutate(
        archive,
        original["Grab coffee"],
        status="dismissed",
        changes={"user_note": "Keep my explanation"},
    )
    completed = mutate(
        archive,
        original["Send grant report"],
        status="completed",
        changes={"user_note": "Submitted; retain context"},
    )
    noisy["selection"]["criterion"] = "none"
    extract(archive, [noisy, useful], model="stricter-relevance")
    assert listing(archive)["items"] == []
    for saved in (dismissed, completed):
        current = InboxStore(archive).item(saved["id"])
        assert current["status"] == saved["status"]
        assert current["user_note"] == saved["user_note"]
        assert current["history"][: len(saved["history"])] == saved["history"]
    assert listing(archive, "dismissed")["items"][0]["id"] == dismissed["id"]


def test_an_all_noise_first_pass_publishes_no_items(archive):
    text = "Pass the salt. I'll grab coffee."
    key = source(archive, text)
    proposed = candidate(
        key,
        text,
        text,
        "Conversation actions",
        "commitment",
        "none",
        "These fleeting actions have no supported later utility.",
    )
    inference = extract(archive, [proposed])
    assert inference.calls == 1 and listing(archive)["items"] == []
