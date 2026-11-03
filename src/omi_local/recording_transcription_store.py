"""Versioned whole-recording transcription jobs, output and lifecycle cleanup."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import TYPE_CHECKING

from .domain import stamp, utc_now
from .merged_recordings import Recording
from .recording_transcription_client import DECODING_FIELDS

if TYPE_CHECKING:
    from .recording_transcription_selection import RecordingTranscriptSelection

SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_transcription_jobs (
 recording_id TEXT NOT NULL,
 input_fingerprint TEXT NOT NULL,
 pipeline_fingerprint TEXT NOT NULL,
 state TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 retry_at TEXT,
 phase TEXT,
 completed_parts INTEGER NOT NULL DEFAULT 0,
 total_parts INTEGER NOT NULL,
 error_code TEXT,
 result_version TEXT,
 transcript TEXT,
 native_segments TEXT NOT NULL DEFAULT '[]',
 word_segments TEXT NOT NULL DEFAULT '[]',
 provenance TEXT NOT NULL DEFAULT '{}',
 source_map TEXT NOT NULL DEFAULT '[]',
 alignment_status TEXT NOT NULL DEFAULT 'disabled',
 alignment_model TEXT,
 spool_path TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(recording_id,input_fingerprint,pipeline_fingerprint)
);
CREATE INDEX IF NOT EXISTS recording_transcription_due
 ON recording_transcription_jobs(state,retry_at,updated_at);
CREATE INDEX IF NOT EXISTS recording_transcription_recording
 ON recording_transcription_jobs(recording_id,updated_at);
CREATE TABLE IF NOT EXISTS recording_transcription_selections (
 recording_id TEXT PRIMARY KEY,
 selection_version INTEGER NOT NULL,
 result_version TEXT,
 input_fingerprint TEXT,
 pipeline_fingerprint TEXT,
 updated_at TEXT NOT NULL
);
"""

STATES = {"queued", "processing", "error", "blocked", "stale", "ready"}


