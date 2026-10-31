"""Durable weekly generation queue and immutable review versions in the archive DB."""

import json
from uuid import uuid4

from .domain import EvidenceChanged, stamp, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_versions (
 id TEXT PRIMARY KEY, week_start TEXT NOT NULL, fingerprint TEXT NOT NULL,
 content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS weekly_version_week ON weekly_versions(week_start,created_at);
CREATE TABLE IF NOT EXISTS weekly_jobs (
 id TEXT PRIMARY KEY, week_start TEXT NOT NULL, fingerprint TEXT NOT NULL,
 timezone TEXT NOT NULL, cutoff TEXT NOT NULL, state TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, completed_calls INTEGER NOT NULL DEFAULT 0,
 version_id TEXT, error TEXT, created_at TEXT NOT NULL, retry_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS weekly_requests (
 request_id TEXT PRIMARY KEY, week_start TEXT NOT NULL, force INTEGER NOT NULL,
 job_id TEXT, version_id TEXT
);
CREATE TRIGGER IF NOT EXISTS weekly_delete AFTER DELETE ON audio BEGIN
 DELETE FROM weekly_versions; DELETE FROM weekly_requests; DELETE FROM weekly_jobs;
END;
CREATE TRIGGER IF NOT EXISTS weekly_expire AFTER UPDATE ON audio
 WHEN (OLD.text IS NOT NULL AND NEW.text IS NULL) OR
      (OLD.state != 'expired' AND NEW.state = 'expired') BEGIN
 DELETE FROM weekly_versions; DELETE FROM weekly_requests; DELETE FROM weekly_jobs;
END;
"""


class WeeklyStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(weekly_jobs)")}
            if "retry_at" not in columns:
                db.execute("ALTER TABLE weekly_jobs ADD COLUMN retry_at TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def public_job(row) -> dict:
        return {
            key: row[key]
            for key in (
                "id",
                "week_start",
                "state",
                "attempts",
                "completed_calls",
                "version_id",
                "error",
                "created_at",
            )
        }

    def recover(self) -> None:
        with self.store.connect() as db:
            db.execute("UPDATE weekly_jobs SET state='queued' WHERE state='running' AND attempts<3")
            db.execute(
                "UPDATE weekly_jobs SET state='failed',error='Generation interrupted repeatedly; "
                "retry explicitly' WHERE state='running'"
            )

    def enqueue(self, day: str, snapshot: dict, request_id: str, force: bool) -> dict:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM weekly_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if old:
                if old["week_start"] != day or bool(old["force"]) != force:
                    raise ValueError("Request ID was already used with different parameters")
                if old["job_id"]:
                    row = db.execute(
                        "SELECT * FROM weekly_jobs WHERE id=?", (old["job_id"],)
                    ).fetchone()
                    return {"job": self.public_job(row), "version_id": row["version_id"]}
                return {"job": None, "version_id": old["version_id"]}
            version = (
                None
                if force
                else db.execute(
                    "SELECT id FROM weekly_versions WHERE week_start=? AND fingerprint=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (day, snapshot["fingerprint"]),
                ).fetchone()
            )
            job = None
            if not version:
                job = db.execute(
                    "SELECT * FROM weekly_jobs WHERE week_start=? AND fingerprint=? "
                    "AND state IN ('queued','running') ORDER BY created_at LIMIT 1",
                    (day, snapshot["fingerprint"]),
                ).fetchone()
                if not job:
                    key = str(uuid4())
                    db.execute(
                        "INSERT INTO weekly_jobs(id,week_start,fingerprint,timezone,cutoff,state,"
                        "created_at) VALUES(?,?,?,?,?,'queued',?)",
                        (
                            key,
                            day,
                            snapshot["fingerprint"],
                            snapshot["timezone"],
                            snapshot["cutoff"],
                            stamp(utc_now()),
                        ),
                    )
                    job = db.execute("SELECT * FROM weekly_jobs WHERE id=?", (key,)).fetchone()
            version_id = version["id"] if version else None
            db.execute(
                "INSERT INTO weekly_requests VALUES(?,?,?,?,?)",
                (request_id, day, int(force), job["id"] if job else None, version_id),
            )
            return {"job": self.public_job(job) if job else None, "version_id": version_id}

    def claim(self) -> dict | None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM weekly_jobs WHERE state='queued' AND retry_at<=? "
                "ORDER BY created_at LIMIT 1", (stamp(utc_now()),),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE weekly_jobs SET state='running',attempts=attempts+1,completed_calls=0 "
                    "WHERE id=?",
                    (row["id"],),
                )
                return dict(row)
        return None

    def progress(self, key: str) -> None:
        with self.store.connect() as db:
            cursor = db.execute(
                "UPDATE weekly_jobs SET completed_calls=completed_calls+1 "
                "WHERE id=? AND state='running'",
                (key,),
            )
            if not cursor.rowcount:
                raise EvidenceChanged("Review was purged while generating")

    def fail(self, key: str, message: str) -> None:
        with self.store.connect() as db:
            db.execute(
                "UPDATE weekly_jobs SET state='failed',error=? WHERE id=?",
                (message, key),
            )

    def job(self, key: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM weekly_jobs WHERE id=?", (key,)).fetchone()
            return self.public_job(row) if row else None

    def latest_job(self, day: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM weekly_jobs WHERE week_start=? ORDER BY created_at DESC LIMIT 1",
                (day,),
            ).fetchone()
            return self.public_job(row) if row else None

    def version(self, day: str, version_id: str | None = None) -> dict | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT content FROM weekly_versions WHERE week_start=? "
                + ("AND id=? " if version_id else "")
                + "ORDER BY created_at DESC LIMIT 1",
                (day, version_id) if version_id else (day,),
            ).fetchone()
            return json.loads(row["content"]) if row else None

    def versions(self, day: str) -> list[dict]:
        with self.store.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,week_start,fingerprint,created_at FROM weekly_versions "
                    "WHERE week_start=? ORDER BY created_at DESC LIMIT 100",
                    (day,),
                )
            ]

    def publish(self, job: dict, version: dict, snapshot_in_transaction) -> None:
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT state FROM weekly_jobs WHERE id=?", (job["id"],)
            ).fetchone()
            if not existing or existing["state"] != "running":
                raise EvidenceChanged("Review job was removed")
            if snapshot_in_transaction(db)["fingerprint"] != job["fingerprint"]:
                raise EvidenceChanged("Evidence changed during generation")
            db.execute(
                "INSERT INTO weekly_versions VALUES(?,?,?,?,?)",
                (
                    version["id"],
                    version["week_start"],
                    version["fingerprint"],
                    json.dumps(version, ensure_ascii=False),
                    version["generated_at"],
                ),
            )
            db.execute(
                "UPDATE weekly_jobs SET state='succeeded',version_id=?,error=NULL WHERE id=?",
                (version["id"], job["id"]),
            )
