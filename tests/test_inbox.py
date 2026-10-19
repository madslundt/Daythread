"""Inbox behavior uses disposable archives and simulated extraction, never live models."""

import asyncio
import io
import json
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from inbox_selection_fixtures import selection
from pydantic import ValidationError

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.inbox_domain import Deadline, Mutation, resolve_deadline, validate_candidates
from omi_local.inbox_jobs import InboxJobs
from omi_local.inbox_store import InboxConflict, InboxStore
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
TEXT = "Ada: I promise to send the résumé tomorrow. What about the budget?"


@pytest.fixture
def archive(tmp_path):
    return Store(tmp_path)


def source(archive, text=TEXT, at=AT):
    key = str(uuid4())
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        audio.writeframes(b"\x00\x00" * 16000)
    archive.ingest(__import__("uuid").UUID(key), "test", at, data.getvalue(), 1)
    archive.complete(key, text)
    return key


def span(key, text, phrase=None):
    quote = phrase or text
    start = text.index(quote)
    return {"source_id": key, "start": start, "end": start + len(quote), "quote": quote}


def proposal(key, text=TEXT, title="Send the résumé tomorrow", kind="commitment"):
    return {
        "kind": kind,
        "title": title,
        "owner": None,
        "deadline_phrase": None,
        "evidence": [span(key, text)],
        "selection": selection(key, text, kind),
    }


class Model:
    def __init__(self, candidates, action=None):
        self.candidates, self.action = candidates, action

    async def generate(self, task, evidence):
        assert "untrusted" in task
        if self.action:
            self.action()
        if isinstance(self.candidates, Exception):
            raise self.candidates
        return json.dumps({"candidates": self.candidates}, ensure_ascii=False)


def run(archive, candidates, action=None):
    jobs = InboxJobs(archive)
    ids = jobs.discover("Europe/Copenhagen")
    while asyncio.run(jobs.process_one(Model(candidates, action))):
        if action:
            break
    return ids


def listing(archive, status="pending"):
    return InboxStore(archive).listing(status, None, None, 200, 0)


def mutate(archive, item, **changes):
    return InboxStore(archive).mutate(
        item["id"],
        Mutation.model_validate(
            {"operation_id": str(uuid4()), "expected_version": item["version"], **changes}
        ),
    )


def test_candidate_evidence_unicode_and_relative_dates(archive):
    text = "Ada will send the résumé tomorrow. What about the budget?"
    key = source(archive, text)
    candidate = proposal(key, text)
    candidate.update(
        owner="Ada",
        owner_evidence=span(key, text, "Ada"),
        deadline_phrase="tomorrow",
        deadline_evidence=span(key, text, "tomorrow"),
    )
    run(archive, [candidate])
    item = listing(archive)["items"][0]
    assert item["owner"] == "Ada"
    assert item["deadline"]["day"] == "2026-10-16"
    assert item["deadline"]["precision"] == "date"
    detail = InboxStore(archive).item(item["id"])
    assert detail["evidence"][0]["quote"] == text
    assert detail["evidence"][0]["timing"] == "chunk"
    assert listing(archive)["coverage"]["jobs"]["done"] == 1


@pytest.mark.parametrize("broken", ["quote", "source", "offset", "owner", "deadline"])
def test_reject_unsupported_fields(archive, broken):
    key = source(archive)
    candidate = proposal(key)
    if broken == "quote":
        candidate["evidence"][0]["quote"] = "Fabricated promise"
    elif broken == "source":
        candidate["evidence"][0]["source_id"] = str(uuid4())
    elif broken == "offset":
        candidate["evidence"][0]["end"] += 1
    elif broken == "owner":
        candidate.update(owner="I", owner_evidence=span(key, TEXT, "I"))
    else:
        candidate.update(deadline_phrase="next Friday")
    jobs = InboxJobs(archive)
    jobs.discover("UTC")
    job = jobs.claim()
    with archive.connect() as db:
        sources = jobs.sources(db, job)
    with pytest.raises((ValueError, ValidationError)):
        validate_candidates(json.dumps({"candidates": [candidate]}), sources, "UTC")


def test_edits_null_reset_status_and_idempotency(archive):
    key = source(archive)
    run(archive, [proposal(key)])
    item = listing(archive)["items"][0]
    body = Mutation.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_version": item["version"],
            "changes": {"owner": None, "title": "My action", "deadline": None},
            "status": "completed",
        }
    )
    store = InboxStore(archive)
    edited = store.mutate(item["id"], body)
    assert store.mutate(item["id"], body) == edited
    assert edited["overrides"]["owner"] is None
    assert edited["title"] == "My action"
    assert not listing(archive)["items"]
    with pytest.raises(InboxConflict):
        store.mutate(item["id"], body.model_copy(update={"status": "dismissed"}))
    with pytest.raises(InboxConflict):
        mutate(archive, item, status="pending")
    reopened = mutate(archive, edited, status="pending", reset=["title"])
    assert reopened["title"] == "Send the résumé tomorrow"
    assert reopened["overrides"] == {"owner": None, "deadline": None}
    assert InboxStore(Store(archive.root)).item(item["id"])["status"] == "pending"


