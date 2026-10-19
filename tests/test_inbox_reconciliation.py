"""Conservative reconciliation and explicit review invariants."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from inbox_selection_fixtures import selection

from omi_local.domain import stamp
from omi_local.inbox_domain import Mutation
from omi_local.inbox_jobs import InboxJobs
from omi_local.inbox_store import InboxStore
from omi_local.store import Store

AT = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)


@pytest.fixture
def archive(tmp_path):
    return Store(tmp_path)


def add_source(archive, text):
    key = str(uuid4())
    with archive.connect() as db:
        db.execute(
            "INSERT INTO audio "
            "(id,device_id,started_at,ended_at,digest,path,state,text,audio_available) "
            "VALUES(?,?,?,?,?,?,'done',?,0)",
            (key, "test", stamp(AT), stamp(AT + timedelta(seconds=20)), "", "", text),
        )
    return key


def candidate(key, text, title, phrase=None):
    result = {
        "kind": "commitment",
        "title": title,
        "evidence": [{"source_id": key, "start": 0, "end": len(text), "quote": text}],
        "selection": selection(key, text, "commitment"),
    }
    if phrase:
        start = text.index(phrase)
        result.update(
            deadline_phrase=phrase,
            deadline_evidence={
                "source_id": key,
                "start": start,
                "end": start + len(phrase),
                "quote": phrase,
            },
        )
    return result


def extract(archive, candidates, zone="Europe/Copenhagen", model="unspecified"):
    jobs = InboxJobs(archive)
    jobs.discover(zone, AT - timedelta(days=1), AT + timedelta(days=1), model=model)

    class Model:
        async def generate(self, task, evidence):
            return json.dumps({"candidates": candidates})

    while asyncio.run(jobs.process_one(Model())):
        pass
    return InboxStore(archive).listing("pending", None, None, 50, 0)["items"]


def edit(archive, item, **fields):
    return InboxStore(archive).mutate(
        item["id"],
        Mutation.model_validate(
            {"operation_id": str(uuid4()), "expected_version": item["version"], **fields}
        ),
    )


def test_different_supported_dates_prevent_broad_quote_merge(archive):
    text = "Send report to Ada on 2026-10-20 and send report to Bo on 2026-10-21."
    key = add_source(archive, text)
    proposals = [
        candidate(key, text, "Send report to Ada", "2026-10-20"),
        candidate(key, text, "Send report to Bo", "2026-10-21"),
    ]
    items = extract(archive, proposals)
    assert len(items) == 2
    assert {i["deadline"]["day"] for i in items} == {"2026-10-20", "2026-10-21"}
    # A second overlapping context preserves both identities and both deadlines.
    archive.correct_transcript(key, text + " Confirmed.")
    proposals = [
        candidate(key, text + " Confirmed.", "Send report to Ada", "2026-10-20"),
        candidate(key, text + " Confirmed.", "Send report to Bo", "2026-10-21"),
    ]
    repeated = extract(archive, proposals)
    assert {i["id"] for i in repeated} == {i["id"] for i in items}


def test_paraphrased_overlap_still_deduplicates(archive):
    text = "Send report to Ada tomorrow."
    key = add_source(archive, text)
    original = extract(archive, [candidate(key, text, "Send report to Ada", "tomorrow")])
    repeated = extract(
        archive, [candidate(key, text, "Send Ada the report", "tomorrow")], model="re-extraction"
    )
    assert len(repeated) == 1
    assert repeated[0]["id"] == original[0]["id"]


@pytest.mark.parametrize("status", ["pending", "completed"])
def test_reopening_or_completing_duplicate_clears_link(archive, status):
    text = "Send report to Ada on 2026-10-20 and send report to Bo on 2026-10-21."
    key = add_source(archive, text)
    first, second = extract(
        archive,
        [
            candidate(key, text, "Send report to Ada", "2026-10-20"),
            candidate(key, text, "Send report to Bo", "2026-10-21"),
        ],
    )
    duplicate = edit(archive, second, status="dismissed", duplicate_of=first["id"])
    assert duplicate["duplicate_of"] == first["id"]
    reopened = edit(archive, duplicate, status=status)
    assert reopened["duplicate_of"] is None
    dismissed = edit(archive, reopened, status="dismissed", duplicate_of=first["id"])
    ordinary = edit(archive, dismissed, status="dismissed", duplicate_of=None)
    assert ordinary["duplicate_of"] is None


def test_reviewed_extracted_deadline_stays_accepted_after_timezone_rescan(archive):
    text = "Send report to Ada tomorrow."
    key = add_source(archive, text)
    proposals = [candidate(key, text, "Send report to Ada", "tomorrow")]
    item = extract(archive, proposals)[0]
    reviewed = edit(archive, item, status="completed")
    assert "deadline" not in reviewed["overrides"]
    assert reviewed["deadline"]["day"] == "2026-10-17"
    extract(archive, proposals, "America/New_York")
    current = InboxStore(archive).item(item["id"])
    assert current["status"] == "completed"
    assert current["deadline"]["day"] == "2026-10-17"
    assert current["deadline"]["timezone"] == "Europe/Copenhagen"
    assert current["proposal"]["deadline"]["day"] == "2026-10-16"
    assert current["health"] == "needs_review"
    accepted = edit(archive, current, reset=["deadline"])
    assert accepted["deadline"]["day"] == "2026-10-16"
    assert accepted["deadline"]["timezone"] == "America/New_York"
    assert accepted["health"] == "current"


@pytest.mark.parametrize("phrase", [None, "tomorrow"])
def test_distinct_actions_with_shared_quote_and_deadline_stay_distinct(archive, phrase):
    text = "Send budget report and send status report tomorrow."
    key = add_source(archive, text)
    proposals = [
        candidate(key, text, "Send budget report", phrase),
        candidate(key, text, "Send status report", phrase),
    ]
    items = extract(archive, proposals)
    assert len(items) == 2
    by_title = {item["title"]: item["id"] for item in items}
    repeated = extract(archive, list(reversed(proposals)), model="re-extraction")
    assert {item["title"]: item["id"] for item in repeated} == by_title
    # Reserve an exact match for the other candidate before attempting a paraphrase.
    mixed = [candidate(key, text, "Send report budget", phrase), proposals[1]]
    mixed_items = extract(archive, mixed, model="third-extraction")
    assert len(mixed_items) == 2
    assert (
        next(i for i in mixed_items if i["title"] == "Send status report")["id"]
        == by_title["Send status report"]
    )


def test_speaker_label_alone_cannot_establish_owner(archive):
    from omi_local.inbox_domain import validate_candidates

    text = "Ada: I promise to send the report tomorrow."
    key = add_source(archive, text)
    proposed = candidate(key, text, "Send report", "tomorrow")
    proposed.update(
        owner="Ada", owner_evidence={"source_id": key, "start": 0, "end": 3, "quote": "Ada"}
    )
    jobs = InboxJobs(archive)
    jobs.discover("UTC")
    job = jobs.claim()
    with archive.connect() as db:
        sources = jobs.sources(db, job)
    with pytest.raises(ValueError, match="speaker label"):
        validate_candidates(json.dumps({"candidates": [proposed]}), sources, "UTC")
