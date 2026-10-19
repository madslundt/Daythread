"""Exact duplicate points consolidate without conflating independently grounded obligations."""

from test_inbox import archive as archive
from test_inbox import listing, source, span
from test_inbox_relevance import candidate, extract


def test_same_title_overlapping_evidence_can_contain_distinct_obligations(archive):
    text = "Send budget report tomorrow and send status report tomorrow."
    key = source(archive, text)
    first = candidate(
        key,
        text,
        "Send budget report tomorrow",
        "Send report",
        "commitment",
        "lasting_follow_up",
        "The budget report remains due tomorrow.",
    )
    second = candidate(
        key,
        text,
        "budget report tomorrow and send status report tomorrow.",
        "Send report",
        "commitment",
        "lasting_follow_up",
        "The separate status report also remains due.",
    )
    extract(archive, [first, second])
    assert len(listing(archive)["items"]) == 2


def test_task_independent_reminder_and_agreed_event_remain_separate(archive):
    text = "Prepare the project review; remind me about the review; we agreed to hold it next week."
    key = source(archive, text)
    proposed = [
        candidate(key, text, text, "Project review", kind, criterion, reason)
        for kind, criterion, reason in (
            ("commitment", "lasting_follow_up", "Preparation remains to be completed."),
            ("reminder", "explicit_reminder", "A separate reminder is deliberately requested."),
            ("event", "agreed_event", "The review itself is agreed for next week."),
        )
    ]
    extract(archive, proposed)
    assert {item["kind"] for item in listing(archive)["items"]} == {
        "commitment",
        "reminder",
        "event",
    }


def test_exact_quotes_never_transfer_unknown_ownership_or_due_dates(archive):
    text = "Ada and Bo must send the report on 2026-10-20 or 2026-10-21."
    key = source(archive, text)
    proposed = []
    for owner, due in (
        (None, None),
        ("Ada", None),
        ("Bo", None),
        ("Ada", "2026-10-20"),
        ("Ada", "2026-10-21"),
    ):
        item = candidate(
            key,
            text,
            text,
            "Send report",
            "commitment",
            "lasting_follow_up",
            "The report remains a concrete obligation in the quoted context.",
        )
        if owner:
            item.update(owner=owner, owner_evidence=span(key, text, owner))
        if due:
            item.update(deadline_phrase=due, deadline_evidence=span(key, text, due))
        proposed.append(item)
    extract(archive, proposed)
    actual = {(item["owner"], item["deadline"]["day"]) for item in listing(archive)["items"]}
    assert actual == {
        (None, None),
        ("Ada", None),
        ("Bo", None),
        ("Ada", "2026-10-20"),
        ("Ada", "2026-10-21"),
    }
