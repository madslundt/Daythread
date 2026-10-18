import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from .archive_store import EXTENDED_SCHEMA, ArchiveStore
from .completeness import SCHEMA as COMPLETENESS_SCHEMA
from .completeness import CompletenessStore
from .conversation_tags import SCHEMA as CONVERSATION_TAGS_SCHEMA
from .conversation_tags import ConversationTagStore, decorate_tags
from .decision_export import diary_markdown
from .decision_store import DECISION_SCHEMA, DecisionStore
from .domain import ButtonEvent, HintPolicy, stamp, utc_now
from .forwarding_store import SCHEMA as FORWARDING_SCHEMA
from .forwarding_store import ForwardingStore
from .importance import SCHEMA as IMPORTANCE_SCHEMA
from .importance import ImportanceStore
from .inbox_schema import SCHEMA as INBOX_SCHEMA
from .label_schema import SCHEMA as LABEL_SCHEMA
from .label_store import LabelStore
from .location_zones import SCHEMA as LOCATION_ZONES_SCHEMA
from .location_zones import LocationZoneStore, contains
from .locations import CaptureLocation
from .notification_store import SCHEMA as NOTIFICATION_SCHEMA
from .notification_store import NotificationStore
from .processing_progress import SCHEMA as PROCESSING_PROGRESS_SCHEMA
from .processing_progress import ProcessingProgressStore
from .recap_store import SCHEMA as RECAP_SCHEMA
from .recap_store import RecapStore
from .recording_topics import SCHEMA as RECORDING_TOPICS_SCHEMA
from .recording_uploads import SCHEMA as RECORDING_UPLOADS_SCHEMA
from .recording_uploads import RecordingUploadStore
from .semantic_store import SEMANTIC_SCHEMA, SemanticStore
from .source_evidence import SCHEMA as SOURCE_EVIDENCE_SCHEMA
from .source_matching import MATCH_VERSION, SourceMatchingStore, UploadGrouping
from .source_matching import SCHEMA as SOURCE_MATCHING_SCHEMA
from .speaker_store import SCHEMA as SPEAKER_SCHEMA
from .translation import SCHEMA as TRANSLATION_SCHEMA
from .translation import TranslationStore
from .vendor_imports import SCHEMA as VENDOR_IMPORT_SCHEMA
from .voice_schema import SCHEMA as VOICE_SCHEMA
from .weekly_store import SCHEMA as WEEKLY_SCHEMA

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


