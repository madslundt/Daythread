"""Extraction retry state should report unresolved failures rather than obsolete attempts."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_inbox import AT, TEXT, Model, listing, proposal, source
from test_inbox import archive as archive
from test_inbox_action_extraction import Extraction

from omi_local import inbox_jobs
from omi_local.domain import stamp
from omi_local.inbox_jobs import InboxJobs
from omi_local.inference import InferenceTruncated


def part(archive, key, length):
    with archive.connect() as db:
        revision = db.execute(
            "SELECT revision FROM inbox_sources WHERE source_id=?", (key,)
        ).fetchone()[0]
    return {"id": key, "revision": revision, "start": 0, "end": length}


def saved_job(
    archive,
    inputs,
    *,
    state="failed",
    prompt="inbox-3",
    model="unspecified",
    timezone="Europe/Copenhagen",
):
    key = str(uuid4())
    with archive.connect() as db:
        db.execute(
            "INSERT INTO inbox_jobs(id,fingerprint,inputs,timezone,model,prompt_version,state,"
            "attempts,error,created_at) VALUES(?,?,?,?,?,?,?,5,?,?)",
            (
                key,
                key,
                json.dumps(inputs),
                timezone,
                model,
                prompt,
                state,
                "Extraction failed: ValidationError",
                stamp(AT),
            ),
        )
        db.executemany(
            "INSERT INTO inbox_dependencies VALUES(?,?,?)",
            [(key, p["id"], p["revision"]) for p in inputs],
        )
    return key


def scan(jobs, at=AT):
    return jobs.discover(
        "Europe/Copenhagen", at - timedelta(days=1), at + timedelta(days=1), retry_failed=True
    )


def test_successful_current_prompt_rescan_resolves_legacy_failure_banner(archive, monkeypatch):
    source(archive)
    jobs = InboxJobs(archive)
    current = inbox_jobs.PROMPT_VERSION
    monkeypatch.setattr(inbox_jobs, "PROMPT_VERSION", "inbox-3")
    old = jobs.discover("Europe/Copenhagen")
    monkeypatch.setattr(inbox_jobs, "PROMPT_VERSION", current)
    inference = Extraction([])
    assert asyncio.run(jobs.process_one(inference))
    assert jobs.status(old[0])["state"] == "failed"
    assert inference.calls == 0
    fresh = jobs.discover(
        "Europe/Copenhagen", AT - timedelta(days=1), AT + timedelta(days=1), retry_failed=True
    )
    assert fresh != old
    assert asyncio.run(jobs.process_one(inference))
    assert jobs.status(fresh[0])["state"] == "done"
    assert inference.calls == 1
    # coverage.failed is what drives the user's 'Some extraction failed' warning.
    assert listing(archive)["coverage"]["jobs"].get("failed", 0) == 0
    old_result = jobs.status(old[0])
    assert old_result["state"] == "superseded"
    assert old_result["error"] == "Extraction configuration changed; rescan these dates"
    assert old_result["attempts"] == 1
    assert scan(jobs) == fresh
    assert not asyncio.run(jobs.process_one(inference))
    assert jobs.status(old[0]) == old_result
    assert inference.calls == 1


def test_explicit_retry_repairs_already_committed_replacement_without_more_inference(archive):
    key = source(archive)
    jobs = InboxJobs(archive)
    inference = Extraction([])
    current = scan(jobs)
    assert asyncio.run(jobs.process_one(inference))
    old = saved_job(
        archive,
        [part(archive, key, len(TEXT))],
    )
    assert listing(archive)["coverage"]["jobs"]["failed"] == 1
    assert scan(jobs) == current
    assert jobs.status(old)["state"] == "superseded"
    first = jobs.status(old)
    assert scan(jobs) == current
    assert jobs.status(old) == first
    assert not asyncio.run(jobs.process_one(inference)) and inference.calls == 1


def test_failed_current_replacement_does_not_retire_previous_failure(archive):
    text = "Remind me to submit the grant report tomorrow."
    key = source(archive, text)
    old = saved_job(archive, [part(archive, key, len(text))])
    jobs = InboxJobs(archive)
    current = scan(jobs)
    # A real schema/selection failure retries, then fails; it must never count as replacement proof.
    malformed = Extraction([{"kind": "reminder", "title": "Submit report", "evidence": []}])
    for _ in range(5):
        assert asyncio.run(jobs.process_one(malformed))
        with archive.connect() as db:
            db.execute("UPDATE inbox_jobs SET retry_at='' WHERE id=?", (current[0],))
    assert jobs.status(current[0])["state"] == "failed"
    assert jobs.status(old)["state"] == "failed"
    assert listing(archive)["coverage"]["jobs"]["failed"] == 2


def test_partial_split_success_does_not_hide_failure_until_complete_union_is_committed(archive):
    text = "Useful context. " * 250
    key = source(archive, text)
    old = saved_job(archive, [part(archive, key, len(text))])
    jobs = InboxJobs(archive)
    parent = scan(jobs)
    assert asyncio.run(jobs.process_one(Model(InferenceTruncated("truncated"))))
    assert jobs.status(parent[0])["state"] == "split"
    assert asyncio.run(jobs.process_one(Extraction([])))
    assert jobs.status(old)["state"] == "failed"
    assert asyncio.run(jobs.process_one(Extraction([])))
    assert jobs.status(old)["state"] == "superseded"
    assert listing(archive)["coverage"]["jobs"].get("failed", 0) == 0


@pytest.mark.parametrize(
    "old_prompt,old_model", [("inbox-3", "unspecified"), ("inbox-4", "other-route")]
)
def test_complete_context_requires_current_success_for_every_source(archive, old_prompt, old_model):
    text = "A grounded commitment."
    first = source(archive, text)
    later = AT + timedelta(days=3)
    context = source(archive, "Required context", later)
    first_part = part(archive, first, len(text))
    context_part = part(archive, context, len("Required context"))
    old = saved_job(archive, [first_part, context_part])
    # Old prompt/different-route success is not current replacement proof for this context.
    saved_job(archive, [context_part], state="done", prompt=old_prompt, model=old_model)
    jobs = InboxJobs(archive)
    scan(jobs)
    assert asyncio.run(jobs.process_one(Extraction([])))
    assert jobs.status(old)["state"] == "failed"
    scan(jobs, later)
    assert asyncio.run(jobs.process_one(Extraction([])))
    assert jobs.status(old)["state"] == "superseded"


def test_unrelated_changed_revision_and_different_timezone_failures_remain(archive):
    text = "A grounded commitment."
    key = source(archive, text)
    unrelated = source(archive, "Other day", AT + timedelta(days=3))
    current_part = part(archive, key, len(text))
    obsolete = saved_job(archive, [current_part | {"revision": "previous-revision"}])
    other = saved_job(archive, [part(archive, unrelated, len("Other day"))])
    timezone = saved_job(archive, [current_part], timezone="America/New_York")
    jobs = InboxJobs(archive)
    scan(jobs)
    assert asyncio.run(jobs.process_one(Extraction([])))
    assert all(jobs.status(key)["state"] == "failed" for key in (obsolete, other, timezone))


def test_retry_split_work_does_not_requeue_failed_legacy_configuration(archive):
    text = "Useful context. " * 250
    key = source(archive, text)
    jobs = InboxJobs(archive)
    parent = scan(jobs)
    assert asyncio.run(jobs.process_one(Model(InferenceTruncated("truncated"))))
    assert jobs.status(parent[0])["state"] == "split"
    old = saved_job(archive, [part(archive, key, len(text))])
    before = jobs.status(old)
    with archive.connect() as db:
        child = db.execute("SELECT id FROM inbox_jobs WHERE state='queued' LIMIT 1").fetchone()[0]
        db.execute("UPDATE inbox_jobs SET state='failed',attempts=5 WHERE id=?", (child,))
    scan(jobs)
    assert jobs.status(child)["state"] == "queued"
    assert jobs.status(old) == before


def test_failed_publish_rolls_back_retirement_and_new_derivatives_together(archive, monkeypatch):
    text = "A grounded commitment."
    key = source(archive, text)
    old = saved_job(archive, [part(archive, key, len(text))])
    jobs = InboxJobs(archive)
    current = scan(jobs)
    real = inbox_jobs.retire_replaced_failures

    def fail_commit(db, key, prompt):
        real(db, key, prompt)
        raise RuntimeError("Simulated publisher failure after retirement")

    monkeypatch.setattr(inbox_jobs, "retire_replaced_failures", fail_commit)
    assert asyncio.run(jobs.process_one(Extraction([proposal(key, text)])))
    assert jobs.status(current[0])["state"] == "queued"
    assert jobs.status(old)["state"] == "failed"
    assert listing(archive)["items"] == []
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM inbox_events").fetchone()[0] == 0
