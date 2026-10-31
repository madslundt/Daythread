"""Saved question groups migrate without leaking changed or removed speech."""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_generation_jobs import jobs as jobs

from omi_local.domain import stamp
from omi_local.generation_jobs import GenerationJobs, GenerationRequest
from omi_local.inference_errors import InferenceDisabled, InferenceUnavailable
from omi_local.question_history import QuestionHistory
from omi_local.service import Memory, public_source
from omi_local.settings import Settings
from omi_local.store import Store

END = datetime(2026, 10, 31, 12, tzinfo=UTC)


def saved_job(jobs, *, end=END, question="Latest question?", count=3, questions=None):
    source_id = str(uuid4())
    jobs.store.ingest(source_id, "omi-test", end - timedelta(seconds=30), b"audio", 30)
    jobs.store.complete(source_id, "First question? Second question? Latest question?")
    source = public_source(jobs.store.get_audio(source_id))
    job = jobs.enqueue(GenerationRequest(id=uuid4(), kind="wearable_action", parameters={
        "action": "answer", "end": stamp(end), "duration_seconds": 30,
    }))
    result = {
        "action": "answer", "sources": [source], "start": source["started_at"],
        "end": stamp(end), "duration_seconds": 30,
        "question_count": count, "question": question, "answer": "Latest answer.",
        "text": (question + "\n" if question else "") + "Latest answer.",
    }
    if questions is not None:
        result["questions"] = questions
    jobs.complete(job, result)
    return jobs.get(job["id"])


async def forbidden(job):
    raise AssertionError("Saved questions must not require inference")


def recovered(job):
    return [{
        "question": f"Question {index + 1}?", "answer": "Last answer" if index == 2 else None,
        "asked_at": job["parameters"]["end"], "time_precision": "request",
    } for index in range(3)]


def test_day_page_flattens_groups_and_highlights_every_clicked_question(jobs):
    jobs.memory.settings.timezone = "Europe/Copenhagen"
    prior = saved_job(jobs, end=datetime(2026, 10, 31, 21, tzinfo=UTC), questions=[{
        "question": "Earlier question?", "answer": None,
        "asked_at": "2026-10-31T21:00:00+00:00", "time_precision": "request",
    }])
    selected = saved_job(jobs, end=datetime(2026, 10, 31, 22, 59, tzinfo=UTC), questions=[{
        "question": f"Selected {i}?", "answer": "4" if i == 3 else None,
        "asked_at": f"2026-10-31T22:58:{i:02}+00:00", "time_precision": "request",
    } for i in (1, 2, 3)])
    saved_job(jobs, end=datetime(2026, 10, 31, 23, tzinfo=UTC))  # Next local day.
    history = QuestionHistory(jobs.memory, forbidden)
    page = history.page(selected["id"])
    assert page["day"] == "2026-10-31"
    assert page["timezone"] == "Europe/Copenhagen"
    assert page["selected_job_id"] == selected["id"]
    assert len(page["selected_question_ids"]) == 3
    assert [q["question"] for q in page["questions"]] == [
        "Selected 3?", "Selected 2?", "Selected 1?", "Earlier question?",
    ]
    assert page["questions"][-1]["job_id"] == prior["id"]
    assert page["questions"][0]["answer"] == "4"
    assert not page["processing"] and page["error"] is None
    assert history.page(selected["id"]) == page
    assert not asyncio.run(history.process_one())


def test_legacy_selected_and_previous_groups_backfill_without_new_notification(jobs):
    previous = saved_job(jobs, end=END - timedelta(hours=1))
    selected = saved_job(jobs)
    jobs.notify_completed()
    notices = jobs.store.notifications_after(0)["notifications"]
    calls = []

    async def extract(job):
        calls.append(job["id"])
        return recovered(job)

    history = QuestionHistory(jobs.memory, extract)
    partial = history.page(selected["id"])
    assert partial["processing"] and partial["error"] is None
    assert len(partial["questions"]) == 2
    assert len(partial["selected_question_ids"]) == 1
    assert all(q["time_precision"] == "request" for q in partial["questions"])
    assert asyncio.run(history.process_one())
    assert asyncio.run(history.process_one())
    assert not asyncio.run(history.process_one())
    page = history.page(selected["id"])
    assert not page["processing"] and len(page["questions"]) == 6
    assert len(page["selected_question_ids"]) == 3
    assert set(calls) == {selected["id"], previous["id"]}
    assert len({q["id"] for q in page["questions"]}) == 6
    assert [q["question"] for q in page["questions"]] == [
        "Question 3?", "Question 2?", "Question 1?", "Question 3?", "Question 2?", "Question 1?",
    ]
    for original in (selected, previous):
        saved = jobs.get(original["id"])
        assert saved["notified"] == 1
        assert {k: v for k, v in saved["result"].items() if k != "questions"} == original["result"]
    jobs.notify_completed()
    assert jobs.store.notifications_after(0)["notifications"] == notices