def test_correction_preserves_edits_and_reanchors(archive):
    key = source(archive)
    run(archive, [proposal(key)])
    item = mutate(
        archive,
        listing(archive)["items"][0],
        changes={"title": "Manual title", "owner": None},
        status="dismissed",
    )
    archive.correct_transcript(key, TEXT + " Corrected.")
    stale = InboxStore(archive).item(item["id"])
    assert stale["health"] == "needs_review"
    assert all(not e["current"] for e in stale["evidence"])
    run(archive, [proposal(key, TEXT + " Corrected.")])
    result = InboxStore(archive).item(item["id"])
    assert result["health"] == "current"
    assert result["title"] == "Manual title"
    assert result["status"] == "dismissed"
    assert len(listing(archive, "dismissed")["items"]) == 1


@pytest.mark.parametrize("action", ["correct", "delete", "expire"])
def test_inflight_evidence_cannot_return(archive, action):
    key = source(archive)

    def change():
        if action == "correct":
            archive.correct_transcript(key, "No promise was made.")
        elif action == "delete":
            archive.delete_audio(key)
        else:
            archive.apply_retention(0, 1, now=AT + timedelta(days=2))

    run(archive, [proposal(key)], change)
    assert not listing(archive)["items"]


def test_context_deletion_purges_manual_content_and_replay(archive):
    key = source(archive)
    context = source(archive, "Context only", AT + timedelta(seconds=2))
    run(archive, [proposal(key)])
    item = mutate(archive, listing(archive)["items"][0], changes={"title": "Private edit"})
    archive.correct_transcript(context, "Corrected context")
    archive.delete_audio(context)
    assert InboxStore(archive).item(item["id"]) is None
    with archive.connect() as db:
        assert not db.execute("SELECT * FROM inbox_events").fetchall()
        assert not db.execute("SELECT * FROM inbox_operations").fetchall()
    # Deleting and reimporting the same UUID cannot silently resurrect extraction.
    archive.delete_audio(key)
    with archive.connect() as db:
        db.execute(
            "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text) "
            "VALUES(?,?,?,?,?,?,'done',?)",
            (key, "omi-api", stamp(AT), stamp(AT + timedelta(seconds=1)), "", "", TEXT),
        )
    assert InboxJobs(archive).discover("UTC") == []


def test_audio_expiry_keeps_item_transcript_expiry_purges(archive):
    key = source(archive)
    run(archive, [proposal(key)])
    item = listing(archive)["items"][0]
    archive.apply_retention(1, 0, now=AT + timedelta(days=2))
    assert not InboxStore(archive).item(item["id"])["evidence"][0]["audio_available"]
    archive.apply_retention(1, 1, now=AT + timedelta(days=2))
    assert InboxStore(archive).item(item["id"]) is None


def test_overlap_dedup_and_duplicate_dismissal(archive):
    key = source(archive)
    run(archive, [proposal(key)])
    source(archive, "Later context", AT + timedelta(seconds=2))
    run(archive, [proposal(key)])
    assert len(listing(archive)["items"]) == 1
    second = source(archive, TEXT, AT + timedelta(days=1))
    run(archive, [proposal(second)])
    items = listing(archive)["items"]
    assert len(items) == 2  # Equal wording on a different day is not proof of identity.
    dismissed = mutate(archive, items[1], status="dismissed", duplicate_of=items[0]["id"])
    assert dismissed["duplicate_of"] == items[0]["id"]
    archive.correct_transcript(key, TEXT + " Again.")
    run(archive, [proposal(key, TEXT + " Again.")])
    assert len(listing(archive, "dismissed")["items"]) == 1


def test_empty_rescan_with_new_answer_withdraws_question_without_completing(archive):
    text = "What about the budget?"
    key = source(archive, text)
    run(archive, [proposal(key, text, "Budget?", "question")])
    item = listing(archive)["items"][0]
    source(archive, "The budget is approved.", AT + timedelta(seconds=2))
    run(archive, [])
    result = InboxStore(archive).item(item["id"])
    assert result["status"] == "pending"
    assert result["health"] == "withdrawn"


