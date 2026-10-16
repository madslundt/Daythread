import asyncio
import json
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .audio import contains_speech_energy
from .domain import utc_now
from .inbox_jobs import InboxJobs
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
        contract = recognition_contract(memory.settings)
        duplicate = memory.store.reusable_transcription(row, contract)
        if duplicate:
            memory.store.complete(
                row["id"], duplicate["text"], json.loads(duplicate["segments"]), claimed=True,
                outcome=duplicate["transcription_outcome"],
                details=json.loads(duplicate["transcription_details"]),
            )
            return True
        data = await asyncio.to_thread(Path(row["path"]).read_bytes)
        voice = await asyncio.to_thread(analyze_voice, data)
        memory.store.save_recap_activity(row["id"], row["digest"], voice)
        threshold = (
            None if memory.settings.stt_preserve_quiet else memory.settings.silence_threshold
        )
        if not contains_speech_energy(data, threshold):
            memory.store.complete(row["id"], "", claimed=True, outcome="silence_gate",
                                  details={"recognition_contract": contract})
        elif hasattr(memory.inference, "transcribe_result"):
            result = await memory.inference.transcribe_result(data)
            memory.store.complete(
                row["id"], result["text"], result["segments"], claimed=True,
                details={**(result.get("processing") or {}), "recognition_contract": contract},
            )
        else:
            memory.store.complete(row["id"], await memory.inference.transcribe(data), claimed=True,
                                  details={"recognition_contract": contract})
    except Exception as exc:
        # Avoid putting transcripts, credentials, URLs or provider bodies into logs.
        memory.store.retry(
            row,
            f"Transcription failed: {type(exc).__name__}",
            blocked=isinstance(exc, FileNotFoundError),
        )
        logger.warning("Audio transcription will retry (%s)", type(exc).__name__)
    return True


async def scheduled_recaps(memory: Memory) -> None:
    now = utc_now().astimezone(ZoneInfo(memory.settings.timezone))
    async with memory.recap_lock:
        await memory.recaps.prepare(now)
    latest = (
        now.date()
        if memory.settings.recap_interval_minutes or now.hour >= memory.settings.recap_hour
        else now.date() - timedelta(days=1)
    )
    days: set[date] = set()
    for row in memory.store.recorded_intervals():
        first = datetime.fromisoformat(row["started_at"]).astimezone(now.tzinfo).date()
        last = datetime.fromisoformat(row["ended_at"]) - timedelta(microseconds=1)
        last = last.astimezone(now.tzinfo).date()
        while first <= min(last, latest):
            days.add(first)
            first += timedelta(days=1)
    for day in sorted(days):
        if day == now.date() and now.hour < memory.settings.recap_hour:
            saved = memory.store.diary(day.isoformat())
            if saved:
                generated = datetime.fromisoformat(saved["created_at"])
                if (now - generated).total_seconds() < memory.settings.recap_interval_minutes * 60:
                    continue
        await memory.recap(day)


async def maintenance(memory: Memory) -> None:
    while True:
        try:
            memory.store.apply_retention(
                memory.settings.audio_retention_days, memory.settings.transcript_retention_days
            )
            await scheduled_recaps(memory)
        except Exception as exc:
            logger.warning("Diary maintenance will retry (%s)", type(exc).__name__)
        await asyncio.sleep(60)


async def run_worker(memory: Memory) -> None:
    upkeep = asyncio.create_task(maintenance(memory))
    inbox = asyncio.create_task(run_inbox(memory))
    topics = asyncio.create_task(run_topics(memory))
    try:
        while True:
            try:
                worked = await process_one(memory)
            except Exception as exc:
                logger.warning("Audio worker will retry (%s)", type(exc).__name__)
                worked = False
            await asyncio.sleep(0.1 if worked else 2)
    finally:
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