def test_shared_source_question_ids_merge_answers_and_keep_selected_highlights(jobs):
    shared = str(uuid4())
    prior = saved_job(jobs, end=END - timedelta(hours=1), questions=[{
        "id": shared, "question": "Repeated question?", "answer": "Known answer",
        "asked_at": stamp(END - timedelta(hours=1)), "time_precision": "request",
    }])
    selected = saved_job(jobs, questions=[{
        "id": shared, "question": "Repeated question?", "answer": None,
        "asked_at": stamp(END), "time_precision": "request",
    }])
    history = QuestionHistory(jobs.memory, forbidden)
    page = history.page(selected["id"])
    assert page["selected_question_ids"] == [shared]
    assert len(page["questions"]) == 1
    assert page["questions"][0]["id"] == shared
    assert page["questions"][0]["answer"] == "Known answer"
    assert page["questions"][0]["job_id"] == selected["id"]
    assert history.page(prior["id"])["selected_question_ids"] == [shared]


def test_backfill_preserves_extractor_question_ids(jobs):
    selected = saved_job(jobs)
    identities = [str(uuid4()) for _ in range(3)]

    async def extract(job):
        return [item | {"id": identity} for item, identity in zip(recovered(job), identities)]

    history = QuestionHistory(jobs.memory, extract)
    history.page(selected["id"])
    asyncio.run(history.process_one())
    assert [q["id"] for q in jobs.get(selected["id"])["result"]["questions"]] == identities


@pytest.mark.parametrize("explicit_position", [False, True])
def test_coarse_question_times_show_latest_first_without_promoting_selected_group(
    jobs, explicit_position,
):
    records = [{"question": f"Question {i}?", "answer": None, "asked_at": stamp(END),
                "time_precision": "request", **({"position": i} if explicit_position else {})}
               for i in (1, 2, 3)]
    selected = saved_job(jobs, questions=records)
    newest = saved_job(jobs, end=END + timedelta(minutes=1), questions=[{
        "question": "Newer session?", "answer": "Yes",
        "asked_at": stamp(END + timedelta(minutes=1)),
        "time_precision": "request",
    }])
    history = QuestionHistory(jobs.memory, forbidden)
    page = history.page(selected["id"])
    assert [q["question"] for q in page["questions"]] == [
        "Newer session?", "Question 3?", "Question 2?", "Question 1?",
    ]
    assert page["questions"][0]["job_id"] == newest["id"]
    assert page["questions"][0]["id"] not in page["selected_question_ids"]


@pytest.mark.parametrize("mutation", ["delete", "text", "source_version", "segments"])
def test_legacy_missing_provenance_still_rejects_changed_sources(jobs, mutation):
    previous = saved_job(jobs, end=END - timedelta(hours=1))
    selected = saved_job(jobs)
    history = QuestionHistory(jobs.memory, forbidden)
    with jobs.store.connect() as db:
        db.execute("DELETE FROM generation_job_sources")  # Pre-provenance archive.
        for job in (previous, selected):
            key = job["result"]["sources"][0]["id"]
            if mutation == "delete":
                db.execute("DELETE FROM audio WHERE id=?", (key,))
            elif mutation == "text":
                db.execute("UPDATE audio SET text='Corrected speech' WHERE id=?", (key,))
            elif mutation == "source_version":
                db.execute("UPDATE audio SET source_version='replacement' WHERE id=?", (key,))
            else:
                db.execute("UPDATE audio SET segments=? WHERE id=?",
                           (json.dumps([{"text": "Changed"}]), key))
    with pytest.raises(HTTPException) as error:
        history.page(selected["id"])
    assert error.value.status_code == 409
    fresh = saved_job(jobs, end=END + timedelta(hours=1), questions=[])
    assert history.page(fresh["id"])["questions"] == []


def test_source_changes_during_extraction_cannot_resurrect_deleted_result(jobs):
    selected = saved_job(jobs)

    async def extract(job):
        with jobs.store.connect() as db:
            db.execute("DELETE FROM audio WHERE id=?", (job["result"]["sources"][0]["id"],))
        return recovered(job)

    history = QuestionHistory(jobs.memory, extract)
    history.page(selected["id"])
    assert asyncio.run(history.process_one())
    assert jobs.get(selected["id"])["result"] is None
    with jobs.store.connect() as db:
        assert not db.execute("SELECT * FROM question_history_requests").fetchall()


