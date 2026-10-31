"""Durable generation requests survive the phone closing and backend restarts."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from .completion_events import generating_job, job_notice
from .domain import EvidenceChanged, stamp, utc_now
from .inference_errors import InferenceDisabled, InferenceUnavailable
from .recording_topics import topic_fingerprint
from .scoped_recap_jobs import fingerprint, invalidate_changed, scoped, validate_result
from .summary_translation import generate_summary_translation
from .topic_context import topic_contexts
from .translation import generate_translation
from .wearable_actions import (
    RecentAudioPending,
    WearableParameters,
    generate_wearable_action,
    translation_target,
)

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS generation_jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, parameters TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', result TEXT, error TEXT,
 created_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0,
 retry_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS generation_job_sources (
 job_id TEXT NOT NULL, source_id TEXT NOT NULL,
 PRIMARY KEY(job_id, source_id)
);
CREATE TRIGGER IF NOT EXISTS generation_source_delete AFTER DELETE ON audio BEGIN
 UPDATE generation_jobs SET result=NULL, state='failed',
 error='Recorded evidence was removed; generate again', notified=1
 WHERE id IN (SELECT job_id FROM generation_job_sources WHERE source_id=OLD.id);
 DELETE FROM generation_job_sources WHERE source_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS generation_source_change
AFTER UPDATE OF text,state,transcript_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version BEGIN
 UPDATE generation_jobs SET result=NULL, state='failed',
 error='Recorded evidence changed; generate again', notified=1
 WHERE id IN (SELECT job_id FROM generation_job_sources WHERE source_id=OLD.id);
 DELETE FROM generation_job_sources WHERE source_id=OLD.id;
END;
"""


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RewindParameters(Parameters):
    minutes: int = Field(default=10, ge=1, le=60)
    end: AwareDatetime | None = None
    summarize: bool = True


class RecapParameters(Parameters):
    day: date
    tag_id: UUID | None = None
    zone_id: UUID | None = None


class TranslationParameters(Parameters):
    source_id: UUID
    language: str | None = Field(default=None, max_length=35)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=1)
    transcript_version: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def passage(self):
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("Supply both passage offsets")
        if self.start_offset is not None:
            if self.end_offset <= self.start_offset or not self.transcript_version:
                raise ValueError("Supply a valid passage and its transcript version")
        return self


class SummaryTranslationParameters(Parameters):
    output_id: UUID
    language: str | None = Field(default=None, max_length=35)


class MeetingParameters(Parameters):
    meeting_id: UUID


class GenerationRequest(Parameters):
    id: UUID
    kind: Literal[
        "rewind", "recap", "translation", "summary_translation", "meeting_recap", "wearable_action",
    ]
    parameters: dict


PARAMETERS = {
    "rewind": RewindParameters, "recap": RecapParameters,
    "translation": TranslationParameters, "summary_translation": SummaryTranslationParameters,
    "meeting_recap": MeetingParameters,
    "wearable_action": WearableParameters,
}
RAW_REWIND = (
    "(kind='rewind' AND CAST(json_extract(parameters,'$.summarize') AS TEXT) IN ('0','false'))"
)


