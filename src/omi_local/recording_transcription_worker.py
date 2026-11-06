"""Durable opt-in transcription of complete explicit manual recordings."""

import asyncio
import hashlib
import json
import logging
from datetime import timedelta

import httpx

from .domain import stamp, utc_now
from .merged_recordings import load_recording
from .recording_transcription_client import (
    SPOOL_DIR,
    _request,
    _spool_wav,
    _validate_response,
)
from .recording_transcription_store import (
    pipeline_fingerprint,
    recording_source_map,
    recording_transcription_result_version,
    source_fingerprint,
)
from .source_matching import MANUAL_UPLOAD_SQL
from .stt_languages import language_code

logger = logging.getLogger(__name__)


def _manual_recording_ids(store) -> list[str]:
    with store.connect() as db:
        return [
            row[0]
            for row in db.execute(
                "SELECT DISTINCT json_extract(source_metadata,'$.recording_id') FROM audio "
                "WHERE json_extract(source_metadata,'$.recording_id') IS NOT NULL AND "
                + MANUAL_UPLOAD_SQL
                + " ORDER BY 1",
            )
        ]


def discover(memory) -> int:
    settings, store = memory.settings, memory.store
    if (
        not settings.recording_transcription_enabled
        or not settings.worker_enabled
        or not settings.background_processing_enabled
        or not settings.stt_enabled
    ):
        return 0
    pipeline = pipeline_fingerprint(settings)
    added = 0
    for recording_id in _manual_recording_ids(store):
        try:
            recording = load_recording(store, recording_id)
        except Exception:
            continue
        if (
            not recording.complete
            or not recording.audio_available
            or any(row["state"] != "done" for row in recording.rows)
            or any(row.get("transcription_outcome") == "corrected" for row in recording.rows)
        ):
            continue
        if sum(recording.frames) / 16000 > settings.recording_transcription_max_seconds:
            store.enqueue_recording_transcription(
                recording_id,
                source_fingerprint(recording),
                pipeline,
                len(recording.rows),
            )
            _set_state(
                store,
                recording_id,
                source_fingerprint(recording),
                pipeline,
                "blocked",
                "duration_cap",
                "validating",
            )
            continue
        if 44 + sum(recording.frames) * 2 > settings.recording_transcription_max_bytes:
            store.enqueue_recording_transcription(
                recording_id,
                source_fingerprint(recording),
                pipeline,
                len(recording.rows),
            )
            _set_state(
                store,
                recording_id,
                source_fingerprint(recording),
                pipeline,
                "blocked",
                "byte_cap",
                "validating",
            )
            continue
        added += store.enqueue_recording_transcription(
            recording_id,
            source_fingerprint(recording),
            pipeline,
            len(recording.rows),
        )
    return added


def _set_state(
    store, recording_id, source, pipeline, state, error=None, phase=None, *, db=None, **extra
):
    values = {
        "state": state,
        "error_code": error,
        "phase": phase,
        "retry_at": None,
        "updated_at": stamp(utc_now()),
        **extra,
    }
    assignments = ",".join(f"{key}=?" for key in values)
    sql = (
        f"UPDATE recording_transcription_jobs SET {assignments} "
        "WHERE recording_id=? AND input_fingerprint=? AND pipeline_fingerprint=?"
    )
    args = (*values.values(), recording_id, source, pipeline)
    if db is not None:
        db.execute(sql, args)
    else:
        with store.connect() as connection:
            connection.execute(sql, args)


def _claim(store):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        now = stamp(utc_now())
        row = db.execute(
            "SELECT jobs.* FROM recording_transcription_jobs AS jobs "
            "LEFT JOIN (SELECT json_extract(source_metadata,'$.recording_id') AS recording_id, "
            "MAX(ended_at) AS capture_end FROM audio "
            "WHERE json_extract(source_metadata,'$.recording_id') IS NOT NULL "
            "GROUP BY json_extract(source_metadata,'$.recording_id')) AS capture "
            "ON capture.recording_id=jobs.recording_id "
            "WHERE jobs.state IN ('queued','error') "
            "AND (jobs.retry_at IS NULL OR jobs.retry_at<=?) "
            "ORDER BY COALESCE(capture.capture_end,'') DESC,jobs.recording_id LIMIT 1",
            (now,),
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE recording_transcription_jobs SET state='processing',phase='validating',"
            "attempts=attempts+1,retry_at=NULL,error_code=NULL,updated_at=? "
            "WHERE recording_id=? AND input_fingerprint=? AND pipeline_fingerprint=?",
            (now, row["recording_id"], row["input_fingerprint"], row["pipeline_fingerprint"]),
        )
        return dict(row) | {"attempts": row["attempts"] + 1}


def recover(store) -> None:
    spool = store.root / SPOOL_DIR
    if spool.exists():
        for path in spool.glob("*.wav"):
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not remove interrupted recording spool file")
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET state='queued',phase=NULL,spool_path=NULL,"
            "retry_at=NULL,updated_at=? WHERE state='processing'",
            (stamp(utc_now()),),
        )


