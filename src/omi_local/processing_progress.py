"""Measured transcription work; provider calls themselves have no progress signal."""

from collections.abc import Callable
from datetime import datetime
from sqlite3 import Connection
from statistics import median

from .domain import stamp, utc_now

ProgressCallback = Callable[[str, int, int], None]

SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_progress (
 audio_id TEXT PRIMARY KEY REFERENCES audio(id) ON DELETE CASCADE,
 stage TEXT NOT NULL, completed_chunks INTEGER NOT NULL DEFAULT 0,
 total_chunks INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL,
 finished_at TEXT
);
CREATE INDEX IF NOT EXISTS processing_progress_finished ON processing_progress(finished_at DESC);
CREATE TRIGGER IF NOT EXISTS processing_progress_delete AFTER DELETE ON audio BEGIN
 DELETE FROM processing_progress WHERE audio_id=OLD.id;
END;
"""


class ProcessingProgressStore:
    def start_processing(self, audio_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO processing_progress "
                "(audio_id,stage,started_at) VALUES(?,'analyzing',?)",
                (audio_id, stamp(utc_now())),
            )

    def update_processing(
        self, audio_id: str, stage: str, completed_chunks: int = 0, total_chunks: int = 0,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE processing_progress SET stage=?,completed_chunks=?,total_chunks=? "
                "WHERE audio_id=?",
                (stage, completed_chunks, total_chunks, audio_id),
            )

    def finish_processing(self, audio_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE processing_progress SET stage='complete',finished_at=? WHERE audio_id=?",
                (stamp(utc_now()), audio_id),
            )


def processing_observation(db: Connection, now: datetime) -> dict:
    active = []
    for row in db.execute(
        "SELECT a.id,p.stage,p.completed_chunks,p.total_chunks,"
        "coalesce(p.started_at,a.processing_started_at) AS started_at "
        "FROM audio a LEFT JOIN processing_progress p ON p.audio_id=a.id "
        "WHERE a.state='transcribing' ORDER BY a.started_at,a.id"
    ):
        active.append({
            "id": row["id"], "stage": row["stage"] or "transcribing",
            "completed_chunks": row["completed_chunks"] or 0,
            "total_chunks": row["total_chunks"] or 0,
            "elapsed_seconds": max(0, (now - datetime.fromisoformat(
                row["started_at"]
            )).total_seconds()) if row["started_at"] else None,
        })
    ratios = []
    for row in db.execute(
        "SELECT a.started_at,a.ended_at,p.started_at AS processing_started_at,p.finished_at "
        "FROM processing_progress p JOIN audio a ON a.id=p.audio_id "
        "WHERE a.state='done' AND p.finished_at IS NOT NULL "
        "ORDER BY p.finished_at DESC LIMIT 20"
    ):
        audio = (datetime.fromisoformat(row["ended_at"])
                 - datetime.fromisoformat(row["started_at"])).total_seconds()
        elapsed = (datetime.fromisoformat(row["finished_at"])
                   - datetime.fromisoformat(row["processing_started_at"])).total_seconds()
        if audio > 0 and elapsed > 0:
            ratios.append(elapsed / audio)
    pending = db.execute(
        "SELECT count(*) AS count,"
        "count(*) FILTER(WHERE state='blocked' OR error IS NOT NULL) AS delayed,"
        "sum((julianday(ended_at)-julianday(started_at))*86400) AS audio_seconds FROM audio "
        "WHERE state IN ('queued','transcribing','blocked')"
    ).fetchone()
    estimate = None
    if not pending["count"]:
        estimate = 0
    elif ratios and not pending["delayed"]:
        total = pending["audio_seconds"] * median(ratios)
        remaining = total - sum(item["elapsed_seconds"] or 0 for item in active)
        # Once the measured baseline is exceeded, a new finish time is unknown.
        if remaining > 0:
            estimate = max(0.1, round(remaining, 1))
    return {
        "active": active, "estimated_remaining_seconds": estimate,
        "estimate_sample_count": len(ratios),
    }
