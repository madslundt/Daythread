"""Explicit selection and exact-version reads for full-recording transcripts."""

import json
import sqlite3
from typing import Literal, TypedDict

from .domain import stamp, utc_now
from .merged_recordings import Recording, load_recording
from .recording_transcription_store import pipeline_fingerprint, source_fingerprint


class RecordingTranscriptSelection(TypedDict, total=False):
    state: Literal["unselected", "selected", "stale"]
    selection_version: int
    result_version: str
    input_fingerprint: str
    pipeline_fingerprint: str
    source_ids: list[str]
    transcript: dict | None


class RecordingTranscriptReference(TypedDict):
    id: str
    reference_type: Literal["recording_transcription"]
    recording_id: str
    result_version: str
    input_fingerprint: str
    pipeline_fingerprint: str
    selection_version: int
    source_ids: list[str]
    text: str


class SelectedRecordingVariant(TypedDict):
    recording: Recording
    variant: dict
    selection_version: int
    input_fingerprint: str
    pipeline_fingerprint: str
    source_ids: list[str]


def _selection_version(db: sqlite3.Connection, recording_id: str) -> int:
    existing = db.execute(
        "SELECT selection_version FROM recording_transcription_selections "
        "WHERE recording_id=?", (recording_id,),
    ).fetchone()
    return existing["selection_version"] if existing else 0


def selected_recording_transcription(
    store, recording: Recording, settings, db: sqlite3.Connection | None = None,
) -> RecordingTranscriptSelection:
    """Return the selected exact variant only while its source and pipeline are current."""
    if db is None:
        with store.connect() as connection:
            connection.execute("BEGIN")
            return selected_recording_transcription(store, recording, settings, connection)
    selection = db.execute(
        "SELECT * FROM recording_transcription_selections WHERE recording_id=?",
        (recording.id,),
    ).fetchone()
    if not selection:
        return {"state": "unselected", "selection_version": 0, "transcript": None}
    if not selection["result_version"]:
        return {
            "state": "unselected", "selection_version": selection["selection_version"],
            "transcript": None,
        }
    row = db.execute(
        "SELECT * FROM recording_transcription_jobs WHERE recording_id=? "
        "AND result_version=? AND input_fingerprint=? AND pipeline_fingerprint=? "
        "AND state='ready'",
        (recording.id, selection["result_version"], selection["input_fingerprint"],
         selection["pipeline_fingerprint"]),
    ).fetchone()
    current = bool(
        row
        and recording.complete
        and selection["input_fingerprint"] == source_fingerprint(recording)
        and selection["pipeline_fingerprint"] == pipeline_fingerprint(settings)
    )
    if not current:
        return {
            "state": "stale", "selection_version": selection["selection_version"],
            "result_version": selection["result_version"], "transcript": None,
        }
    variant = store._public_variant(dict(row), recording)
    return {
        "state": "selected", "selection_version": selection["selection_version"],
        "result_version": selection["result_version"],
        "input_fingerprint": selection["input_fingerprint"],
        "pipeline_fingerprint": selection["pipeline_fingerprint"],
        "source_ids": [part["source_id"] for part in variant["source_map"]],
        "transcript": variant,
    }


def select_recording_transcription(
    store,
    recording: Recording,
    settings,
    expected_version: int,
    result_version: str | None,
) -> RecordingTranscriptSelection:
    """Compare and set an exact current selection, or explicitly clear it."""
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = load_recording(store, recording.id, db)
        if source_fingerprint(current) != source_fingerprint(recording):
            raise ValueError("Recording changed; refresh before selecting a transcript")
        actual_version = _selection_version(db, recording.id)
        if actual_version != expected_version:
            raise ValueError("Transcript selection changed; refresh before retrying")
        next_version = actual_version + 1
        if result_version is None:
            db.execute(
                "INSERT INTO recording_transcription_selections VALUES(?,?,NULL,NULL,NULL,?) "
                "ON CONFLICT(recording_id) DO UPDATE SET "
                "selection_version=excluded.selection_version,result_version=NULL,"
                "input_fingerprint=NULL,pipeline_fingerprint=NULL,updated_at=excluded.updated_at",
                (recording.id, next_version, stamp(utc_now())),
            )
            return {"state": "unselected", "selection_version": next_version}
        if not recording.complete:
            raise ValueError("Recording is incomplete")
        fingerprint = source_fingerprint(current)
        pipeline = pipeline_fingerprint(settings)
        variant = db.execute(
            "SELECT 1 FROM recording_transcription_jobs WHERE recording_id=? "
            "AND result_version=? AND input_fingerprint=? AND pipeline_fingerprint=? "
            "AND state='ready'",
            (recording.id, result_version, fingerprint, pipeline),
        ).fetchone()
        if not variant:
            raise ValueError("Only a ready current transcript version can be selected")
        db.execute(
            "INSERT INTO recording_transcription_selections VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(recording_id) DO UPDATE SET "
            "selection_version=excluded.selection_version,result_version=excluded.result_version,"
            "input_fingerprint=excluded.input_fingerprint,"
            "pipeline_fingerprint=excluded.pipeline_fingerprint,updated_at=excluded.updated_at",
            (recording.id, next_version, result_version, fingerprint, pipeline, stamp(utc_now())),
        )
        return {
            "state": "selected", "selection_version": next_version,
            "result_version": result_version, "input_fingerprint": fingerprint,
            "pipeline_fingerprint": pipeline,
        }


def exact_recording_transcription(
    store,
    recording_id: str,
    result_version: str,
    settings,
    db: sqlite3.Connection | None = None,
) -> dict | None:
    """Fetch exact retained history for citations; never substitute a newer result."""
    if db is None:
        with store.connect() as connection:
            connection.execute("BEGIN")
            return exact_recording_transcription(
                store, recording_id, result_version, settings, connection,
            )
    row = db.execute(
        "SELECT * FROM recording_transcription_jobs WHERE recording_id=? "
        "AND result_version=? AND transcript IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
        (recording_id, result_version),
    ).fetchone()
    if not row:
        return None
    recording = load_recording(store, recording_id, db)
    current = (
        row["input_fingerprint"] == source_fingerprint(recording)
        and row["pipeline_fingerprint"] == pipeline_fingerprint(settings)
        and row["state"] == "ready"
    )
    value = store._public_variant(dict(row), recording, stale=not current)
    value.update({
        "id": recording_id,
        "reference_type": "recording_transcription",
        "recording_id": recording_id,
        "result_version": value["version"],
    })
    snapshot = recording.snapshot()
    value.update({key: snapshot[key] for key in (
        "started_at", "ended_at", "duration_seconds", "audio_available",
        "audio_availability", "title",
    )})
    if not current:
        # The transcript is retained for historical citations, but the current
        # recording may no longer contain the audio that produced this version.
        value["audio_available"] = False
    value["source_ids"] = [part["source_id"] for part in json.loads(row["source_map"] or "[]")]
    value["sections"] = [{
        "title": "Full recording transcript",
        "chapter_id": f"{recording_id}:transcript:{result_version}",
        "start_offset": 0,
        "end_offset": len(value.get("text") or ""),
    }]
    return value