async def process_one(memory) -> bool:
    settings, store = memory.settings, memory.store
    job = _claim(store)
    if job is None:
        return False
    rid, source, pipeline = (
        job["recording_id"],
        job["input_fingerprint"],
        job["pipeline_fingerprint"],
    )
    path = None
    try:
        recording = load_recording(store, rid)
        if (
            not recording.complete
            or not recording.audio_available
            or any(row["state"] != "done" for row in recording.rows)
            or any(row.get("transcription_outcome") == "corrected" for row in recording.rows)
            or source_fingerprint(recording) != source
        ):
            _set_state(store, rid, source, pipeline, "stale", "source_changed", "validating")
            return True
        if pipeline_fingerprint(settings) != pipeline:
            _set_state(store, rid, source, pipeline, "stale", "pipeline_changed", "validating")
            return True
        spool = store.root / SPOOL_DIR
        path = spool / (hashlib.sha256(f"{rid}:{source}:{pipeline}".encode()).hexdigest() + ".wav")
        _set_state(
            store, rid, source, pipeline, "processing", phase="assembling", spool_path=str(path)
        )

        def progress(done, total):
            _set_state(
                store,
                rid,
                source,
                pipeline,
                "processing",
                phase="assembling",
                completed_parts=done,
                total_parts=total,
            )

        _, frames = await asyncio.to_thread(
            _spool_wav,
            store,
            recording,
            path,
            settings.recording_transcription_max_bytes,
            settings.recording_transcription_max_seconds,
            progress,
        )
        _set_state(store, rid, source, pipeline, "processing", phase="transcribing")
        headers, payload = await _request(settings, path)
        text, native, provenance = _validate_response(headers, payload, settings, frames / 16000)
        words, alignment = [], {"status": "disabled", "model": None}
        if settings.recording_transcription_alignment_enabled and native:
            detected = payload.get("language")
            language = language_code(detected) if isinstance(detected, str) else None
            if language and len(language) > 32:
                language = None
            _set_state(store, rid, source, pipeline, "processing", phase="aligning")
            audio_bytes = path.read_bytes()
            try:
                words, alignment = await memory.inference._align_chunks(
                    [(0, audio_bytes, text, language, 0, native)],
                    settings,
                    settings.alignment_timeout_seconds,
                )
                words = words or []
            except Exception as exc:
                # Older alignment integrations may not yet accept native envelopes.
                status = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
                words, alignment = [], {"status": status, "model": None}
        source_map = recording_source_map(recording)
        version = recording_transcription_result_version(
            source, pipeline, text, native, words, provenance,
        )
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = load_recording(store, rid, db)
            if (
                not current.complete
                or not current.audio_available
                or any(row["state"] != "done" for row in current.rows)
                or any(row.get("transcription_outcome") == "corrected" for row in current.rows)
                or source_fingerprint(current) != source
                or pipeline_fingerprint(settings) != pipeline
            ):
                _set_state(
                    store, rid, source, pipeline, "stale", "source_changed", "publishing", db=db
                )
                return True
            db.execute(
                "UPDATE recording_transcription_jobs SET state='ready',phase='ready',"
                "transcript=?,native_segments=?,word_segments=?,provenance=?,source_map=?,"
                "alignment_status=?,alignment_model=?,result_version=?,error_code=NULL,"
                "spool_path=NULL,updated_at=? WHERE recording_id=? AND input_fingerprint=? "
                "AND pipeline_fingerprint=?",
                (
                    text,
                    json.dumps(native),
                    json.dumps(words),
                    json.dumps(provenance),
                    json.dumps(source_map),
                    alignment.get("status", "unavailable"),
                    alignment.get("model"),
                    version,
                    stamp(utc_now()),
                    rid,
                    source,
                    pipeline,
                ),
            )
        return True
    except ValueError as exc:
        code = (
            str(exc)
            if str(exc)
            in {
                "duration_cap",
                "byte_cap",
                "invalid_audio",
                "response_cap",
                "provenance_mismatch",
                "invalid_response",
                "invalid_segments",
            }
            else "invalid_response"
        )
        _set_state(store, rid, source, pipeline, "blocked", code, "error")
        return True
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if 400 <= status < 500 and status not in {408, 425, 429}:
            _set_state(store, rid, source, pipeline, "blocked", "provider_refused", "error")
        else:
            _retry(store, job, "provider_unavailable")
        return True
    except Exception as exc:
        logger.warning("Whole-recording transcription will retry (%s)", type(exc).__name__)
        _retry(store, job, "provider_unavailable")
        return True
    finally:
        if path:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove private recording spool file")


def _retry(store, job, error):
    delay = min(3600, 30 * (2 ** min(job["attempts"] - 1, 7)))
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET state='error',phase='retry_wait',"
            "error_code=?,retry_at=?,spool_path=NULL,updated_at=? WHERE recording_id=? "
            "AND input_fingerprint=? AND pipeline_fingerprint=?",
            (
                error,
                stamp(utc_now() + timedelta(seconds=delay)),
                stamp(utc_now()),
                job["recording_id"],
                job["input_fingerprint"],
                job["pipeline_fingerprint"],
            ),
        )


async def run_recording_transcription(memory) -> None:
    store = memory.store
    recover(store)
    while True:
        try:
            if (
                memory.settings.recording_transcription_enabled
                and memory.settings.worker_enabled
                and memory.settings.background_processing_enabled
                and memory.settings.stt_enabled
            ):
                discover(memory)
                worked = await process_one(memory)
            else:
                worked = False
        except Exception as exc:
            logger.warning("Whole-recording transcription worker retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.2 if worked else 5)