def test_retry_failure_sanitization_and_recovery(archive):
    source(archive)
    jobs = InboxJobs(archive)
    keys = jobs.discover("UTC")
    for _ in range(5):
        assert asyncio.run(jobs.process_one(Model(ValueError("secret transcript credential"))))
        with archive.connect() as db:
            db.execute("UPDATE inbox_jobs SET retry_at=''")
    status = jobs.status(keys[0])
    assert status["state"] == "failed"
    assert status["attempts"] == 5
    assert "secret" not in status["error"]
    assert jobs.discover("UTC", AT - timedelta(days=1), AT + timedelta(days=1), True) == keys
    assert asyncio.run(jobs.process_one(Model([])))
    assert jobs.status(keys[0])["state"] == "done"


def test_lease_fences_late_worker_and_recovers(archive):
    key = source(archive)
    jobs = InboxJobs(archive)
    jobs.discover("UTC")
    first = jobs.claim()
    with archive.connect() as db:
        db.execute("UPDATE inbox_jobs SET claimed_at=?", (stamp(AT - timedelta(days=1000)),))
    second = jobs.claim()
    assert second["claim"] != first["claim"]
    jobs.finish_stale(first)
    assert jobs.status(first["id"])["state"] == "running"
    archive.delete_audio(key)
    assert jobs.status(first["id"]) is None


def test_large_transcript_coverage_and_backfill_is_explicit(archive):
    key = source(archive, "a" * 22000)
    # Simulate upgrading an existing archive: backfill sources start automatic=0.
    with archive.connect() as db:
        db.execute("DELETE FROM inbox_sources")
    archive = Store(archive.root)
    jobs = InboxJobs(archive)
    assert jobs.discover("UTC") == []
    keys = jobs.discover("UTC", AT - timedelta(days=1), AT + timedelta(days=1))
    with archive.connect() as db:
        parts = [
            json.loads(db.execute("SELECT inputs FROM inbox_jobs WHERE id=?", (j,)).fetchone()[0])[
                0
            ]
            for j in keys
        ]
    assert min(p["start"] for p in parts) == 0
    assert max(p["end"] for p in parts) == 22000
    assert all(p["id"] == key for p in parts)
    assert all(p["end"] - p["start"] <= 6000 for p in parts)


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("tomorrow", "2026-10-16"),
        ("i morgen", "2026-10-16"),
        ("2026-10-20", "2026-10-20"),
        ("next Friday", None),
        ("soon", None),
        ("end of day", None),
    ],
)
def test_conservative_dates(phrase, expected):
    row = {"started_at": stamp(AT), "ended_at": stamp(AT + timedelta(seconds=30))}
    deadline = resolve_deadline(phrase, row, "Europe/Copenhagen")
    assert deadline["day"] == expected
    assert deadline["instant"] is None


def test_midnight_and_invalid_manual_deadlines():
    row = {"started_at": "2026-10-15T21:59:59+00:00", "ended_at": "2026-10-15T22:00:01+00:00"}
    assert resolve_deadline("tomorrow", row, "Europe/Copenhagen")["precision"] == "unknown"
    with pytest.raises(ValidationError):
        Deadline(precision="datetime", instant="2026-10-25T02:30:00", timezone="Europe/Copenhagen")
    with pytest.raises(ValidationError):
        Deadline(precision="date", day="2026-10-20", timezone="not/a/timezone")


