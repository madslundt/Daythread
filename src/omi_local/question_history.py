"""Day-scoped question history derived from valid saved wearable answer jobs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .domain import EvidenceChanged, stamp, utc_now
from .inference_errors import InferenceDisabled, InferenceUnavailable

if TYPE_CHECKING:
    from .service import Memory

QuestionExtractor = Callable[[dict], Awaitable[list[dict]]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS question_history_requests (
 job_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'queued',
 retry_at TEXT NOT NULL DEFAULT '', error TEXT,
 created_at TEXT NOT NULL
);
"""


def _answer_job(row: Any) -> dict | None:
    if row is None or row["kind"] != "wearable_action":
        return None
    job = dict(row)
    job["parameters"] = json.loads(job["parameters"])
    if job["parameters"].get("action") != "answer":
        return None
    job["result"] = json.loads(job["result"]) if job["result"] else None
    return job


def _current_sources(db: Any, job: dict) -> dict[str, dict]:
    """Validate saved originals even when a legacy job lacks trigger provenance."""
    sources = {}
    for source in job["result"].get("sources", []):
        row = db.execute("SELECT * FROM audio WHERE id=?", (source["id"],)).fetchone()
        if row is None:
            raise EvidenceChanged("Recorded evidence changed; generate again")
        current = dict(row)
        for field in ("text", "state", "transcript_version", "source_version",
                      "started_at", "ended_at"):
            if field in source and current[field] != source[field]:
                raise EvidenceChanged("Recorded evidence changed; generate again")
        if "segments" in source and json.loads(current["segments"]) != source["segments"]:
            raise EvidenceChanged("Recorded evidence changed; generate again")
        sources[source["id"]] = {field: current[field] for field in (
            "text", "state", "transcript_version", "source_version", "segments",
            "started_at", "ended_at",
        )}
    if not sources and (job["result"].get("questions") or job["result"].get("question")):
        raise EvidenceChanged("Recorded evidence changed; generate again")
    return sources


def _questions(job: dict, values: list[dict]) -> list[dict]:
    sources = {source["id"]: source for source in job["result"].get("sources", [])}
    end = job["parameters"]["end"]
    result = []
    for index, value in enumerate(values):
        question = value.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError
        source_id = value.get("source_id")
        if source_id and source_id not in sources:
            raise ValueError
        asked_at = datetime.fromisoformat(value.get("asked_at") or end)
        if asked_at.tzinfo is None:
            raise ValueError
        precision = value.get("time_precision", "request")
        if precision not in {"segment", "recording", "request"}:
            raise ValueError
        if precision != "request" and not source_id:
            raise ValueError
        answer = value.get("answer")
        if answer is not None and not isinstance(answer, str):
            raise ValueError
        identity = value.get("id") or str(uuid5(
            NAMESPACE_URL, f"daythread:question:{job['id']}:{index}:"
            f"{source_id or ''}:{value.get('start_offset')}:{question}",
        ))
        if not isinstance(identity, str) or len(identity) > 200:
            raise ValueError
        position = value.get("position", index)
        if type(position) is not int or position < 0:
            raise ValueError
        item = {
            "id": identity,
            "job_id": job["id"], "question": question.strip(), "answer": answer,
            "asked_at": stamp(asked_at), "time_precision": precision,
            "position": position,
        }
        if source_id:
            item["source_id"] = source_id
        left, right = value.get("start_offset"), value.get("end_offset")
        if left is not None or right is not None:
            if (not source_id or type(left) is not int or type(right) is not int
                    or not 0 <= left < right <= len(sources[source_id]["text"])):
                raise ValueError
            item.update(start_offset=left, end_offset=right)
        result.append(item)
    return result


def _saved_questions(job: dict) -> list[dict]:
    result = job["result"]
    if isinstance(result.get("questions"), list):
        return _questions(job, result["questions"])
    # Older notifications saved only the latest question. Keep it visible while
    # the full capture window is recovered asynchronously.
    if result.get("question"):
        return _questions(job, [{"question": result["question"], "answer": result.get("answer"),
                                 "asked_at": job["parameters"]["end"],
                                 "time_precision": "request"}])
    return []