def test_result_replaced_during_extraction_is_not_overwritten(jobs):
    selected = saved_job(jobs)

    async def extract(job):
        replacement = job["result"] | {"text": "Replacement answer"}
        with jobs.store.connect() as db:
            db.execute("UPDATE generation_jobs SET result=? WHERE id=?",
                       (json.dumps(replacement), job["id"]))
        return recovered(job)

    history = QuestionHistory(jobs.memory, extract)
    history.page(selected["id"])
    asyncio.run(history.process_one())
    result = jobs.get(selected["id"])["result"]
    assert result["text"] == "Replacement answer" and "questions" not in result


def test_gateway_busy_retries_durably_and_never_exposes_provider_details(jobs):
    selected = saved_job(jobs)

    async def busy(job):
        raise InferenceUnavailable("private provider detail")

    history = QuestionHistory(jobs.memory, busy)
    history.page(selected["id"])
    assert asyncio.run(history.process_one())
    page = history.page(selected["id"])
    assert page["processing"] and page["error"] is None
    assert not asyncio.run(history.process_one())
    with jobs.store.connect() as db:
        assert db.execute("SELECT retry_at FROM question_history_requests").fetchone()[0]
        db.execute("UPDATE question_history_requests SET retry_at=''")

    async def extract(job):
        return recovered(job)

    restarted = QuestionHistory(jobs.memory, extract)
    assert asyncio.run(restarted.process_one())
    assert len(restarted.page(selected["id"])["questions"]) == 3


def test_disabled_inference_has_explicit_status_and_resumes_pending_work(jobs):
    selected = saved_job(jobs)

    async def extract(job):
        return recovered(job)

    history = QuestionHistory(jobs.memory, extract)
    jobs.memory.settings.llm_enabled = False
    page = history.page(selected["id"])
    assert not page["processing"] and page["error"] == "Language model processing is off."
    assert not asyncio.run(history.process_one())
    jobs.memory.settings.llm_enabled = True
    assert asyncio.run(history.process_one())
    assert history.page(selected["id"])["error"] is None


def test_disabled_provider_response_and_failures_keep_original_partial(jobs):
    selected = saved_job(jobs)

    async def disabled(job):
        raise InferenceDisabled("private policy body")

    history = QuestionHistory(jobs.memory, disabled)
    history.page(selected["id"])
    asyncio.run(history.process_one())
    assert history.page(selected["id"])["error"] == "Language model processing is off."
    with jobs.store.connect() as db:
        db.execute("UPDATE question_history_requests SET retry_at=''")

    async def invalid(job):
        return [{"question": "Must not escape", "source_id": str(uuid4())}]

    history.extract = invalid
    asyncio.run(history.process_one())
    page = history.page(selected["id"])
    assert not page["processing"] and "Generation could not finish" in page["error"]
    assert [q["question"] for q in page["questions"]] == ["Latest question?"]


def test_failed_history_retries_only_on_explicit_request_for_the_selected_day(jobs):
    selected = saved_job(jobs)
    previous = saved_job(jobs, end=END - timedelta(hours=1))
    other_day = saved_job(jobs, end=END - timedelta(days=1))

    async def extract(job):
        return recovered(job)

    history = QuestionHistory(jobs.memory, extract)
    history.page(selected["id"])
    history.page(other_day["id"])
    with jobs.store.connect() as db:
        db.execute("UPDATE question_history_requests SET state='failed',error='Failure',"
                   "retry_at='2099-01-01T00:00:00+00:00'")
    page = history.page(selected["id"])
    assert not page["processing"] and page["error"] == "Failure"
    assert not asyncio.run(history.process_one())
    page = history.page(selected["id"], retry=True)
    assert page["processing"] and page["error"] is None
    with jobs.store.connect() as db:
        states = {row["job_id"]: dict(row) for row in db.execute(
            "SELECT * FROM question_history_requests",
        ).fetchall()}
    for identity in (selected["id"], previous["id"]):
        assert states[identity]["state"] == "queued"
        assert states[identity]["error"] is None and states[identity]["retry_at"] == ""
    assert states[other_day["id"]]["state"] == "failed"
    assert states[other_day["id"]]["retry_at"].startswith("2099")
    assert asyncio.run(history.process_one())
    assert asyncio.run(history.process_one())
    assert not asyncio.run(history.process_one())
    page = history.page(selected["id"])
    assert not page["processing"] and page["error"] is None
    assert len(page["questions"]) == 6


