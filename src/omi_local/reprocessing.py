"""Durable archive redo with retained-audio and local-calendar boundaries."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator

from .blob_storage import audio_is_present
from .domain import EvidenceChanged, stamp, utc_now

logger = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS reprocessing_jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', days TEXT NOT NULL,
 completed_days TEXT NOT NULL DEFAULT '[]',
 overview_days TEXT NOT NULL DEFAULT '[]', overview_weeks TEXT NOT NULL DEFAULT '[]',
 error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reprocessing_sources (
 job_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(job_id,source_id)
);
"""
ACTIVE = "('queued','transcribing','summarizing')"


class RedoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["transcripts", "summaries"]
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def date_range(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Supply both dates or choose all available dates")
        if self.start is not None and self.end < self.start:
            raise ValueError("The end date must be on or after the start date")
        return self


class Reprocessing:
    def __init__(self, memory):
        self.memory, self.store = memory, memory.store
        with self.store.connect() as db:
            db.executescript(SCHEMA)

    def row_days(self, row):
        zone = ZoneInfo(self.memory.settings.timezone)
        first = datetime.fromisoformat(row["started_at"]).astimezone(zone).date()
        last = (datetime.fromisoformat(row["ended_at"]) - timedelta(microseconds=1))
        last = max(first, last.astimezone(zone).date())
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]

    def available(self, db):
        rows = [dict(row) for row in db.execute(
            "SELECT id,started_at,ended_at,state,length(trim(text))>0 AS has_text,"
            "path,audio_available,error_code "
            "FROM audio WHERE text IS NOT NULL ORDER BY started_at,id"
        )]
        transcripts = [row for row in rows if audio_is_present(row)]
        summary_days = {day for row in rows if row["has_text"]
                        for day in self.row_days(row)}
        summary_days.update(date.fromisoformat(row["day"]) for row in db.execute(
            "SELECT day FROM diary"
        ))
        return transcripts, summary_days

    def public_job(self, db, row):
        if row is None:
            return None
        sources = db.execute(
            "SELECT a.state FROM reprocessing_sources r LEFT JOIN audio a ON a.id=r.source_id "
            "WHERE r.job_id=?", (row["id"],),
        ).fetchall()
        return {key: row[key] for key in ("id", "kind", "state", "error")} | {
            "start": row["start_day"], "end": row["end_day"],
            "transcript_total": len(sources),
            "transcript_completed": sum(source["state"] == "done" for source in sources),
            "summary_total": len(json.loads(row["days"])),
            "summary_completed": len(json.loads(row["completed_days"])),
        }

    def observation(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            transcripts, days = self.available(db)
            transcript_days = {day for row in transcripts for day in self.row_days(row)}

            def bounds(values, count):
                return {"start": min(values).isoformat() if values else None,
                        "end": max(values).isoformat() if values else None, "count": count}

            row = db.execute(
                "SELECT * FROM reprocessing_jobs ORDER BY created_at DESC,id DESC LIMIT 1"
            ).fetchone()
            return {"transcripts": bounds(transcript_days, len(transcripts)),
                    "summaries": bounds(days, len(days)), "job": self.public_job(db, row)}

    def enqueue(self, body: RedoRequest):
        if not self.memory.settings.background_processing_enabled:
            raise HTTPException(409, "Enable background processing before starting a redo")
        if body.kind == "transcripts" and not self.memory.settings.worker_enabled:
            raise HTTPException(409, "Enable the audio worker before redoing transcripts")
        if not self.memory.settings.llm_enabled:
            raise HTTPException(409, "Enable language model processing before redoing summaries")
        if body.kind == "transcripts" and not self.memory.settings.stt_enabled:
            raise HTTPException(409, "Enable transcription before redoing transcripts")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(f"SELECT 1 FROM reprocessing_jobs WHERE state IN {ACTIVE}").fetchone():
                raise HTTPException(409, "A redo is already in progress")
            transcripts, summary_days = self.available(db)
            days = ({day for row in transcripts for day in self.row_days(row)}
                    if body.kind == "transcripts" else summary_days)
            if not days:
                raise HTTPException(422, "No available data to redo")
            start, end = body.start or min(days), body.end or max(days)
            if start < min(days) or end > max(days):
                raise HTTPException(422, "Choose dates within the available date range")
            selected_days = sorted(day for day in days if start <= day <= end)
            if not selected_days:
                raise HTTPException(422, "No available data in the selected date range")
            selected = [row for row in transcripts if any(
                start <= day <= end for day in self.row_days(row)
            )] if body.kind == "transcripts" else []
            if any(row["state"] != "done" for row in selected):
                raise HTTPException(409, "Wait for selected recordings to finish processing")
            # A recording crossing midnight must refresh every recap that uses it.
            if selected:
                selected_days = sorted({day for row in selected for day in self.row_days(row)})
            key = str(uuid4())
            overview_days = [row["day"] for row in db.execute("SELECT day FROM day_overviews")
                             if date.fromisoformat(row["day"]) in selected_days]
            weeks = {day - timedelta(days=day.weekday()) for day in selected_days}
            overview_weeks = [row["week_start"] for row in db.execute(
                "SELECT week_start FROM week_overviews"
            ) if date.fromisoformat(row["week_start"]) in weeks]
            db.execute(
                "INSERT INTO reprocessing_jobs(id,kind,start_day,end_day,days,created_at,"
                "overview_days,overview_weeks) VALUES(?,?,?,?,?,?,?,?)",
                (key, body.kind, start.isoformat(), end.isoformat(),
                 json.dumps([day.isoformat() for day in selected_days]), stamp(utc_now()),
                 json.dumps(overview_days), json.dumps(overview_weeks)),
            )
            for row in selected:
                db.execute("INSERT INTO reprocessing_sources VALUES(?,?)", (key, row["id"]))
                db.execute(
                    "UPDATE audio SET state='queued',attempts=0,retry_at='',error=NULL,"
                    "error_code=NULL,processing_started_at=NULL WHERE id=?", (row["id"],),
                )
            return self.public_job(db, db.execute(
                "SELECT * FROM reprocessing_jobs WHERE id=?", (key,),
            ).fetchone())

    async def process_one(self):
        if not self.memory.settings.llm_enabled:
            return False
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT * FROM reprocessing_jobs WHERE state IN {ACTIVE} "
                "ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            row = dict(row)
            sources = db.execute(
                "SELECT a.state FROM reprocessing_sources r "
                "LEFT JOIN audio a ON a.id=r.source_id WHERE r.job_id=?", (row["id"],),
            ).fetchall()
            if any(source["state"] in (None, "blocked") for source in sources):
                db.execute("UPDATE reprocessing_jobs SET state='failed',error=? WHERE id=?",
                           ("A selected recording is unavailable; restore its audio and redo again",
                            row["id"]))
                return True
            if any(source["state"] != "done" for source in sources):
                db.execute("UPDATE reprocessing_jobs SET state='transcribing' WHERE id=?",
                           (row["id"],))
                return False
            completed = json.loads(row["completed_days"])
            pending = [day for day in json.loads(row["days"]) if day not in completed]
            db.execute("UPDATE reprocessing_jobs SET state='summarizing' WHERE id=?", (row["id"],))
        try:
            if pending:
                day = date.fromisoformat(pending[0])
                groups = self.memory.recaps.for_day(self.memory.recaps.groups(utc_now()), day)
                if any(not group.ready and any(source["text"] for source in group.sources)
                       for group in groups):
                    return False  # Wait for complete conversation evidence before inference.
                await self.memory.recap(day, force=True)
                if pending[0] in json.loads(row["overview_days"]):
                    from .day_overview import DayOverview
                    await DayOverview(self.memory).generate(day, force=True)
                completed.append(pending[0])
            if len(pending) <= 1:
                from .weekly_overview import WeekOverview
                for week in json.loads(row["overview_weeks"]):
                    await WeekOverview(self.memory).generate(date.fromisoformat(week), force=True)
            with self.store.connect() as db:
                db.execute(
                    "UPDATE reprocessing_jobs SET state=?,completed_days=?,error=NULL WHERE id=?",
                    ("summarizing" if len(pending) > 1 else "completed",
                     json.dumps(completed), row["id"]),
                )
        except EvidenceChanged:
            return False  # Late arrivals/corrections retry against the next settled snapshot.
        except Exception as exc:
            logger.warning("Archive redo failed (%s)", type(exc).__name__)
            with self.store.connect() as db:
                db.execute("UPDATE reprocessing_jobs SET state='failed',error=? WHERE id=?",
                           ("Summary generation failed; check model settings and redo again",
                            row["id"]))
        return True

    async def run(self):
        while True:
            try:
                worked = await self.process_one()
            except Exception as exc:
                logger.warning("Archive redo will retry (%s)", type(exc).__name__)
                worked = False
            await asyncio.sleep(0.2 if worked else 3)


def reprocessing_routes(get_jobs):
    router = APIRouter()

    @router.get("/v1/reprocessing")
    def observation() -> dict:
        return get_jobs().observation()

    @router.post("/v1/reprocessing", status_code=202)
    def redo(body: RedoRequest) -> dict:
        return get_jobs().enqueue(body)

    return router