class QuestionHistory:
    def __init__(self, memory: Memory, extract: QuestionExtractor) -> None:
        self.memory = memory
        self.store = memory.store
        self.extract = extract
        with self.store.connect() as db:
            db.executescript(SCHEMA)
            db.execute("UPDATE question_history_requests SET state='queued' WHERE state='running'")

    def page(self, job_id: str, retry: bool = False) -> dict:
        zone = ZoneInfo(self.memory.settings.timezone)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            selected = _answer_job(db.execute(
                "SELECT * FROM generation_jobs WHERE id=?", (job_id,),
            ).fetchone())
            if selected is None:
                raise HTTPException(404, "Generation job not found")
            if selected["state"] != "completed" or not selected["result"]:
                raise HTTPException(409, "Recorded evidence changed; generate again")
            try:
                _current_sources(db, selected)
            except EvidenceChanged as exc:
                raise HTTPException(409, "Recorded evidence changed; generate again") from exc
            day = datetime.fromisoformat(selected["parameters"]["end"]).astimezone(zone).date()
            jobs = []
            for row in db.execute(
                "SELECT * FROM generation_jobs WHERE kind='wearable_action' "
                "AND state='completed' AND result IS NOT NULL ORDER BY created_at DESC,id",
            ).fetchall():
                job = _answer_job(row)
                if (job is None or datetime.fromisoformat(job["parameters"]["end"])
                        .astimezone(zone).date() != day):
                    continue
                try:
                    _current_sources(db, job)
                except EvidenceChanged:
                    continue
                jobs.append(job)
                if (not isinstance(job["result"].get("questions"), list)
                        and job["result"].get("question_count") != 0):
                    db.execute(
                        "INSERT OR IGNORE INTO question_history_requests(job_id,created_at) "
                        "VALUES(?,?)", (job["id"], stamp(utc_now())),
                    )
                    if retry:
                        db.execute(
                            "UPDATE question_history_requests SET state='queued',error=NULL,"
                            "retry_at='' WHERE job_id=? AND state='failed'", (job["id"],),
                        )
            selected_ids = [item["id"] for item in _saved_questions(selected)]
            grouped: dict[str, dict] = {}
            for job in jobs:
                for item in _saved_questions(job):
                    previous = grouped.get(item["id"])
                    if previous is None:
                        grouped[item["id"]] = item
                    else:
                        # Shared source spans identify one question across overlapping
                        # clicks. Retain its answer and the selected group's identity.
                        answer = (item["answer"] if item["job_id"] == job_id
                                  and item["answer"] is not None else previous["answer"])
                        if answer is None:
                            answer = item["answer"]
                        representative = item if item["job_id"] == job_id else previous
                        grouped[item["id"]] = representative | {"answer": answer}
            questions = list(grouped.values())
            states = [dict(row) for job in jobs if (row := db.execute(
                "SELECT state,error FROM question_history_requests WHERE job_id=?", (job["id"],),
            ).fetchone()) is not None]
        questions.sort(
            key=lambda item: (item["asked_at"], item["position"], item.get("start_offset", -1),
                              item["job_id"], item["id"]), reverse=True,
        )
        pending = any(row["state"] in {"queued", "running"} for row in states)
        error = next((row["error"] for row in states if row["error"]), None)
        if pending and not self.memory.settings.llm_enabled:
            pending, error = False, "Language model processing is off."
        return {
            "day": day.isoformat(), "timezone": str(zone), "selected_job_id": job_id,
            "selected_question_ids": list(dict.fromkeys(selected_ids)),
            "questions": questions, "processing": pending, "error": error,
        }

    async def process_one(self) -> bool:
        if not self.memory.settings.llm_enabled:
            return False
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            request = db.execute(
                "SELECT job_id FROM question_history_requests WHERE state='queued' "
                "AND retry_at<=? ORDER BY created_at,job_id LIMIT 1", (stamp(utc_now()),),
            ).fetchone()
            if request is None:
                return False
            job_id = request["job_id"]
            db.execute("UPDATE question_history_requests SET state='running',error=NULL "
                       "WHERE job_id=?", (job_id,))
            row = db.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
            job = _answer_job(row)
            try:
                if job is None or job["state"] != "completed" or not job["result"]:
                    raise EvidenceChanged("Recorded evidence changed; generate again")
                sources = _current_sources(db, job)
                snapshot = (row["result"], row["parameters"], sources)
            except EvidenceChanged:
                db.execute("DELETE FROM question_history_requests WHERE job_id=?", (job_id,))
                return True
        try:
            if isinstance(job["result"].get("questions"), list):
                values = job["result"]["questions"]
            elif job["result"].get("question_count") == 0:
                values = []
            else:
                async with asyncio.timeout(self.memory.settings.inference_timeout_seconds):
                    values = await self.extract(job)
            questions = _questions(job, values)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
                current = _answer_job(row)
                if (current is None or current["state"] != "completed" or not current["result"]
                        or row["result"] != snapshot[0] or row["parameters"] != snapshot[1]
                        or _current_sources(db, current) != snapshot[2]):
                    raise EvidenceChanged("Recorded evidence changed; generate again")
                result = current["result"] | {"questions": questions}
                db.execute("UPDATE generation_jobs SET result=? WHERE id=?",
                           (json.dumps(result), job_id))
                db.execute("UPDATE question_history_requests SET state='completed',error=NULL "
                           "WHERE job_id=?", (job_id,))
        except InferenceUnavailable as exc:
            self._retry(job_id, exc.retry_after_seconds)
        except InferenceDisabled:
            self._retry(job_id, 30, "Language model processing is off.")
        except asyncio.CancelledError:
            self._retry(job_id, 0)
            raise
        except EvidenceChanged:
            with self.store.connect() as db:
                db.execute("DELETE FROM question_history_requests WHERE job_id=?", (job_id,))
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE question_history_requests SET state='failed',error=? "
                           "WHERE job_id=?", (
                               "Generation could not finish. Check home-core and try again.",
                               job_id,
                           ))
        return True

    def _retry(self, job_id: str, seconds: int, error: str | None = None) -> None:
        with self.store.connect() as db:
            db.execute("UPDATE question_history_requests SET state='queued',retry_at=?,error=? "
                       "WHERE job_id=?", (
                           stamp(utc_now() + timedelta(seconds=seconds)), error, job_id,
                       ))
