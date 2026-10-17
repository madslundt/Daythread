"""On-demand weekly projections over original local archive evidence."""

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .completion_events import publish_weekly
from .domain import EvidenceChanged, stamp, utc_now
from .inference import InferenceError, for_task
from .service import public_source
from .weekly_inference import generate_items
from .weekly_models import PROMPT_VERSION, ReviewVersion, default_week, week_bounds
from .weekly_store import WeeklyStore

logger = logging.getLogger(__name__)


def coverage(rows: list[dict], gaps: list[dict], start: datetime, end: datetime, zone: str) -> dict:
    speech = [row for row in rows if row["state"] == "done" and (row["text"] or "").strip()]

    def interval_seconds(records: list[dict]) -> float:
        intervals = sorted(
            (
                max(start, datetime.fromisoformat(row["started_at"])),
                min(end, datetime.fromisoformat(row["ended_at"])),
            )
            for row in records
        )
        seconds, until = 0.0, start
        for left, right in intervals:
            seconds += max(0, (right - max(left, until)).total_seconds())
            until = max(until, right)
        return seconds

    dates = set()
    for row in speech:
        first = max(start, datetime.fromisoformat(row["started_at"]))
        last = min(end, datetime.fromisoformat(row["ended_at"])) - timedelta(microseconds=1)
        day, final_day = (value.astimezone(ZoneInfo(zone)).date() for value in (first, last))
        while day <= final_day:
            dates.add(day)
            day += timedelta(days=1)
    return {
        "sources": len(rows),
        "speech_sources": len(speech),
        "speech_days": len(dates),
        "pending_chunks": sum(row["state"] in ("queued", "transcribing") for row in rows),
        "empty_chunks": sum(
            row["state"] == "done" and not (row["text"] or "").strip() for row in rows
        ),
        "expired_chunks": sum(row["state"] == "expired" for row in rows),
        "audio_unavailable": sum(not row["audio_available"] for row in rows),
        "captured_seconds": interval_seconds(rows),
        "capture_gaps": len(gaps),
        "known_gap_seconds": interval_seconds(gaps),
    }