class GenerationJobs:
    def __init__(self, memory):
        self.memory = memory
        self.store = memory.store
        with self.store.connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(generation_jobs)")}
            if "retry_at" not in columns:
                db.execute(
                    "ALTER TABLE generation_jobs ADD COLUMN retry_at TEXT NOT NULL DEFAULT ''"
                )
            if memory.settings.background_processing_enabled:
                db.execute("UPDATE generation_jobs SET state='queued' WHERE state='running'")

    def get(self, job_id: str) -> dict:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
            if row is not None:
                row = invalidate_changed(self.memory, row, db)
        if row is None:
            raise HTTPException(404, "Generation job not found")
        return dict(row) | {
            "parameters": json.loads(row["parameters"]),
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def observation(self) -> dict:
        """Counts of generation tasks, separate from audio chunks awaiting STT."""
        stages: dict[str, int] = {}
        queued = running = paused = 0
        with self.store.connect() as db:
            rows = db.execute(
                f"SELECT kind,state,CASE WHEN {RAW_REWIND} THEN 0 ELSE 1 END AS uses_llm,"
                "count(*) AS n FROM generation_jobs WHERE state IN ('queued','running') "
                "GROUP BY kind,state,uses_llm"
            ).fetchall()
            fingerprint = topic_fingerprint(self.memory)
            topics = sum(context.ready and context.pending(fingerprint)
                         for context in topic_contexts(db))
        for row in rows:
            if row["uses_llm"] and not self.memory.settings.llm_enabled:
                paused += row["n"]
                continue
            if row["state"] == "queued":
                queued += row["n"]
            else:
                running += row["n"]
                if not row["uses_llm"]:
                    continue
                stage = ("translating" if row["kind"] in {"translation", "summary_translation"}
                         else "summarizing")
                stages[stage] = stages.get(stage, 0) + row["n"]
        for stage, count in self.memory.background_activity.copy().items():
            if count:
                if not self.memory.settings.llm_enabled:
                    paused += count
                    continue
                stages[stage] = stages.get(stage, 0) + count
                running += count
        topics = max(0, topics - stages.get("organizing", 0))
        if not self.memory.settings.llm_enabled:
            paused += topics
        else:
            queued += topics
        result = {"queued": queued, "running": running, "stages": stages}
        if self.memory.settings.llm_enabled and self.memory.background_progress:
            now = utc_now()
            result["active"] = [
                progress | {"elapsed_seconds": max(
                    0, (now - datetime.fromisoformat(progress["started_at"])).total_seconds(),
                )}
                for progress in self.memory.background_progress.copy().values()
            ]
        return result | {"paused": paused} if not self.memory.settings.llm_enabled else result

    def enqueue(self, body: GenerationRequest) -> dict:
        try:
            params = PARAMETERS[body.kind].model_validate(body.parameters).model_dump(mode="json")
            if body.kind == "recap":
                params = {key: value for key, value in params.items() if value is not None}
        except ValidationError as exc:
            raise HTTPException(422, "Invalid generation parameters") from exc
        if body.kind != "rewind" or params["summarize"]:
            self.memory.settings.require_inference("llm")
        job_id = str(body.id)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM generation_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if existing:
                previous = json.loads(existing["parameters"])
                if body.kind == "recap" and scoped(params):
                    params["scope_fingerprint"] = previous.get("scope_fingerprint")
                # Freeze relative rewind time at first submission, including retries.
                if body.kind == "rewind" and params["end"] is None:
                    params["end"] = previous.get("end")
                if body.kind == "wearable_action" and params["action"] == "translate":
                    if params["language"] is None:
                        params["language"] = previous.get("language")
                if existing["kind"] != body.kind or previous != params:
                    raise HTTPException(409, "Request ID already has different parameters")
            else:
                if body.kind == "recap" and scoped(params):
                    params["scope_fingerprint"] = fingerprint(self.memory, params, db)
                if body.kind == "rewind" and params["end"] is None:
                    params["end"] = stamp(utc_now())
                if body.kind == "wearable_action" and params["action"] == "translate":
                    params["language"] = translation_target(self.memory, params["language"])
                db.execute(
                    "INSERT INTO generation_jobs(id,kind,parameters,created_at,notified) "
                    "VALUES(?,?,?,?,?)",
                    (job_id, body.kind, json.dumps(params, sort_keys=True), stamp(utc_now()),
                     int(body.kind == "recap" and scoped(params))),
                )
        return self.get(job_id)

    async def execute(self, job: dict) -> dict:
        params, kind, memory = job["parameters"], job["kind"], self.memory
        if kind == "wearable_action":
            return await generate_wearable_action(memory, params)
        if kind == "rewind":
            end = datetime.fromisoformat(params["end"])
            return await memory.window(
                end - timedelta(minutes=params["minutes"]), end,
                f"a rewind of the last {params['minutes']} minutes", params["summarize"],
                kind="rewind",
            )
        if kind == "recap":
            if scoped(params):
                return await memory.recap(
                    date.fromisoformat(params["day"]), params.get("tag_id"), params.get("zone_id"),
                    expected_fingerprint=params["scope_fingerprint"],
                )
            return await memory.recap(date.fromisoformat(params["day"]))
        if kind == "summary_translation":
            return await generate_summary_translation(
                memory, UUID(params["output_id"]), params["language"],
            )
        if kind == "translation":
            return await generate_translation(
                memory, UUID(params["source_id"]), params["start_offset"], params["end_offset"],
                params["transcript_version"], params["language"],
            )
        meeting = next((m for m in memory.timeline()["meetings"]
                        if m["id"] == params["meeting_id"]), None)
        if meeting is None:
            raise HTTPException(404, "Meeting not found")
        result = await memory.window(
            datetime.fromisoformat(meeting["context_start"]),
            datetime.fromisoformat(meeting["context_end"]),
            "meeting notes with decisions and action items", kind="meeting",
        )
        return result | {"meeting": meeting}

    def complete(self, job: dict, result: dict) -> None:
        sources = result.get("sources", [])
        if job["kind"] == "translation":
            translation = result.get("translation") or {}
            sources = [{"id": job["parameters"]["source_id"],
                        "transcript_version": translation.get("transcript_version")}]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if job["kind"] == "recap" and scoped(job["parameters"]):
                validate_result(self.memory, job["parameters"], result, db)
            if job["kind"] == "summary_translation":
                source = self.store._summary_source(db, job["parameters"]["output_id"])
                if source["summary_version"] != result["summary_version"]:
                    raise EvidenceChanged("Summary changed; try again")
                sources = source["sources"]
            for source in sources:
                current = db.execute(
                    "SELECT transcript_version,source_version,text,state FROM audio WHERE id=?",
                    (source["id"],),
                ).fetchone()
                if current is None or (source.get("transcript_version") is not None
                                       and current[0] != source["transcript_version"]):
                    raise EvidenceChanged("Recorded evidence changed; generate again")
                if job["kind"] == "wearable_action" and any(
                    field in source and current[field] != source[field]
                    for field in ("source_version", "text", "state")
                ):
                    raise EvidenceChanged("Recorded evidence changed; generate again")
                db.execute("INSERT OR IGNORE INTO generation_job_sources VALUES(?,?)",
                           (job["id"], source["id"]))
            db.execute(
                "UPDATE generation_jobs SET state='completed',result=?,error=NULL WHERE id=?",
                (json.dumps(result), job["id"]),
            )

    def notify_completed(self) -> None:
        notifications = getattr(self.memory, "notifications", None)
        if notifications is None:
            return
        with self.store.connect() as db:
            pending = db.execute(
                "SELECT id FROM generation_jobs WHERE state IN ('completed','failed') "
                "AND notified=0 ORDER BY created_at LIMIT 20"
            ).fetchall()
        for row in pending:
            try:
                self.notify_one(row["id"], notifications)
            except Exception as exc:
                logger.warning("Completion notice will retry (%s)", type(exc).__name__)

    def notify_one(self, job_id: str, notifications) -> None:
        job = self.get(job_id)
        if job["state"] == "completed":
            title, body = job_notice(self.memory, job, job["result"])
        else:
            title = f"{job['kind'].replace('_', ' ').capitalize()} could not finish"
            body = job["error"]

        def guard(db):
            current = db.execute("SELECT state,result,notified FROM generation_jobs WHERE id=?",
                                 (job_id,)).fetchone()
            if (current is None or current["notified"] or current["state"] != job["state"]
                    or (json.loads(current["result"]) if current["result"] else None)
                    != job["result"]):
                raise EvidenceChanged("Recorded evidence changed")

        notifications.publish(
            kind=job["kind"], title=title, body=body,
            target={"job_id": job["id"], "kind": job["kind"]},
            event_key=f"job:{job['id']}", guard=guard,
        )
        with self.store.connect() as db:
            db.execute("UPDATE generation_jobs SET notified=1 WHERE id=?", (job["id"],))

    async def process_one(self) -> bool:
        policy = "" if self.memory.settings.llm_enabled else f" AND {RAW_REWIND}"
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM generation_jobs WHERE state='queued' AND retry_at<=?" + policy
                + " ORDER BY CASE WHEN kind='wearable_action' THEN 0 ELSE 1 END,"
                " created_at LIMIT 1", (stamp(utc_now()),),
            ).fetchone()
            if row is None:
                return False
            db.execute("UPDATE generation_jobs SET state='running' WHERE id=?", (row["id"],))
        job = self.get(row["id"])
        token = generating_job.set(True)
        try:
            async with asyncio.timeout(self.memory.settings.inference_timeout_seconds):
                result = await self.execute(job)
            self.complete(job, result)
        except (InferenceUnavailable, RecentAudioPending) as exc:
            with self.store.connect() as db:
                db.execute(
                    "UPDATE generation_jobs SET state='queued',error=NULL,retry_at=? WHERE id=? "
                    "AND state='running'",
                    (stamp(utc_now() + timedelta(seconds=exc.retry_after_seconds)), job["id"]),
                )
        except InferenceDisabled:
            with self.store.connect() as db:
                db.execute(
                    "UPDATE generation_jobs SET state='queued',error=NULL WHERE id=?",
                    (job["id"],),
                )
        except asyncio.CancelledError:
            with self.store.connect() as db:
                db.execute("UPDATE generation_jobs SET state='queued' WHERE id=?", (job["id"],))
            raise
        except Exception:
            # Provider responses can contain credentials or transcript text.
            with self.store.connect() as db:
                db.execute(
                    "UPDATE generation_jobs SET state='failed',error=? WHERE id=?",
                    ("Generation could not finish. Check home-core and try again.", job["id"]),
                )
        finally:
            generating_job.reset(token)
        return True

    async def run(self) -> None:
        while True:
            try:
                self.notify_completed()
                worked = await self.process_one()
            except Exception as exc:
                logger.warning("Generation worker will retry (%s)", type(exc).__name__)
                worked = False
            if not worked:
                await asyncio.sleep(1)


def generation_routes(get_jobs) -> APIRouter:
    router = APIRouter(prefix="/v1/jobs")

    @router.post("", status_code=202)
    def enqueue(body: GenerationRequest) -> dict:
        return get_jobs().enqueue(body)

    @router.get("/{job_id}")
    def read(job_id: Annotated[UUID, Field()]) -> dict:
        return get_jobs().get(str(job_id))

    return router