def test_shared_http_contract_and_validation(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        archive = client.app.state.memory.store
        key = source(archive)
        run(archive, [proposal(key)])
        page = client.get("/v1/inbox").json()
        assert page["pending"] == 1
        item = page["items"][0]
        assert client.get(f"/v1/inbox/{item['id']}").json()["evidence"]
        data = {
            "operation_id": str(uuid4()),
            "expected_version": item["version"],
            "changes": {"title": "Edited"},
            "status": "completed",
        }
        assert client.patch(f"/v1/inbox/{item['id']}", json=data).status_code == 200
        data["operation_id"] = str(uuid4())
        assert client.patch(f"/v1/inbox/{item['id']}", json=data).status_code == 409
        assert client.get("/v1/inbox?status=invalid").status_code == 422
        assert client.get("/v1/inbox?due=overdue").json()["items"] == []
        assert (
            client.post(
                "/v1/inbox/extractions", json={"start_day": "2026-01-01", "end_day": "2026-03-01"}
            ).status_code
            == 422
        )
        result = client.post(
            "/v1/inbox/extractions", json={"start_day": "2026-10-15", "end_day": "2026-10-15"}
        ).json()
        assert result["total"] == 1
        assert (
            client.get("/v1/inbox/extractions/" + result["job_ids"][0]).json()["state"] == "queued"
        )
        assert client.delete("/v1/audio/" + key).status_code == 200
        assert client.get(f"/v1/inbox/{item['id']}").status_code == 404


def test_model_configuration_is_part_of_extraction_identity(archive):
    source(archive)
    jobs = InboxJobs(archive)
    first = jobs.discover("UTC", AT - timedelta(days=1), AT + timedelta(days=1), model="local-v1")
    second = jobs.discover("UTC", AT - timedelta(days=1), AT + timedelta(days=1), model="local-v2")
    assert first != second
    with archive.connect() as db:
        assert (
            db.execute("SELECT model FROM inbox_jobs WHERE id=?", (first[0],)).fetchone()[0]
            == "local-v1"
        )


def test_truncated_extraction_splits_and_never_publishes_partial_output(archive):
    from omi_local.inference import InferenceTruncated

    source(archive, "word " * 800)
    jobs = InboxJobs(archive)
    keys = jobs.discover("UTC")
    assert asyncio.run(jobs.process_one(Model(InferenceTruncated("truncated"))))
    assert jobs.status(keys[0])["state"] == "split"
    assert listing(archive)["items"] == []
    while asyncio.run(jobs.process_one(Model([]))):
        pass
    assert listing(archive)["coverage"]["jobs"]["done"] == 2


def test_backup_restore_preserves_inbox_edits_events_and_jobs(archive, tmp_path):
    import importlib.util
    import shutil
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "archive_cli", Path(__file__).parents[1] / "scripts/archive.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    key = source(archive)
    run(archive, [proposal(key)])
    item = mutate(
        archive,
        listing(archive)["items"][0],
        status="completed",
        changes={"title": "Keep manual title", "owner": None, "user_note": "Keep personal context"},
    )
    source(archive, "Pending extraction source", AT + timedelta(days=1))
    InboxJobs(archive).discover("UTC")
    snapshot = archive.export_archive()
    destination = tmp_path / "restored"
    try:
        cli.restore(snapshot, destination, 10_000_000)
        restored = Store(destination)
        assert InboxStore(restored).item(item["id"]) == item
        assert listing(restored)["coverage"]["jobs"]["queued"] == 1
        assert restored.get_audio(key)["path"].startswith(str(destination))
        restored.correct_transcript(key, "Correction after restore")
        assert InboxStore(restored).item(item["id"])["health"] == "needs_review"
        assert InboxStore(restored).item(item["id"])["user_note"] == "Keep personal context"
    finally:
        shutil.rmtree(snapshot.parent)


def test_equal_actions_at_different_offsets_in_one_source_remain_distinct(archive):
    text = "Send the invoice. " + ("Other discussion. " * 30) + "Send the invoice."
    key = source(archive, text)
    first = proposal(key, text, "Send invoice")
    first["evidence"] = [span(key, text, "Send the invoice.")]
    second = proposal(key, text, "Send invoice")
    start = text.rindex("Send the invoice.")
    second["evidence"] = [
        {"source_id": key, "start": start, "end": len(text), "quote": "Send the invoice."}
    ]
    run(archive, [first, second])
    assert len(listing(archive)["items"]) == 2


def test_timezone_change_does_not_rewrite_a_reviewed_deadline(archive):
    key = source(archive)
    candidate = proposal(key)
    candidate.update(deadline_phrase="tomorrow", deadline_evidence=span(key, TEXT, "tomorrow"))
    run(archive, [candidate])
    item = mutate(
        archive,
        listing(archive)["items"][0],
        changes={
            "deadline": {"precision": "date", "day": "2026-10-25", "timezone": "Europe/Copenhagen"}
        },
    )
    archive.correct_transcript(key, TEXT + " Clarification.")
    jobs = InboxJobs(archive)
    jobs.discover("America/New_York")
    updated = proposal(key, TEXT + " Clarification.")
    updated.update(
        deadline_phrase="tomorrow",
        deadline_evidence=span(key, TEXT + " Clarification.", "tomorrow"),
    )
    while asyncio.run(jobs.process_one(Model([updated]))):
        pass
    result = InboxStore(archive).item(item["id"])
    assert result["deadline"]["day"] == "2026-10-25"
    assert result["deadline"]["timezone"] == "Europe/Copenhagen"


def test_restarted_job_cannot_claim_a_different_model_route(archive):
    from types import SimpleNamespace

    source(archive)
    jobs = InboxJobs(archive)
    keys = jobs.discover("UTC", model="old-route")
    model = Model([])
    model.settings = SimpleNamespace(llm_model="new-route")
    assert asyncio.run(jobs.process_one(model))
    assert jobs.status(keys[0])["state"] == "failed"
    assert "configuration changed" in jobs.status(keys[0])["error"]