class WeeklyReviews:
    def __init__(self, memory):
        self.memory = memory
        self.store = WeeklyStore(memory.store)

    def snapshot(self, day: date, cutoff: datetime | None = None, db=None) -> dict:
        if db is None:
            with self.memory.store.connect() as connection:
                connection.execute("BEGIN")
                return self.snapshot(day, cutoff, connection)
        settings = self.memory.settings
        saved = db.execute("SELECT value FROM runtime_settings WHERE key='timezone'").fetchone()
        zone = json.loads(saved["value"]) if saved else settings.timezone
        saved_language = db.execute(
            "SELECT value FROM runtime_settings WHERE key='summary_language'"
        ).fetchone()
        summary_language = (
            json.loads(saved_language["value"]) if saved_language else settings.summary_language
        ).lower()
        before, start, end = week_bounds(day, zone)
        now = utc_now()
        if start > now:
            raise ValueError("Future weeks cannot be reviewed")
        cutoff = min(cutoff or now, end)
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM canonical_audio WHERE started_at<? AND ended_at>? "
                "ORDER BY started_at,id",
                (stamp(cutoff), stamp(before)),
            )
        ]
        gaps = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM capture_gaps WHERE started_at<? AND ended_at>? "
                "ORDER BY started_at,gap_id",
                (stamp(cutoff), stamp(before)),
            )
        ]
        # Include contextual-event mutations without treating hints as factual evidence.
        events = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM events WHERE occurred_at>=? AND occurred_at<? "
                "ORDER BY occurred_at,event_id",
                (stamp(before - timedelta(minutes=10)), stamp(cutoff + timedelta(minutes=10))),
            )
        ]
        current = [
            row
            for row in rows
            if row["started_at"] < stamp(cutoff) and row["ended_at"] > stamp(start)
        ]
        previous = [row for row in rows if row["started_at"] < stamp(start)]
        sources = []
        for row in rows:
            if row["state"] != "done" or not (row["text"] or "").strip():
                continue
            role = "boundary"
            if row["started_at"] >= stamp(start) and row["ended_at"] <= stamp(end):
                role = "current"
            elif row["started_at"] >= stamp(before) and row["ended_at"] <= stamp(start):
                role = "previous"
            sources.append(public_source(row) | {"role": role})
        current_gaps = [gap for gap in gaps if gap["ended_at"] > stamp(start)]
        previous_gaps = [gap for gap in gaps if gap["started_at"] < stamp(start)]
        cov = coverage(current, current_gaps, start, cutoff, zone)
        prior_cov = coverage(previous, previous_gaps, before, start, zone)
        # Cutoff moves while a partial week is idle, but unchanged original evidence is reusable.
        identity = {
            "week_start": day.isoformat(),
            "timezone": zone,
            "summary_language": summary_language,
            "start": stamp(start),
            "end": stamp(end),
            "comparison_start": stamp(before),
            "rows": [
                public_source(row)
                | {
                    "state": "pending"
                    if row["state"] in ("queued", "transcribing")
                    else row["state"]
                }
                for row in rows
            ],
            "gaps": gaps,
            "events": events,
            "model": settings.llm_route("weekly").model,
            "endpoint": settings.llm_route("weekly").base_url,
            "prompt_version": PROMPT_VERSION,
            "deployment_revision": settings.llm_route("weekly").revision,
            "partial": cutoff < end,
        }
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return {
            "week_start": day.isoformat(),
            "timezone": zone,
            "summary_language": summary_language,
            "start": stamp(start),
            "end": stamp(end),
            "comparison_start": stamp(before),
            "cutoff": stamp(cutoff),
            "partial": cutoff < end,
            "sources": sources,
            "coverage": cov,
            "comparison_coverage": prior_cov,
            "fingerprint": fingerprint,
        }

    def read(self, day: date | None = None, version_id: str | None = None) -> dict:
        day = day or default_week(self.memory.settings.timezone)
        live = self.snapshot(day)
        version = self.store.version(day.isoformat(), version_id)
        sources = []
        if version:
            for source in version["sources"]:
                row = self.memory.store.get_audio(source["id"])
                if row:
                    sources.append(public_source(row))
        return {
            **{key: value for key, value in live.items() if key != "sources"},
            "state": "generated"
            if version
            else ("not_generated" if live["coverage"]["speech_sources"] else "empty"),
            "version": version,
            "sources": sources,
            "stale": bool(version and version["fingerprint"] != live["fingerprint"]),
            "job": self.store.latest_job(day.isoformat()),
        }

    def enqueue(self, day: date, request_id: str, force: bool) -> dict:
        snapshot = self.snapshot(day)
        if not snapshot["coverage"]["speech_sources"]:
            return {"job": None, "version_id": None, "state": "empty"}
        return self.store.enqueue(day.isoformat(), snapshot, request_id, force)

    async def process_one(self) -> bool:
        job = self.store.claim()
        if job is None:
            return False
        try:
            day = date.fromisoformat(job["week_start"])
            # Include arrivals since enqueue too; mutation fails visibly and can be retried.
            snapshot = self.snapshot(day)
            if snapshot["fingerprint"] != job["fingerprint"]:
                raise EvidenceChanged("Evidence changed while queued")
            items = await generate_items(
                for_task(self.memory.inference, "weekly"),
                snapshot["sources"],
                snapshot["timezone"],
                lambda: self.store.progress(job["id"]),
                summary_language=snapshot["summary_language"],
            )
            version = ReviewVersion.model_validate(
                {
                    **snapshot,
                    "id": str(uuid4()),
                    "generated_at": stamp(utc_now()),
                    "prompt_version": PROMPT_VERSION,
                    "model": self.memory.settings.llm_route("weekly").model,
                    "items": items,
                }
            ).model_dump(mode="json")
            self.store.publish(job, version, lambda db: self.snapshot(day, db=db))
            def notification_guard(db):
                if (not db.execute("SELECT 1 FROM weekly_versions WHERE id=?",
                                   (version["id"],)).fetchone()
                        or self.snapshot(day, db=db)["fingerprint"] != version["fingerprint"]):
                    raise EvidenceChanged("Recorded evidence changed")

            publish_weekly(self.memory, version, guard=notification_guard)
        except EvidenceChanged:
            self.store.fail(job["id"], "Recorded evidence changed; generate again")
        except InferenceError:
            self.store.fail(
                job["id"],
                "Local model output failed evidence/schema validation or "
                "the review exceeded its processing budget; retry or inspect the model",
            )
        except Exception:
            # No transcript, provider body, credentials or endpoint in durable errors.
            self.store.fail(
                job["id"], "Local review generation failed; check local inference and retry"
            )
        return True

    async def run(self) -> None:
        recovered = False
        while True:
            try:
                if not recovered:
                    self.store.recover()
                    recovered = True
                worked = await self.process_one()
            except Exception as exc:
                logger.warning("Weekly review worker will retry (%s)", type(exc).__name__)
                worked = False
                recovered = False
            await asyncio.sleep(0.1 if worked else 1)