class Store(
    ConversationTagStore,
    LocationZoneStore,
    NotificationStore,
    ArchiveStore,
    CompletenessStore,
    DecisionStore,
    SemanticStore,
    LabelStore,
    ForwardingStore,
    ImportanceStore,
    TranslationStore,
    RecapStore,
    SourceMatchingStore,
    RecordingUploadStore,
    ProcessingProgressStore,
):
    def __init__(
        self, root: Path, *, database_url: str | None = None,
        database_schema: str = "public", storage=None,
    ):
        from .postgres import PostgresDatabase

        self.database = PostgresDatabase(database_url, database_schema) if database_url else None
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audio_dir = root / "audio"
        self.diary_dir = root / "diary"
        self.audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.diary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        from .blob_storage import LocalAudioStorage

        self.audio_storage = storage or LocalAudioStorage(self.audio_dir)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.executescript(CONVERSATION_TAGS_SCHEMA)
            db.executescript(LOCATION_ZONES_SCHEMA)
            db.executescript(FORWARDING_SCHEMA)
            db.executescript(EXTENDED_SCHEMA)
            db.executescript(COMPLETENESS_SCHEMA)
            db.executescript(PROCESSING_PROGRESS_SCHEMA)
            db.executescript(WEEKLY_SCHEMA)
            db.executescript(DECISION_SCHEMA)
            # Preserve existing event history when upgrading an already-used starter.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
            if "hint_policy" not in columns:
                db.execute("ALTER TABLE events ADD COLUMN hint_policy TEXT NOT NULL DEFAULT '{}'")
            audio_columns = {row["name"] for row in db.execute("PRAGMA table_info(audio)")}
            if "storage_bytes" not in audio_columns:
                db.execute("ALTER TABLE audio ADD COLUMN storage_bytes INTEGER NOT NULL DEFAULT 0")
            if "segments" not in audio_columns:
                db.execute("ALTER TABLE audio ADD COLUMN segments TEXT NOT NULL DEFAULT '[]'")
            if "transcription_details" not in audio_columns:
                db.execute(
                    "ALTER TABLE audio ADD COLUMN transcription_details TEXT NOT NULL DEFAULT '{}'"
                )
            if "topic_details" not in audio_columns:
                db.execute("ALTER TABLE audio ADD COLUMN topic_details TEXT NOT NULL DEFAULT '{}'")
            if "audio_available" not in audio_columns:
                db.execute(
                    "ALTER TABLE audio ADD COLUMN audio_available INTEGER NOT NULL DEFAULT 1"
                )
            if "source_metadata" not in audio_columns:
                db.execute(
                    "ALTER TABLE audio ADD COLUMN source_metadata TEXT NOT NULL DEFAULT '{}'"
                )
            for column in ("source_kind", "source_id"):
                if column not in audio_columns:
                    db.execute(f"ALTER TABLE audio ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            for column in ("received_at", "processing_started_at", "processed_at", "error_code"):
                if column not in audio_columns:
                    db.execute(f"ALTER TABLE audio ADD COLUMN {column} TEXT")
            if "transcription_outcome" not in audio_columns:
                db.execute(
                    "ALTER TABLE audio ADD COLUMN transcription_outcome "
                    "TEXT NOT NULL DEFAULT 'unknown'"
                )
            gap_columns = {row["name"] for row in db.execute("PRAGMA table_info(capture_gaps)")}
            if "cause" not in gap_columns:
                db.execute(
                    "ALTER TABLE capture_gaps ADD COLUMN cause TEXT NOT NULL DEFAULT 'unknown'"
                )
            if "source_version" not in audio_columns:
                db.execute("ALTER TABLE audio ADD COLUMN source_version TEXT NOT NULL DEFAULT ''")
            db.execute(
                "UPDATE audio SET source_version=lower(hex(randomblob(16))) WHERE source_version=''"
            )
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS source_insert_version AFTER INSERT ON audio
                WHEN NEW.source_version='' BEGIN
                  UPDATE audio SET source_version=lower(hex(randomblob(16))) WHERE id=NEW.id;
                END;
            """)
            if "transcript_version" not in audio_columns:
                db.execute(
                    "ALTER TABLE audio ADD COLUMN transcript_version TEXT NOT NULL DEFAULT ''"
                )
                db.execute("UPDATE audio SET transcript_version=lower(hex(randomblob(16)))")
            db.executescript(SEMANTIC_SCHEMA)
            db.executescript(TRANSLATION_SCHEMA)
            db.executescript(SPEAKER_SCHEMA)
            db.executescript(VOICE_SCHEMA)
            message_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(chat_messages)")
            }
            if "retrieval_details" not in message_columns:
                db.execute("ALTER TABLE chat_messages ADD COLUMN retrieval_details TEXT")
            db.executescript(INBOX_SCHEMA)
            db.executescript(LABEL_SCHEMA)
            db.executescript(IMPORTANCE_SCHEMA)
            db.executescript(RECAP_SCHEMA)
            db.executescript(NOTIFICATION_SCHEMA)
            db.executescript(RECORDING_TOPICS_SCHEMA)
            db.executescript(SOURCE_MATCHING_SCHEMA)
            db.executescript(RECORDING_UPLOADS_SCHEMA)
            db.executescript(VENDOR_IMPORT_SCHEMA)
            db.executescript(SOURCE_EVIDENCE_SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO upload_parts "
                "SELECT json_extract(source_metadata,'$.recording_id'),"
                "json_extract(source_metadata,'$.part_index'),"
                "json_extract(source_metadata,'$.part_count') FROM audio "
                "WHERE json_extract(source_metadata,'$.recording_id') IS NOT NULL"
            )
            for row in db.execute(
                "SELECT a.id FROM audio a LEFT JOIN source_match_checked m ON m.id=a.id "
                "WHERE m.id IS NULL OR m.version!=? || ':' || a.source_version",
                (MATCH_VERSION,),
            ).fetchall():
                self.refresh_source_matches(db, row["id"])
            session_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(chat_sessions)")
            }
            for column in ("day", "meeting_id", "start_day", "end_day"):
                if column not in session_columns:
                    db.execute(f"ALTER TABLE chat_sessions ADD COLUMN {column} TEXT")
            phone_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(phone_observations)")
            }
            if "historical" not in phone_columns:
                db.execute(
                    "ALTER TABLE phone_observations ADD COLUMN historical "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            # Backups can be restored under a different data directory.
            for row in ([] if self.database or self.audio_storage.is_remote else
                        db.execute("SELECT id,path FROM audio").fetchall()):
                if Path(row["path"]).parent != self.audio_dir:
                    db.execute("UPDATE phone_observations SET historical=1")
                db.execute(
                    "UPDATE audio SET path=? WHERE id=?",
                    (str(self.audio_dir / f"{row['id']}.wav"), row["id"]),
                )

    def read_audio(self, audio_id: str) -> bytes:
        return self.audio_storage.read(audio_id)

    def has_audio(self, audio_id: str) -> bool:
        return self.audio_storage.exists(audio_id)

    def storage_usage(self) -> int:
        with self.connect() as db:
            return db.execute(
                "SELECT COALESCE(SUM(storage_bytes),0) FROM audio WHERE audio_available=1"
            ).fetchone()[0]

    @contextmanager
    def connect(self):
        if self.database:
            db = self.database.connect()
            try:
                with db:
                    yield db
            finally:
                db.close()
            return
        db = sqlite3.connect(self.root / "omi.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function("location_zone_contains", 3, contains, deterministic=True)
        try:
            with db:
                yield db
        finally:
            db.close()

    def migrate_sqlite(self, path: Path) -> None:
        """Atomically copy an existing archive into an empty managed database."""
        if not self.database:
            raise ValueError("SQLite migration requires a PostgreSQL destination")
        with sqlite3.connect(path) as source, self.connect() as target:
            target.import_sqlite(source)

    def ingest(
        self, audio_id: UUID, device: str, start: datetime, data: bytes, duration: float,
        *, source_kind: str = "", source_id: str = "",
        source_metadata: dict | None = None,
    ) -> dict:
        key = str(audio_id)
        digest = hashlib.sha256(data).hexdigest()
        at = stamp(start)
        end = stamp(start + timedelta(seconds=duration))
        metadata = UploadGrouping.model_validate(source_metadata or {}).model_dump(
            exclude_none=True, mode="json"
        )
        if metadata.get("location"):
            if source_kind or device in {"web", "manual-web"}:
                raise ValueError("GPS observations apply only to phone capture")
            CaptureLocation.model_validate(metadata["location"]).validate_capture_time(start)
        if metadata.get("vendor_id") and source_kind != "manual_upload":
            raise ValueError("Vendor provenance applies to imported audio files")
        encoded_metadata = json.dumps(metadata, sort_keys=True)
        if metadata.get("recording_started_at") and not (
            datetime.fromisoformat(metadata["recording_started_at"])
            <= start + timedelta(milliseconds=1)
            and datetime.fromisoformat(metadata["recording_ended_at"])
            >= start + timedelta(seconds=duration, milliseconds=-1)
        ):
            raise ValueError("Audio part must fall within its recording bounds")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.validate_recording_upload(
                db, key, digest, device, source_kind, source_id, metadata, at,
            )
            old = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
            if old:
                if (
                    old["digest"], old["device_id"], old["started_at"],
                    old["source_kind"], old["source_id"], json.loads(old["source_metadata"]),
                ) != (digest, device, at, source_kind, source_id, metadata):
                    raise ValueError("Audio ID already exists with different content or metadata")
                # A matching resend can repair a missing file, but never reverse retention.
                if old["audio_available"] and not self.has_audio(key):
                    self.audio_storage.write(key, data)
                    db.execute("UPDATE audio SET storage_bytes=? WHERE id=?", (len(data), key))
                    if old["state"] == "blocked" and old["error_code"] == "missing_audio":
                        db.execute(
                            "UPDATE audio SET state='queued',retry_at='',error=NULL,"
                            "error_code=NULL "
                            "WHERE id=?",
                            (key,),
                        )
                    return dict(db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone())
                return dict(old)
            target = metadata.get("conversation_source_id")
            if target and not db.execute(
                "SELECT 1 FROM audio WHERE id=? AND state!='expired'", (target,),
            ).fetchone():
                raise ValueError("The selected conversation source is no longer available")
            if metadata.get("recording_id"):
                for other in db.execute(
                    "SELECT * FROM audio WHERE json_extract(source_metadata,'$.recording_id')=?",
                    (metadata["recording_id"],),
                ):
                    value = json.loads(other["source_metadata"])
                    if (other["device_id"] != device or other["source_id"] != source_id
                            or value.get("vendor_id") != metadata.get("vendor_id")
                            or value["part_count"] != metadata["part_count"]
                            or value.get("conversation_source_id") != target
                            or value.get("recording_started_at") !=
                            metadata.get("recording_started_at")
                            or value.get("recording_ended_at") != metadata.get("recording_ended_at")
                            or (value["part_index"] == metadata["part_index"] and
                                (other["digest"], other["started_at"], other["ended_at"]) !=
                                (digest, at, end))):
                        raise ValueError("Recording part conflicts with previously uploaded parts")
            # Each ID is a validated UUID, never a client-supplied filename.
            path = self.audio_storage.location(key)
            self.audio_storage.write(key, data)
            db.execute(
                "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,received_at,"
                "source_kind,source_id,source_metadata,storage_bytes) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key, device, at, end, digest, str(path), stamp(utc_now()), source_kind, source_id,
                 encoded_metadata, len(data)),
            )
            if metadata.get("recording_id"):
                db.execute(
                    "INSERT OR IGNORE INTO upload_parts VALUES(?,?,?)",
                    (metadata["recording_id"], metadata["part_index"], metadata["part_count"]),
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
                    "UPDATE audio SET state='transcribing',attempts=attempts+1,"
                    "processing_started_at=? WHERE id=?",
                    (stamp(utc_now()), row["id"]),
                )
                return dict(row)
        return None

    def complete(
        self,
        audio_id: str,
        text: str,
        segments: list[dict] | None = None,
        claimed: bool = False,
        outcome: str | None = None,
        details: dict | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM audio WHERE id=?", (audio_id,)).fetchone()
            if not row or (claimed and row["state"] != "transcribing"):
                return
            self.invalidate_decision_sources(db, [audio_id], purge=False)
            db.execute(
                "UPDATE audio SET state='done',text=?,segments=?,error=NULL,error_code=NULL,"
                "processed_at=?,transcription_outcome=?,transcription_details=?,"
                "source_version=lower(hex(randomblob(16))) "
                "WHERE id=?",
                (
                    text,
                    json.dumps(segments or []),
                    stamp(utc_now()),
                    outcome or ("text" if text else "empty_stt"),
                    json.dumps(details or {}),
                    audio_id,
                ),
            )
            db.execute("DELETE FROM transcript_search WHERE id=?", (audio_id,))
            db.execute("INSERT INTO transcript_search(id,text) VALUES(?,?)", (audio_id, text))
            self.refresh_source_matches(db, audio_id)

    def retry(self, row: dict, error: str, blocked: bool = False) -> None:
        delay = min(300, 2 ** min(row["attempts"] + 1, 9))
        with self.connect() as db:
            db.execute(
                "UPDATE audio SET state=?,retry_at=?,error=?,error_code=? "
                "WHERE id=? AND state='transcribing'",
                (
                    "blocked" if blocked else "queued",
                    stamp(utc_now() + timedelta(seconds=delay)),
                    error,
                    "missing_audio" if blocked else "provider_error",
                    row["id"],
                ),
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
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM audio WHERE id=?", (audio_id,)).fetchone()
            return decorate_tags(db, [dict(row)])[0] if row else None

    def get_audio_many(self, audio_ids: list[str]) -> dict[str, dict]:
        if not audio_ids:
            return {}
        with self.connect() as db:
            return {
                row["id"]: dict(row)
                for row in db.execute(
                    "SELECT * FROM audio WHERE id IN (" + ",".join("?" for _ in audio_ids) + ")",
                    audio_ids,
                )
            }

    def has_recordings(self, start: datetime, end: datetime) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM audio WHERE started_at<? AND ended_at>? LIMIT 1",
                    (stamp(end), stamp(start)),
                ).fetchone()
                is not None
            )

    def sample_transcripts(
        self, limit: int, start: datetime | None = None, end: datetime | None = None
    ) -> tuple[list[dict], int]:
        """Evenly sample chronological IDs before loading selected transcript bodies."""
        if limit < 1:
            raise ValueError("Sample limit must be positive")
        where = "state='done' AND text IS NOT NULL AND text!=''"
        args = ()
        if start and end:
            where += " AND started_at<? AND ended_at>?"
            args = (stamp(end), stamp(start))
        with self.connect() as db:
            # Count and selected IDs must describe the same snapshot during capture/corrections.
            db.execute("BEGIN")
            total = db.execute(
                "SELECT count(*) FROM canonical_audio WHERE " + where, args
            ).fetchone()[0]
            if not total:
                return [], 0
            size = min(limit, total)
            offsets = (
                [total - 1]
                if size == 1
                else [round(index * (total - 1) / (size - 1)) for index in range(size)]
            )
            rows = db.execute(
                "WITH ranked AS (SELECT id, row_number() OVER (ORDER BY started_at,id)-1 AS n "
                "FROM canonical_audio WHERE " + where + ") "
                "SELECT a.* FROM ranked r JOIN audio a ON a.id=r.id WHERE r.n IN ("
                + ",".join("?" for _ in offsets)
                + ") ORDER BY a.started_at,a.id",
                (*args, *offsets),
            )
            return [dict(row) for row in rows], total

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
        # FTS highlight uses the exact unicode61 matching/normalization rules, so accents
        # and whole tokens retain authoritative offsets into the original transcript.
        marker = f"<omi-match-{uuid4().hex}>"
        close = f"</omi-match-{uuid4().hex}>"
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.*,highlight(transcript_search,1,?,?) AS matched_text "
                "FROM transcript_search f JOIN canonical_audio a ON a.id=f.id "
                "WHERE transcript_search MATCH ?"
                + scope
                + " AND a.state='done' ORDER BY bm25(transcript_search),a.started_at,a.id LIMIT ?",
                (marker, close, *args),
            ).fetchall()
        result = []
        for row in rows:
            row = dict(row)
            highlighted = row.pop("matched_text")
            if marker not in row["text"] and close not in row["text"]:
                # Track authoritative original offsets across highlighted tokens. Keep a
                # bounded set so a repeated common name cannot crowd out the transcript tail.
                first, last, cursor, removed = {}, {}, 0, 0
                while (position := highlighted.find(marker, cursor)) >= 0:
                    ending = highlighted.find(close, position + len(marker))
                    if ending < 0:
                        break
                    term = highlighted[position + len(marker) : ending].casefold()
                    first.setdefault(term, position - removed)
                    last[term] = position - removed
                    removed += len(marker) + len(close)
                    cursor = ending + len(close)
                offsets = sorted(set(first.values()) | set(last.values()))
                if offsets:
                    row["lexical_offset"] = offsets[0]
                    row["lexical_offsets"] = offsets[:64]
            result.append(row)
        return result

    def contiguous_context(
        self,
        source: dict,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict]:
        """At most one prior/next local chunk; never cross scope, device or capture gaps."""
        if source.get("source_kind", ""):
            return []
        scope = " AND a.started_at<? AND a.ended_at>?" if start and end else ""
        scope_args = (stamp(end), stamp(start)) if start and end else ()
        result = []
        with self.connect() as db:
            for boundary, at in (
                ("ended_at", source["started_at"]),
                ("started_at", source["ended_at"]),
            ):
                row = db.execute(
                    "SELECT a.* FROM audio a WHERE a.device_id=? AND a.source_kind='' "
                    "AND a.state='done' AND length(trim(a.text))>0 AND a."
                    + boundary
                    + "=?"
                    + scope
                    + " AND NOT EXISTS (SELECT 1 FROM capture_gaps g "
                    "WHERE g.device_id=a.device_id AND g.started_at<max(a.ended_at,?) "
                    "AND g.ended_at>min(a.started_at,?)) ORDER BY a.id LIMIT 1",
                    (
                        source["device_id"],
                        at,
                        *scope_args,
                        source["ended_at"],
                        source["started_at"],
                    ),
                ).fetchone()
                if row:
                    result.append(dict(row))
        return result

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

    def save_diary(
        self, day: str, fingerprint: str, content: dict,
        guard: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        serialized = json.dumps(content, ensure_ascii=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if guard is not None:
                guard(db)
            self.verify_sources(db, content["sources"])
            if content.get("output_id") and content.get("evidence"):
                self._save_evidence(
                    db, content["output_id"], content["evidence"], content["sources"]
                )
            db.execute(
                "INSERT INTO diary_versions VALUES(?,?,?,?,?)",
                (
                    content.get("output_id") or str(uuid4()),
                    day,
                    fingerprint,
                    serialized,
                    stamp(utc_now()),
                ),
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
