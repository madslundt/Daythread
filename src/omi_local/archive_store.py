"""Archive projections, revisions and persistent application state."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .domain import ChatScopeConflict, EvidenceChanged, stamp, utc_now
from .weekly_models import markdown as weekly_markdown

EXTENDED_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transcript_revisions (
 id TEXT PRIMARY KEY, audio_id TEXT NOT NULL, previous_text TEXT, text TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diary_versions (
 id TEXT PRIMARY KEY, day TEXT NOT NULL, fingerprint TEXT NOT NULL,
 content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 day TEXT, meeting_id TEXT, start_day TEXT, end_day TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
 content TEXT NOT NULL, sources TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_session_messages ON chat_messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS capture_gaps (
 gap_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, started_at TEXT NOT NULL,
 ended_at TEXT NOT NULL, reason TEXT NOT NULL
);
"""


class ArchiveStore:
    """SQLite helpers mixed into Store; paths remain controlled by the archive."""

    @staticmethod
    def verify_sources(db: sqlite3.Connection, sources: list[dict]) -> None:
        for source in sources:
            current = db.execute("SELECT text FROM audio WHERE id=?", (source["id"],)).fetchone()
            if not current or (
                source.get("text") is not None and current["text"] != source["text"]
            ):
                raise EvidenceChanged("Recorded evidence changed; retry the request")

    def runtime_settings(self) -> dict:
        with self.connect() as db:
            return {
                row["key"]: json.loads(row["value"])
                for row in db.execute("SELECT * FROM runtime_settings")
            }

    def append_gap(self, gap: dict) -> dict:
        values = tuple(
            gap[key] for key in ("gap_id", "device_id", "started_at", "ended_at", "reason")
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM capture_gaps WHERE gap_id=?", (values[0],)).fetchone()
            if old and tuple(old) != values:
                raise ValueError("Gap ID already exists with different metadata")
            db.execute("INSERT OR IGNORE INTO capture_gaps VALUES(?,?,?,?,?)", values)
        return gap

    def capture_gaps(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[dict]:
        scope = " WHERE started_at<? AND ended_at>?" if start and end else ""
        args = (stamp(end), stamp(start)) if start and end else ()
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM capture_gaps" + scope + " ORDER BY started_at DESC LIMIT 1000",
                    args,
                )
            ]

    def save_settings(self, changes: dict) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO runtime_settings VALUES(?,?)",
                [(key, json.dumps(value)) for key, value in changes.items()],
            )

    def transcripts(
        self, start: datetime | None, end: datetime | None, query: str, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        filters, args = ["1=1"], []
        if start and end:
            filters.extend(["started_at<?", "ended_at>?"])
            args.extend([stamp(end), stamp(start)])
        if query:
            # Parameters and LIKE escaping make quotes/wildcards ordinary user text.
            filters.append("text LIKE ? ESCAPE '\\'")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.append(f"%{escaped}%")
        where = " AND ".join(filters)
        with self.connect() as db:
            total = db.execute(f"SELECT count(*) FROM audio WHERE {where}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM audio WHERE {where} ORDER BY started_at DESC,id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def correct_transcript(self, audio_id: str, text: str) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM audio WHERE id=?", (audio_id,)).fetchone()
            if not old:
                return None
            db.execute(
                "INSERT INTO transcript_revisions VALUES(?,?,?,?,?)",
                (str(uuid4()), audio_id, old["text"], text, stamp(utc_now())),
            )
            db.execute(
                "UPDATE audio SET text=?,state='done',segments='[]',error=NULL WHERE id=?",
                (text, audio_id),
            )
            db.execute("DELETE FROM transcript_search WHERE id=?", (audio_id,))
            db.execute("INSERT INTO transcript_search VALUES(?,?)", (audio_id, text))
            row = dict(db.execute("SELECT * FROM audio WHERE id=?", (audio_id,)).fetchone())
            self._invalidate_diaries(db)
        self._prune_files()
        return row

    def transcript_revisions(self, audio_id: str) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM transcript_revisions WHERE audio_id=? ORDER BY created_at",
                    (audio_id,),
                )
            ]

    def _invalidate_diaries(self, db: sqlite3.Connection, purge_history: bool = False) -> None:
        db.execute("DELETE FROM diary")
        if purge_history:
            db.execute("DELETE FROM diary_versions")

    def _prune_files(self, audio_paths: Sequence[str] = ()) -> None:
        # Commit logical removal first. A separate writer transaction excludes backup
        # pinning and ingestion while rechecking references before physical cleanup.
        # Rollbacks preserve files; a retry that recreated an ID preserves its new file.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for path in audio_paths:
                if not db.execute(
                    "SELECT 1 FROM audio WHERE path=? AND audio_available=1", (path,)
                ).fetchone():
                    Path(path).unlink(missing_ok=True)
            # Restored backup projections must obey the same purge as the SQLite records.
            for path in (self.root / "weekly").glob("*.md"):
                if not db.execute(
                    "SELECT 1 FROM weekly_versions WHERE id=?", (path.stem,)
                ).fetchone():
                    path.unlink(missing_ok=True)
            for path in self.diary_dir.glob("*.md"):
                if not db.execute("SELECT 1 FROM diary WHERE day=?", (path.stem,)).fetchone():
                    path.unlink(missing_ok=True)

    def invalidate_diaries(self, purge_history: bool = False) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._invalidate_diaries(db, purge_history)
        self._prune_files()

    def diaries(self) -> list[dict]:
        with self.connect() as db:
            return [
                json.loads(row["content"])
                for row in db.execute("SELECT content FROM diary ORDER BY day DESC")
            ]

    def diary_versions(self, day: str) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row) | {"content": json.loads(row["content"])}
                for row in db.execute(
                    "SELECT * FROM diary_versions WHERE day=? ORDER BY created_at DESC", (day,)
                )
            ]

    def ensure_session(
        self,
        session_id: str | None,
        title: str,
        day: str | None = None,
        meeting_id: str | None = None,
        scope_supplied: bool = False,
        start_day: str | None = None,
        end_day: str | None = None,
    ) -> dict:
        key, now = session_id or str(uuid4()), stamp(utc_now())
        with self.connect() as db:
            old = db.execute("SELECT * FROM chat_sessions WHERE id=?", (key,)).fetchone()
            if old:
                saved_scope = tuple(
                    old[field] for field in ("day", "meeting_id", "start_day", "end_day")
                )
                if scope_supplied and saved_scope != (day, meeting_id, start_day, end_day):
                    raise ChatScopeConflict("Start a new chat to choose a different scope")
                return dict(old)
            if session_id:
                raise KeyError("Chat session not found")
            db.execute(
                "INSERT INTO chat_sessions "
                "(id,title,created_at,updated_at,day,meeting_id,start_day,end_day) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (key, title[:120], now, now, day, meeting_id, start_day, end_day),
            )
        return {
            "id": key,
            "title": title[:120],
            "created_at": now,
            "updated_at": now,
            "day": day,
            "meeting_id": meeting_id,
            "start_day": start_day,
            "end_day": end_day,
        }

    def chat_sessions(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT 100"
                )
            ]

    def chat_session(self, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chat_sessions WHERE id=?", (key,)).fetchone()
            if not row:
                return None
            messages = [
                dict(message)
                for message in db.execute(
                    "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at,id", (key,)
                )
            ]
        return dict(row) | {
            "messages": [
                message | {"sources": json.loads(message["sources"])} for message in messages
            ]
        }

    def save_exchange(
        self, session_id: str, question: str, answer: str, sources: list[dict]
    ) -> None:
        now = stamp(utc_now())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone():
                raise EvidenceChanged("Chat was removed while the answer was generated")
            self.verify_sources(db, sources)
            for role, content, evidence in (("user", question, []), ("assistant", answer, sources)):
                db.execute(
                    "INSERT INTO chat_messages VALUES(?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        session_id,
                        role,
                        content,
                        json.dumps(evidence),
                        stamp(utc_now()),
                    ),
                )
            db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))

    def delete_audio(self, key: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM audio WHERE id=?", (key,))
            db.execute("DELETE FROM transcript_search WHERE id=?", (key,))
            db.execute("DELETE FROM transcript_revisions WHERE audio_id=?", (key,))
            # Answers may repeat this recording's content; derived conversation is removed too.
            db.execute("DELETE FROM chat_messages")
            db.execute("DELETE FROM chat_sessions")
            self._invalidate_diaries(db, purge_history=True)
        self._prune_files([row["path"]])
        return True

    def apply_retention(
        self, audio_days: int, transcript_days: int, now: datetime | None = None
    ) -> dict:
        now = now or utc_now()
        removed_audio, removed_transcripts = [], []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if audio_days:
                rows = db.execute(
                    "SELECT * FROM audio WHERE ended_at<? AND audio_available=1",
                    (stamp(now - timedelta(days=audio_days)),),
                ).fetchall()
                for row in rows:
                    db.execute(
                        "UPDATE audio SET audio_available=0, "
                        "state=CASE WHEN state IN ('queued','transcribing') "
                        "THEN 'expired' ELSE state END WHERE id=?",
                        (row["id"],),
                    )
                    removed_audio.append(row["path"])
            if transcript_days:
                cutoff = stamp(now - timedelta(days=transcript_days))
                rows = db.execute(
                    "SELECT id FROM audio WHERE ended_at<? "
                    "AND (text IS NOT NULL OR state!='expired')",
                    (cutoff,),
                ).fetchall()
                removed_transcripts = [row["id"] for row in rows]
                for key in removed_transcripts:
                    db.execute(
                        "UPDATE audio SET text=NULL,segments='[]',state='expired' WHERE id=?",
                        (key,),
                    )
                    db.execute("DELETE FROM transcript_search WHERE id=?", (key,))
                    db.execute("DELETE FROM transcript_revisions WHERE audio_id=?", (key,))
                if removed_transcripts:
                    db.execute("DELETE FROM chat_messages")
                    db.execute("DELETE FROM chat_sessions")
            if removed_audio or removed_transcripts:
                self._invalidate_diaries(db, purge_history=True)
        if removed_audio or removed_transcripts:
            self._prune_files(removed_audio)
        return {
            "audio_files_removed": len(removed_audio),
            "transcripts_removed": len(removed_transcripts),
        }

    def export_archive(self) -> Path:
        """Create a recoverable SQLite snapshot plus audio and Markdown in a local ZIP."""
        work = Path(tempfile.mkdtemp(prefix="omi-export-"))
        try:
            snapshot = work / "omi.sqlite3"
            # Pin immutable recordings while writers are excluded. Compression can then run
            # without blocking new phone uploads, retention, or explicit deletion.
            with self.connect() as guard:
                guard.execute("BEGIN IMMEDIATE")
                with self.connect() as source, closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
                    target.execute("PRAGMA journal_mode=DELETE")
                    for row in target.execute("SELECT id FROM audio WHERE audio_available=1"):
                        filename = f"{row[0]}.wav"
                        original = self.audio_dir / filename
                        pinned = work / filename
                        try:
                            os.link(original, pinned)
                        except OSError:
                            # A configured archive may live on another filesystem from temp.
                            shutil.copyfile(original, pinned)
            output = work / "omi-backup.zip"
            with (
                closing(sqlite3.connect(snapshot)) as db,
                zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive,
            ):
                db.row_factory = sqlite3.Row
                archive.write(snapshot, "omi.sqlite3")
                for row in db.execute("SELECT id,digest,audio_available FROM audio"):
                    if not row["audio_available"]:
                        continue
                    data = (work / f"{row['id']}.wav").read_bytes()
                    if hashlib.sha256(data).hexdigest() != row["digest"]:
                        raise ValueError("Stored audio checksum mismatch; backup was not created")
                    archive.writestr(f"audio/{row['id']}.wav", data)
                    (work / f"{row['id']}.wav").unlink()
                # Render the snapshot's diary projection; live files may already be newer.
                for row in db.execute("SELECT day,content FROM diary"):
                    content = json.loads(row["content"])
                    markdown = (
                        f"# {row['day']}\n\n{content['summary']}\n\n## Transcript sources\n\n"
                    )
                    markdown += "\n".join(
                        f"- {source['started_at']} [{source['id']}] {source['text']}"
                        for source in content["sources"]
                    )
                    archive.writestr(f"diary/{row['day']}.md", markdown)
                for row in db.execute("SELECT id,content FROM weekly_versions"):
                    archive.writestr(
                        f"weekly/{row['id']}.md",
                        weekly_markdown(json.loads(row["content"]), stale=True),
                    )
            snapshot.unlink()
            return output
        except BaseException:
            shutil.rmtree(work)
            raise
