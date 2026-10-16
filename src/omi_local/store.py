import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from .archive_store import EXTENDED_SCHEMA, ArchiveStore
from .decision_export import diary_markdown
from .decision_store import DECISION_SCHEMA, DecisionStore
from .domain import ButtonEvent, HintPolicy, stamp, utc_now

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS audio (
 id TEXT PRIMARY KEY, device_id TEXT NOT NULL, started_at TEXT NOT NULL,
 ended_at TEXT NOT NULL, digest TEXT NOT NULL, path TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 retry_at TEXT NOT NULL DEFAULT '', error TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS audio_time ON audio(started_at, ended_at);
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_search USING fts5(id UNINDEXED, text);
CREATE TABLE IF NOT EXISTS events (
 event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
 action TEXT NOT NULL, max_minutes INTEGER NOT NULL, context_seconds INTEGER NOT NULL,
 hint_policy TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS diary (
 day TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class Store(ArchiveStore, DecisionStore):
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audio_dir = root / "audio"
        self.diary_dir = root / "diary"
        self.audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.diary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.executescript(EXTENDED_SCHEMA)
            db.executescript(DECISION_SCHEMA)
            # Preserve existing event history when upgrading an already-used starter.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
            if "hint_policy" not in columns:
                db.execute("ALTER TABLE events ADD COLUMN hint_policy TEXT NOT NULL DEFAULT '{}'")
            audio_columns = {row["name"] for row in db.execute("PRAGMA table_info(audio)")}
            if "segments" not in audio_columns:
                db.execute("ALTER TABLE audio ADD COLUMN segments TEXT NOT NULL DEFAULT '[]'")
            if "audio_available" not in audio_columns:
                db.execute(
                    "ALTER TABLE audio ADD COLUMN audio_available INTEGER NOT NULL DEFAULT 1"
                )
            for column in ("source_kind", "source_id"):
                if column not in audio_columns:
                    db.execute(f"ALTER TABLE audio ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            if "source_version" not in audio_columns:
                db.execute("ALTER TABLE audio ADD COLUMN source_version TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE audio SET source_version=lower(hex(randomblob(16))) "
                       "WHERE source_version=''")
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS source_insert_version AFTER INSERT ON audio
                WHEN NEW.source_version='' BEGIN
                  UPDATE audio SET source_version=lower(hex(randomblob(16))) WHERE id=NEW.id;
                END;
            """)
            session_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(chat_sessions)")
            }
            for column in ("day", "meeting_id", "start_day", "end_day"):
                if column not in session_columns:
                    db.execute(f"ALTER TABLE chat_sessions ADD COLUMN {column} TEXT")
            # Backups can be restored under a different data directory.
            for row in db.execute("SELECT id FROM audio").fetchall():
                db.execute(
                    "UPDATE audio SET path=? WHERE id=?",
                    (str(self.audio_dir / f"{row['id']}.wav"), row["id"]),
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "omi.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(
        self, audio_id: UUID, device: str, start: datetime, data: bytes, duration: float
    ) -> dict:
        key = str(audio_id)
        digest = hashlib.sha256(data).hexdigest()
        at = stamp(start)
        end = stamp(start + timedelta(seconds=duration))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
            if old:
                if (old["digest"], old["device_id"], old["started_at"]) != (digest, device, at):
                    raise ValueError("Audio ID already exists with different content or metadata")
                return dict(old)
            # Each ID is a validated UUID, never a client-supplied filename.
            path = self.audio_dir / f"{key}.wav"
            with path.open("wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            db.execute(
                "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path) "
                "VALUES(?,?,?,?,?,?)",
                (key, device, at, end, digest, str(path)),
            )
            return dict(db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone())

    def recover(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE audio SET state='queued' WHERE state='transcribing'")

    def claim(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM audio WHERE state='queued' AND retry_at<=? "
                "ORDER BY started_at LIMIT 1",
                (stamp(utc_now()),),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE audio SET state='transcribing',attempts=attempts+1 WHERE id=?",
                    (row["id"],),
                )
                return dict(row)
        return None

    def complete(
        self, audio_id: str, text: str, segments: list[dict] | None = None, claimed: bool = False
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM audio WHERE id=?", (audio_id,)).fetchone()
            if not row or (claimed and row["state"] != "transcribing"):
                return
            self.invalidate_decision_sources(db, [audio_id], purge=False)
            db.execute(
                "UPDATE audio SET state='done',text=?,segments=?,error=NULL,"
                "source_version=lower(hex(randomblob(16))) WHERE id=?",
                (text, json.dumps(segments or []), audio_id),
            )
            db.execute("DELETE FROM transcript_search WHERE id=?", (audio_id,))
            db.execute("INSERT INTO transcript_search(id,text) VALUES(?,?)", (audio_id, text))

    def retry(self, row: dict, error: str) -> None:
        delay = min(300, 2 ** min(row["attempts"] + 1, 9))
        with self.connect() as db:
            db.execute(
                "UPDATE audio SET state='queued',retry_at=?,error=? "
                "WHERE id=? AND state='transcribing'",
                (stamp(utc_now() + timedelta(seconds=delay)), error, row["id"]),
            )

    def window(self, start: datetime, end: datetime) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM audio WHERE started_at<? AND ended_at>? ORDER BY started_at,id",
                (stamp(end), stamp(start)),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_audio(self, audio_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM audio WHERE id=?", (audio_id,)).fetchone()
            return dict(row) if row else None

    def recorded_intervals(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT started_at,ended_at FROM audio ORDER BY started_at"
                ).fetchall()
            ]

    def search(
        self, question: str, limit: int, start: datetime | None = None, end: datetime | None = None
    ) -> list[dict]:
        words = re.findall(r"\w+", question, re.UNICODE)[:30]
        if not words:
            return []
        match = " OR ".join('"' + word + '"' for word in words)
        scope = " AND a.started_at<? AND a.ended_at>?" if start and end else ""
        args = (match, stamp(end), stamp(start), limit) if start and end else (match, limit)
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT a.* FROM transcript_search f JOIN audio a ON a.id=f.id "
                    "WHERE transcript_search MATCH ?"
                    + scope
                    + " ORDER BY bm25(transcript_search) LIMIT ?",
                    args,
                ).fetchall()
            ]

    def append_event(
        self, event: ButtonEvent, max_minutes: int, context: int, policy: HintPolicy | None = None
    ) -> dict:
        values = (str(event.event_id), event.device_id, stamp(event.occurred_at), event.action)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM events WHERE event_id=?", (values[0],)).fetchone()
            if existing:
                if tuple(existing)[:4] != values:
                    raise ValueError("Event ID already exists with different metadata")
                if event.meeting_max_minutes is not None:
                    if event.meeting_max_minutes != existing["max_minutes"]:
                        raise ValueError("Event ID already exists with a different meeting timeout")
                captured_policy = HintPolicy.model_validate_json(existing["hint_policy"])
                for field in ("meeting_min_seconds", "marker_cooldown_seconds"):
                    override = getattr(event, field)
                    if override is not None and override != getattr(captured_policy, field):
                        raise ValueError("Event ID already exists with a different captured policy")
                return dict(existing)
            captured_max = (
                event.meeting_max_minutes if event.meeting_max_minutes is not None else max_minutes
            )
            captured_policy = (policy or HintPolicy()).model_copy(
                update={
                    field: getattr(event, field)
                    for field in ("meeting_min_seconds", "marker_cooldown_seconds")
                    if getattr(event, field) is not None
                }
            )
            db.execute(
                "INSERT INTO events(event_id,device_id,occurred_at,action,max_minutes,"
                "context_seconds,hint_policy) VALUES(?,?,?,?,?,?,?)",
                (*values, captured_max, context, captured_policy.model_dump_json()),
            )
            return dict(
                db.execute("SELECT * FROM events WHERE event_id=?", (values[0],)).fetchone()
            )

    def events(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events ORDER BY occurred_at,event_id"
                ).fetchall()
            ]

    def diary(self, day: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM diary WHERE day=?", (day,)).fetchone()
            return dict(row) if row else None

    def summary_days(self, start: date, end: date) -> list[str]:
        with self.connect() as db:
            return [
                row["day"]
                for row in db.execute(
                    "SELECT day FROM diary WHERE day>=? AND day<=? "
                    "AND json_type(content,'$.summary')='text' "
                    "AND length(trim(json_extract(content,'$.summary')))>0 ORDER BY day",
                    (start.isoformat(), end.isoformat()),
                )
            ]

    def save_diary(self, day: str, fingerprint: str, content: dict) -> None:
        serialized = json.dumps(content, ensure_ascii=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.verify_sources(db, content["sources"])
            db.execute(
                "INSERT INTO diary_versions VALUES(?,?,?,?,?)",
                (str(uuid4()), day, fingerprint, serialized, stamp(utc_now())),
            )
            db.execute(
                "INSERT OR REPLACE INTO diary VALUES(?,?,?,?)",
                (day, fingerprint, serialized, stamp(utc_now())),
            )
        # This file is a portable projection; SQLite remains authoritative.
        self.export_diary(day, content)

    def export_diary(self, day: str, content: dict) -> None:
        # Validate even internally generated paths at this persistence boundary.
        from datetime import date

        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Expected ISO calendar date")
        export = self.diary_dir / f"{day}.md"
        temporary = export.with_suffix(f".{uuid4()}.tmp")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.verify_sources(db, content["sources"])
            try:
                temporary.write_text(
                    diary_markdown(db, day, content),
                    encoding="utf-8",
                )
                temporary.replace(export)
            finally:
                temporary.unlink(missing_ok=True)

    def status(self) -> dict:
        with self.connect() as db:
            counts = db.execute("SELECT state,count(*) AS n FROM audio GROUP BY state").fetchall()
            return {row["state"]: row["n"] for row in counts}
