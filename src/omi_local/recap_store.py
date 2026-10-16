"""Persistent conversation preparation with source-aware invalidation."""

import json
import sqlite3
from collections.abc import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS recap_activity (
 source_id TEXT PRIMARY KEY, digest TEXT NOT NULL, activity TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recap_summaries (
 fingerprint TEXT PRIMARY KEY, content TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS recap_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM recap_activity WHERE source_id=OLD.id;
 DELETE FROM recap_summaries;
END;
CREATE TRIGGER IF NOT EXISTS recap_source_correct AFTER UPDATE OF text ON audio
WHEN OLD.text IS NOT NULL AND OLD.text IS NOT NEW.text BEGIN
 DELETE FROM recap_summaries;
END;
"""


class RecapStore:
    def recap_snapshot(self, db: sqlite3.Connection | None = None) -> dict:
        if db is None:
            with self.connect() as db:
                db.execute("BEGIN")
                return self.recap_snapshot(db)
        return {
            "rows": [dict(row) for row in db.execute(
                "SELECT a.id,a.device_id,a.started_at,a.ended_at,a.state,a.source_kind,"
                "a.source_id,a.source_version,a.transcript_version,a.digest,a.path,a.audio_available,"
                "a.source_metadata,"
                "length(trim(coalesce(a.text,'')))>0 AS text,"
                "json_object('voice_activity',json_extract(a.transcription_details,"
                "'$.voice_activity')) AS transcription_details,v.activity AS recap_activity "
                "FROM audio a "
                "LEFT JOIN recap_activity v ON v.source_id=a.id AND v.digest=a.digest "
                "ORDER BY a.started_at,a.id"
            )],
            "events": [dict(row) for row in db.execute("SELECT * FROM events")],
            "gaps": [dict(row) for row in db.execute("SELECT * FROM capture_gaps")],
            "source_matches": [dict(row) for row in db.execute("SELECT * FROM source_matches")],
            "upload_parts": [dict(row) for row in db.execute("SELECT * FROM upload_parts")],
        }

    def save_recap_activity(self, source_id: str, digest: str, activity: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO recap_activity SELECT id,digest,? FROM audio "
                "WHERE id=? AND digest=?",
                (json.dumps(activity), source_id, digest),
            )

    def recap_summary(self, fingerprint: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT content FROM recap_summaries WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
        return row["content"] if row else None

    def save_recap_summary(
        self, fingerprint: str, content: str, sources: list[dict],
        guard: Callable[[sqlite3.Connection], None],
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            guard(db)
            self.verify_sources(db, sources)
            db.execute("INSERT OR REPLACE INTO recap_summaries VALUES (?,?)",
                       (fingerprint, content))
