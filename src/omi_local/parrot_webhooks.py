"""Signed Parrot meeting JSON, normalized as original transcript evidence.

Sender contract (Parrot 0.29.10): turantekin/Parrot at c3510636,
Parrot/Services/Integrations.swift. Transcript times are floored start offsets,
not complete segment intervals. Reports, notes and calendar people are not speech.
"""

import asyncio
import hashlib
import hmac
import re
import sqlite3
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from .domain import stamp
from .service import public_source
from .vendor_imports import TranscriptArchive, TranscriptSegment, VendorPage, VendorRecording

PATH = "/v1/integrations/parrot/webhook"
MAX_BYTES = 16_000_000
NAMESPACE = "https://openparrot.app/meetings"


class ParrotTurn(BaseModel):
    time: str = Field(pattern=r"^[0-9]{2,6}:[0-5][0-9]$")
    speaker: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=20000)


class ParrotMeeting(BaseModel):
    id: UUID
    date: AwareDatetime
    duration_seconds: int = Field(strict=True, gt=0, le=7 * 86400)
    transcript: list[ParrotTurn] | None = Field(default=None, max_length=10000)


class ParrotEvent(BaseModel):
    event: Literal["meeting.finished", "test"]
    sent_at: AwareDatetime
    meeting: ParrotMeeting | None = None


def normalize_meeting(meeting: ParrotMeeting) -> VendorRecording:
    if not meeting.transcript:
        raise HTTPException(422, "Enable Include the full transcript in Parrot's webhook settings")
    segments = []
    text = []
    for turn in meeting.transcript:
        minutes, seconds = map(int, turn.time.split(":"))
        start = minutes * 60 + seconds
        if (not turn.text.strip() or "\x00" in turn.text or start > meeting.duration_seconds + 1
                or (segments and start < segments[-1].start)):
            raise ValueError("Invalid Parrot transcript turn")
        # Point timestamps preserve the supplied starts without fabricating end times.
        segments.append(TranscriptSegment(
            start=start, end=start, text=turn.text, speaker_name=turn.speaker or None,
            speaker_id=turn.speaker or None,
        ))
        text.append(f"{turn.speaker}: {turn.text}" if turn.speaker else turn.text)
    transcript = "\n".join(text)
    transcript.encode("utf-8")  # Reject unpaired JSON surrogate escapes before persistence.
    return VendorRecording(
        source_id=str(meeting.id), started_at=meeting.date,
        ended_at=meeting.date + timedelta(seconds=meeting.duration_seconds),
        text=transcript, segments=segments, metadata={
            "timestamp_basis": "provider_meeting_date", "transcript_timing": "start_only",
            "parrot_meeting_date": stamp(meeting.date),
            "parrot_duration_seconds": str(meeting.duration_seconds),
        },
    )


def parrot_routes(settings, runtime) -> APIRouter:
    router = APIRouter()
    slots = asyncio.Semaphore(4)

    @router.post(PATH)
    async def receive(request: Request) -> dict:
        config = settings()
        if not config.parrot_webhook_enabled:
            raise HTTPException(404, "Parrot webhook receiver is disabled")
        secret = config.parrot_webhook_secret.get_secret_value()
        if not secret:
            raise HTTPException(503, "Save a Parrot webhook secret in server settings")
        signatures = request.headers.getlist("x-parrot-signature")
        if len(signatures) != 1 or not re.fullmatch(r"sha256=[a-f0-9]{64}", signatures[0]):
            raise HTTPException(401, "Invalid Parrot webhook signature")
        if (request.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/json"
                or request.headers.get("content-encoding", "identity").lower() != "identity"):
            raise HTTPException(415, "Send uncompressed application/json")
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                raise HTTPException(422, "Invalid Content-Length")
            if len(length) > 10 or int(length) > MAX_BYTES:
                raise HTTPException(413, "Parrot webhook exceeds 16 MB")
        if slots.locked():
            raise HTTPException(429, "Parrot webhook receiver is busy; retry later")
        async with slots:
            data = bytearray()

            async def read_body():
                async for part in request.stream():
                    if len(data) + len(part) > MAX_BYTES:
                        raise HTTPException(413, "Parrot webhook exceeds 16 MB")
                    data.extend(part)

            try:
                await asyncio.wait_for(read_body(), timeout=15)
            except TimeoutError:
                raise HTTPException(408, "Parrot webhook body timed out") from None
            expected = "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signatures[0], expected):
                raise HTTPException(401, "Invalid Parrot webhook signature")
            try:
                body = ParrotEvent.model_validate_json(data)
                if body.event == "test":
                    return {"event": "test", "accepted": True}
                if body.meeting is None:
                    raise ValueError("Missing meeting")
                recording = normalize_meeting(body.meeting)
            except (ValidationError, ValueError, OverflowError):
                # Never echo private meeting data from validation errors.
                raise HTTPException(
                    422, "Invalid Parrot meeting payload or recording bounds"
                ) from None

            def persist():
                store = runtime().memory.store
                archive = TranscriptArchive(store, "parrot", NAMESPACE, "parrot_export")
                result = archive.persist_page(VendorPage([recording]))
                store.apply_retention(config.audio_retention_days, config.transcript_retention_days)
                result["items"] = [public_source(row) for item in result["items"]
                                   if (row := store.get_audio(item["id"])) is not None]
                return result

            try:
                return await asyncio.to_thread(persist)
            except (OSError, sqlite3.Error):
                raise HTTPException(503, "Parrot meeting could not be saved; retry later") from None

    return router