def source_fingerprint(recording: Recording) -> str:
    fields = (
        "id",
        "digest",
        "source_version",
        "transcript_version",
        "state",
        "started_at",
        "ended_at",
        "transcription_outcome",
    )
    parts = []
    for row in recording.rows:
        metadata = json.loads(row.get("source_metadata") or "{}")
        parts.append(
            {key: row.get(key) for key in fields}
            | {
                key: metadata.get(key)
                for key in (
                    "recording_id",
                    "part_index",
                    "part_count",
                    "recording_started_at",
                    "recording_ended_at",
                )
            }
        )
    value = json.dumps(
        {"recording_id": recording.id, "parts": parts},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def pipeline_fingerprint(settings) -> str:
    value = {
        "pipeline": "whisper-cpp-full-recording-v2-cli-beam5",
        "endpoint": settings.recording_transcription_base_url,
        "model": "whisper",
        "model_sha256": settings.recording_transcription_expected_model_sha256,
        "model_snapshot": settings.recording_transcription_expected_model_snapshot,
        "runtime_commit": settings.recording_transcription_expected_runtime_commit,
        "decoding": DECODING_FIELDS,
        "prompt": None,
        "timeout_seconds": settings.recording_transcription_timeout_seconds,
        "max_seconds": settings.recording_transcription_max_seconds,
        "max_bytes": settings.recording_transcription_max_bytes,
        "alignment_enabled": settings.alignment_enabled,
        "alignment_base_url": settings.alignment_base_url if settings.alignment_enabled else None,
        "alignment_language": settings.alignment_language if settings.alignment_enabled else None,
        "alignment_timeout_seconds": (
            settings.alignment_timeout_seconds if settings.alignment_enabled else None
        ),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def recording_source_map(recording: Recording) -> list[dict]:
    offset = 0
    result = []
    for row, frames in zip(recording.rows, recording.frames):
        start = offset / 16000
        offset += frames
        metadata = json.loads(row.get("source_metadata") or "{}")
        result.append(
            {
                "source_id": row["id"],
                "part_index": metadata.get("part_index"),
                "part_count": metadata.get("part_count"),
                "start": start,
                "end": offset / 16000,
                "capture_start": row["started_at"],
                "capture_end": row["ended_at"],
            }
        )
    return result


def project_words(text: str, words: list[dict], source_map: list[dict]) -> list[dict]:
    """Preserve full text; map only exact aligned words contained in one original part."""
    output = []
    cursor = 0
    previous_end = 0.0
    for word in words:
        left, right = word.get("start_offset"), word.get("end_offset")
        start, end = word.get("start"), word.get("end")
        token = word.get("text")
        if (
            type(left) is not int
            or type(right) is not int
            or not 0 <= left < right <= len(text)
            or text[left:right] != token
            or left < cursor
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < previous_end
            or end <= start
        ):
            continue
        if left > cursor:
            output.append(
                {
                    "text": text[cursor:left],
                    "start_offset": cursor,
                    "end_offset": left,
                    "start": None,
                    "end": None,
                    "source_id": None,
                    "source_start": None,
                    "source_end": None,
                    "candidate_source_ids": [],
                    "kind": "unknown",
                }
            )
        overlapping = [part for part in source_map if start < part["end"] and end > part["start"]]
        owner = next(
            (part for part in overlapping if start >= part["start"] and end <= part["end"]), None
        )
        output.append(
            {
                "text": token,
                "start_offset": left,
                "end_offset": right,
                "start": start,
                "end": end,
                "source_id": owner["source_id"] if owner else None,
                "source_start": start - owner["start"] if owner else None,
                "source_end": end - owner["start"] if owner else None,
                "candidate_source_ids": (
                    [] if owner else [part["source_id"] for part in overlapping]
                ),
                "kind": "word" if owner else "boundary_word",
            }
        )
        cursor = right
        previous_end = end
    if cursor < len(text):
        output.append(
            {
                "text": text[cursor:],
                "start_offset": cursor,
                "end_offset": len(text),
                "start": None,
                "end": None,
                "source_id": None,
                "source_start": None,
                "source_end": None,
                "candidate_source_ids": [],
                "kind": "unknown",
            }
        )
    return output


class RecordingTranscriptionStore:
    def ensure_schema(self) -> None:
        with self.connect() as db:
            db.executescript(SCHEMA)

    def enqueue_recording_transcription(
        self,
        recording_id: str,
        input_fingerprint: str,
        pipeline: str,
        total_parts: int,
        now: datetime | None = None,
    ) -> int:
        now = now or utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT OR IGNORE INTO recording_transcription_jobs "
                "(recording_id,input_fingerprint,pipeline_fingerprint,state,total_parts,created_at,"
                "updated_at) VALUES(?,?,?,'queued',?,?,?)",
                (recording_id, input_fingerprint, pipeline, total_parts, stamp(now), stamp(now)),
            )
            if cursor.rowcount:
                db.execute(
                    "UPDATE recording_transcription_jobs SET state='stale',retry_at=NULL,"
                    "error_code='source_changed',updated_at=? WHERE recording_id=? "
                    "AND (input_fingerprint!=? OR pipeline_fingerprint!=?) "
                    "AND state IN ('queued','error')",
                    (stamp(now), recording_id, input_fingerprint, pipeline),
                )
            return cursor.rowcount

    @staticmethod
    def delete_recording_transcriptions(db, recording_id: str | None) -> None:
        if recording_id:
            db.execute(
                "DELETE FROM recording_transcription_jobs WHERE recording_id=?",
                (recording_id,),
            )
            db.execute(
                "DELETE FROM recording_transcription_selections WHERE recording_id=?",
                (recording_id,),
            )

    def recording_transcription_view(self, recording: Recording, settings, db=None) -> dict:
        current_fingerprint = source_fingerprint(recording)
        current_pipeline = pipeline_fingerprint(settings)
        if db is None:
            with self.connect() as connection:
                return self.recording_transcription_view(recording, settings, connection)
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM recording_transcription_jobs WHERE recording_id=? "
                "ORDER BY updated_at DESC,created_at DESC,pipeline_fingerprint",
                (recording.id,),
            )
        ]
        current = next(
            (
                row
                for row in rows
                if row["input_fingerprint"] == current_fingerprint
                and row["pipeline_fingerprint"] == current_pipeline
            ),
            None,
        )
        ready = next((row for row in rows if row["result_version"]), None)
        if current:
            value = self._public_variant(current, recording)
            if (
                ready
                and ready is not current
                and ready["result_version"] != current["result_version"]
            ):
                value["previous"] = self._public_variant(ready, recording, stale=True)
            return value
        if ready:
            return self._public_variant(ready, recording, stale=True)
        if not settings.recording_transcription_enabled:
            return {"state": "disabled", "version": None}
        if not recording.complete:
            return {"state": "waiting_parts", "version": None}
        if any(row["state"] != "done" for row in recording.rows):
            return {"state": "waiting_transcripts", "version": None}
        if any(row.get("transcription_outcome") == "corrected" for row in recording.rows):
            return {"state": "review_required", "version": None}
        if not recording.audio_available:
            return {"state": "audio_unavailable", "version": None}
        if (
            not settings.worker_enabled
            or not settings.background_processing_enabled
            or not settings.stt_enabled
        ):
            return {"state": "not_scheduled", "version": None}
        return {"state": "queued", "version": None}

    def selected_recording_transcription(
        self, recording: Recording, settings, db=None,
    ) -> RecordingTranscriptSelection:
        from .recording_transcription_selection import selected_recording_transcription

        return selected_recording_transcription(self, recording, settings, db)

    def select_recording_transcription(
        self, recording: Recording, settings, expected_version: int,
        result_version: str | None,
    ) -> RecordingTranscriptSelection:
        from .recording_transcription_selection import select_recording_transcription

        return select_recording_transcription(
            self, recording, settings, expected_version, result_version,
        )

    def exact_recording_transcription(
        self, recording_id: str, result_version: str, settings, db=None,
    ):
        from .recording_transcription_selection import exact_recording_transcription

        return exact_recording_transcription(self, recording_id, result_version, settings, db)

    def _public_variant(self, row: dict, recording: Recording, *, stale=False) -> dict:
        if row["result_version"]:
            text = row["transcript"] or ""
            source_map = json.loads(row["source_map"] or "[]")
            words = json.loads(row["word_segments"] or "[]")
            projection = project_words(text, words, source_map)
            provenance = json.loads(row["provenance"] or "{}")
        else:
            text, source_map, projection, provenance = None, [], [], {}
        return {
            "state": "stale" if stale else row["state"],
            "version": row["result_version"],
            "input_fingerprint": row["input_fingerprint"],
            "pipeline_fingerprint": row["pipeline_fingerprint"],
            "source_ids": [part["source_id"] for part in source_map],
            "text": text,
            "native_segments": json.loads(row["native_segments"] or "[]"),
            "word_segments": json.loads(row["word_segments"] or "[]"),
            "projection": projection,
            "source_map": source_map,
            "provenance": provenance,
            "alignment_status": row["alignment_status"],
            "alignment_model": row["alignment_model"],
            "processing": (
                {
                    "phase": row["phase"],
                    "completed_parts": row["completed_parts"],
                    "total_parts": row["total_parts"],
                    "attempts": row["attempts"],
                    "retry_at": row["retry_at"],
                    "error_code": row["error_code"],
                }
                if not row["result_version"]
                else None
            ),
            "audio_available": recording.audio_available,
            "stale": stale,
        }