def test_explicit_retry_does_not_restart_running_history_work(jobs):
    selected = saved_job(jobs)
    history = QuestionHistory(jobs.memory, forbidden)
    history.page(selected["id"])
    with jobs.store.connect() as db:
        db.execute("UPDATE question_history_requests SET state='running',"
                   "retry_at='2099-01-01T00:00:00+00:00'")
    assert history.page(selected["id"], retry=True)["processing"]
    with jobs.store.connect() as db:
        row = db.execute("SELECT * FROM question_history_requests").fetchone()
    assert row["state"] == "running" and row["retry_at"].startswith("2099")


def test_cancellation_and_restart_recover_without_repeating_success(jobs):
    selected = saved_job(jobs)

    async def extract(job):
        raise asyncio.CancelledError()

    history = QuestionHistory(jobs.memory, extract)
    history.page(selected["id"])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(history.process_one())
    assert history.page(selected["id"])["processing"]
    with jobs.store.connect() as db:
        db.execute("UPDATE question_history_requests SET state='running'")

    async def recover(job):
        return recovered(job)

    restarted = QuestionHistory(jobs.memory, recover)
    assert asyncio.run(restarted.process_one())
    assert not asyncio.run(restarted.process_one())


def test_profile_stores_cannot_read_each_others_questions(jobs, tmp_path):
    selected = saved_job(jobs)
    settings = Settings(data_dir=tmp_path / "other", worker_enabled=False, _env_file=None)
    memory = Memory(Store(settings.data_dir), settings, SimpleNamespace())
    GenerationJobs(memory)
    history = QuestionHistory(memory, forbidden)
    with pytest.raises(HTTPException) as error:
        history.page(selected["id"])
    assert error.value.status_code == 404


def test_no_questions_and_source_anchors_need_no_model_work(jobs):
    empty = saved_job(jobs, question=None, count=0)
    history = QuestionHistory(jobs.memory, forbidden)
    assert history.page(empty["id"])["questions"] == []
    assert not history.page(empty["id"])["processing"]
    selected = saved_job(jobs, questions=[{
        "question": "First question?", "answer": None, "asked_at": stamp(END),
        "time_precision": "request",
    }])
    with jobs.store.connect() as db:
        result = selected["result"]
        result["questions"][0].update(source_id=result["sources"][0]["id"], start_offset=0,
                                       end_offset=15, time_precision="segment")
        db.execute("UPDATE generation_jobs SET result=? WHERE id=?",
                   (json.dumps(result), selected["id"]))
    question = history.page(selected["id"])["questions"][0]
    assert question["start_offset"] == 0 and question["end_offset"] == 15
    assert question["time_precision"] == "segment"
    assert not asyncio.run(history.process_one())


def test_new_wearable_job_preempts_backfill_and_preserves_its_durable_retry(jobs):
    selected = saved_job(jobs)
    history = jobs.question_history
    history.page(selected["id"])

    async def run():
        started, answered = asyncio.Event(), asyncio.Event()
        order = []

        async def backfill(job):
            order.append("history started")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                order.append("history cancelled")

        async def execute(job):
            with jobs.store.connect() as db:
                state = db.execute("SELECT state FROM question_history_requests WHERE job_id=?",
                                   (selected["id"],)).fetchone()[0]
            assert state == "queued"
            order.append("live answer")
            answered.set()
            return {"action": "answer", "text": "4", "sources": [], "question_count": 0}

        history.extract = backfill
        jobs.execute = execute
        worker = asyncio.create_task(jobs.run())
        try:
            await asyncio.wait_for(started.wait(), 2)
            live = jobs.enqueue(GenerationRequest(
                id=uuid4(), kind="wearable_action",
                parameters={"action": "answer", "end": stamp(END)},
            ))
            await asyncio.wait_for(answered.wait(), 2)
            assert jobs.get(live["id"])["state"] == "completed"
            assert order[:3] == ["history started", "history cancelled", "live answer"]
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        with jobs.store.connect() as db:
            assert db.execute("SELECT state FROM question_history_requests WHERE job_id=?",
                              (selected["id"],)).fetchone()[0] == "queued"

        async def recover(job):
            return recovered(job)

        history.extract = recover
        assert await history.process_one()
        assert len(history.page(selected["id"])["questions"]) == 3

    asyncio.run(run())
