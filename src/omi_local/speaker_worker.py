"""Durable, bounded automatic diarization for completed logical recordings."""

import asyncio
import logging
from datetime import timedelta

from fastapi import HTTPException

from .conversation_state import conversations, meeting_timeline
from .domain import stamp, utc_now
from .speakers import fingerprint

logger = logging.getLogger(__name__)


def discover(memory) -> int:
    if not memory.settings.diarization_enabled:
        return 0
    now = utc_now()
    added = 0
    with memory.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        snapshot = memory.store.recap_snapshot(db)
        timeline = meeting_timeline(snapshot, now)
        groups = conversations(snapshot, now, 5 * 60, timeline=timeline)
        for group in groups:
            if not group.ready or not group.sources:
                continue
            scope = memory.speakers.resolve(
                "source",
                group.sources[0]["id"],
                db,
                snapshot=snapshot,
                groups=groups,
                timeline=timeline,
            )
            if any(source["state"] != "done" for source in scope["rows"]):
                # resolve() can widen a single ready upload part to its
                # multipart recording; wait for every part before queueing it.
                continue
            if any(
                not source["audio_available"] or not memory.store.has_audio(source["id"])
                for source in scope["rows"]
            ):
                continue
            saved = memory.speakers.load(db, scope)
            # A stale or reviewed scope is left for explicit reprocessing. Never
            # replace confirmed names, merges, or manual corrections on retry.
            if saved is not None:
                continue
            fp = fingerprint(scope)
            cursor = db.execute(
                "INSERT INTO speaker_jobs(scope_key,fingerprint,state,updated_at) "
                "VALUES(?,?,'queued',?) ON CONFLICT(scope_key) DO UPDATE SET "
                "fingerprint=excluded.fingerprint,state='queued',attempts=0,retry_at=NULL,"
                "error=NULL,updated_at=excluded.updated_at "
                "WHERE speaker_jobs.fingerprint!=excluded.fingerprint "
                "AND speaker_jobs.state!='processing'",
                (scope["key"], fp, stamp(now)),
            )
            added += cursor.rowcount
            if cursor.rowcount:
                db.execute("DELETE FROM speaker_dependencies WHERE scope_key=?", (scope["key"],))
                db.executemany(
                    "INSERT OR IGNORE INTO speaker_dependencies VALUES(?,?)",
                    [(scope["key"], source["id"]) for source in scope["rows"]],
                )
    return added


async def process_one(memory) -> bool:
    if not memory.settings.diarization_enabled:
        return False
    with memory.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM speaker_jobs WHERE state IN ('queued','error') "
            "AND (retry_at IS NULL OR retry_at<=?) ORDER BY updated_at LIMIT 1",
            (stamp(utc_now()),),
        ).fetchone()
        if row is None:
            return False
        job = dict(row)
        db.execute(
            "UPDATE speaker_jobs SET state='processing',updated_at=? WHERE scope_key=?",
            (stamp(utc_now()), job["scope_key"]),
        )
    try:
        kind, key = job["scope_key"].split(":", 1)
        with memory.store.connect() as db:
            db.execute("BEGIN")
            scope = memory.speakers.resolve(kind, key, db)
            saved = memory.speakers.load(db, scope)
            current = fingerprint(scope)
            incomplete = any(r["state"] != "done" for r in scope["rows"])
        if saved is not None:
            _finish(memory, job["scope_key"], "done", None, None)
            return True
        if current != job["fingerprint"] or incomplete:
            _finish(memory, job["scope_key"], "queued", current, None)
            return True
        await memory.speakers.detect(kind, key)
        _finish(memory, job["scope_key"], "done", None, None)
    except Exception as exc:
        attempts = job["attempts"] + 1
        delay = min(3600, 15 * 2 ** min(attempts - 1, 8))
        reason = exc.detail if isinstance(exc, HTTPException) else type(exc).__name__
        _finish(
            memory,
            job["scope_key"],
            "error",
            None,
            stamp(utc_now() + timedelta(seconds=delay)),
            f"Speaker detection failed: {reason}",
            attempts,
        )
        logger.warning("Automatic speaker detection failed (%s)", type(exc).__name__)
    return True


def _finish(memory, key, state, fingerprint_value, retry_at, error=None, attempts=None):
    with memory.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if attempts is None:
            db.execute(
                "UPDATE speaker_jobs SET state=?,fingerprint=COALESCE(?,fingerprint),"
                "retry_at=?,error=?,updated_at=? WHERE scope_key=?",
                (state, fingerprint_value, retry_at, error, stamp(utc_now()), key),
            )
        else:
            db.execute(
                "UPDATE speaker_jobs SET state=?,attempts=?,retry_at=?,error=?,updated_at=? "
                "WHERE scope_key=?",
                (state, attempts, retry_at, error, stamp(utc_now()), key),
            )


async def run_speakers(memory):
    needs_recovery = True
    while True:
        if not memory.settings.diarization_enabled:
            # Profiles may deliberately omit a Store while all processing is
            # disabled. Avoid archive access until this task is enabled.
            needs_recovery = True
            await asyncio.sleep(0.5)
            continue
        try:
            if needs_recovery:
                with memory.store.connect() as db:
                    db.execute(
                        "UPDATE speaker_jobs SET state='queued',updated_at=? "
                        "WHERE state='processing'",
                        (stamp(utc_now()),),
                    )
                needs_recovery = False
            discover(memory)
            worked = await process_one(memory)
        except Exception as exc:
            logger.warning("Automatic speaker queue will retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.2 if worked else 5)
