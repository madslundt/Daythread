import asyncio
import json
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from .audio import contains_speech_energy
from .decision_service import extract
from .decisions import ExtractionRequest
from .domain import utc_now, wav_duration
from .inbox_jobs import InboxJobs
from .inference import Inference
from .recording_topics import run_topics
from .service import Memory
from .source_matching import recognition_contract
from .voice_activity import analyze_voice

logger = logging.getLogger(__name__)


async def process_one(memory: Memory) -> bool:
    row = memory.store.claim()
    if row is None:
        return False
    try:
        memory.store.start_processing(row["id"])
        contract = recognition_contract(memory.settings)
        duplicate = memory.store.reusable_transcription(row, contract)
        if duplicate:
            memory.store.complete(
                row["id"], duplicate["text"], json.loads(duplicate["segments"]), claimed=True,
                outcome=duplicate["transcription_outcome"],
                details=json.loads(duplicate["transcription_details"]),
            )
            return True
        data = await asyncio.to_thread(memory.store.read_audio, row["id"])
        voice = await asyncio.to_thread(analyze_voice, data)
        memory.store.save_recap_activity(row["id"], row["digest"], voice)
        threshold = (
            None if memory.settings.stt_preserve_quiet else memory.settings.silence_threshold
        )
        if not contains_speech_energy(data, threshold):
            filtering = {
                "original_seconds": wav_duration(data), "retained_seconds": 0,
                "detected_speech_seconds": 0 if threshold is None else None,
                "status": "filtered" if threshold is None else "legacy_gate",
            }
            if isinstance(memory.inference, Inference):
                memory.inference.record_usage({
                    "kind": "filter", "recording_id": row["id"],
                    "filter_status": filtering["status"], **filtering,
                })
            memory.store.complete(
                row["id"], "", claimed=True, outcome="silence_gate",
                details={"recognition_contract": contract, "filtering": filtering},
            )
        elif hasattr(memory.inference, "transcribe_result"):
            if isinstance(memory.inference, Inference):
                result = await memory.inference.transcribe_result(
                    data, on_progress=lambda stage, completed, total:
                    memory.store.update_processing(row["id"], stage, completed, total),
                    recording_id=row["id"],
                    voice_activity=voice,
                )
            else:
                memory.store.update_processing(row["id"], "transcribing")
                result = await memory.inference.transcribe_result(data)
            memory.store.update_processing(row["id"], "saving")
            memory.store.complete(
                row["id"], result["text"], result["segments"], claimed=True,
                details={**(result.get("processing") or {}), "recognition_contract": contract},
                outcome="silence_gate" if not result.get("processing", {}).get("chunks", [True])
                else None,
            )
            memory.store.finish_processing(row["id"])
        else:
            memory.store.update_processing(row["id"], "transcribing")
            text = await memory.inference.transcribe(data)
            memory.store.update_processing(row["id"], "saving")
            memory.store.complete(row["id"], text, claimed=True,
                                  details={"recognition_contract": contract})
            memory.store.finish_processing(row["id"])
    except Exception as exc:
        # Avoid putting transcripts, credentials, URLs or provider bodies into logs.
        memory.store.retry(
            row,
            f"Transcription failed: {type(exc).__name__}",
            blocked=isinstance(exc, FileNotFoundError),
        )
        logger.warning("Audio transcription will retry (%s)", type(exc).__name__)
    return True


def recorded_days(memory: Memory, now: datetime) -> list[date]:
    """Include historical and cross-midnight arrivals in the archive's timezone."""
    days: set[date] = set()
    for row in memory.store.recorded_intervals():
        first = datetime.fromisoformat(row["started_at"]).astimezone(now.tzinfo).date()
        last = datetime.fromisoformat(row["ended_at"]) - timedelta(microseconds=1)
        last = last.astimezone(now.tzinfo).date()
        while first <= min(last, now.date()):
            days.add(first)
            first += timedelta(days=1)
    return sorted(days)


async def scheduled_recaps(memory: Memory) -> None:
    now = utc_now().astimezone(ZoneInfo(memory.settings.timezone))
    await memory.recaps.observe()
    groups = memory.recaps.groups(now)
    for day in recorded_days(memory, now):
        try:
            saved = memory.store.diary(day.isoformat())
            if saved and saved["fingerprint"] == memory.recaps.day_key(
                memory.recaps.for_day(groups, day)
            ):
                continue
            await memory.recap(day)
        except Exception as exc:
            logger.warning("Automatic recap will retry (%s)", type(exc).__name__)


async def scheduled_decisions(memory: Memory) -> None:
    now = utc_now().astimezone(ZoneInfo(memory.settings.timezone))
    for day in recorded_days(memory, now):
        try:
            # Reuses the current snapshot and advances one bounded batch each pass.
            # Completed snapshots cost no inference; changed transcripts start a new one.
            await extract(memory, ExtractionRequest(request_id=uuid4(), day=day), automatic=True)
        except Exception as exc:
            logger.warning("Automatic decisions will retry (%s)", type(exc).__name__)


async def run_insights(memory: Memory) -> None:
    while True:
        for update in (scheduled_recaps, scheduled_decisions):
            try:
                await update(memory)
            except Exception as exc:
                logger.warning("Automatic insights will retry (%s)", type(exc).__name__)
        await asyncio.sleep(5)


async def maintenance(memory: Memory) -> None:
    while True:
        try:
            cleanup = asyncio.create_task(asyncio.to_thread(
                memory.store.apply_retention,
                memory.settings.audio_retention_days, memory.settings.transcript_retention_days,
            ))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Join in-flight storage work before Profiles closes database pools.
                # Cancelling to_thread itself would leave its mutations running.
                with suppress(Exception):
                    await cleanup
                raise
        except Exception as exc:
            logger.warning("Diary maintenance will retry (%s)", type(exc).__name__)
        await asyncio.sleep(60)


async def run_worker(memory: Memory) -> None:
    upkeep = asyncio.create_task(maintenance(memory))
    inbox = asyncio.create_task(run_inbox(memory))
    topics = asyncio.create_task(run_topics(memory))
    insights = asyncio.create_task(run_insights(memory))
    try:
        while True:
            try:
                worked = await process_one(memory)
            except Exception as exc:
                logger.warning("Audio worker will retry (%s)", type(exc).__name__)
                worked = False
            await asyncio.sleep(0.1 if worked else 2)
    finally:
        insights.cancel()
        with suppress(asyncio.CancelledError):
            await insights
        topics.cancel()
        with suppress(asyncio.CancelledError):
            await topics
        inbox.cancel()
        with suppress(asyncio.CancelledError):
            await inbox
        upkeep.cancel()
        with suppress(asyncio.CancelledError):
            await upkeep


async def run_inbox(memory: Memory) -> None:
    jobs = InboxJobs(memory.store)
    with memory.store.connect() as db:
        db.execute("UPDATE inbox_jobs SET state='queued',claim=NULL WHERE state='running'")
    while True:
        try:
            jobs.discover(
                memory.settings.timezone,
                model=memory.settings.llm_route("extraction").cache_key,
                settled_before=utc_now() - timedelta(seconds=30),
            )
            worked = await jobs.process_one(memory.inference)
        except Exception as exc:
            logger.warning("Inbox worker will retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.2 if worked else 3)
