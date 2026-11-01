"""Explicit-demand speech previews have no transcript or archive mutation authority."""

import asyncio
import time
from contextlib import suppress
from copy import copy
from weakref import WeakKeyDictionary

from fastapi import APIRouter, HTTPException, Request, Response

from .domain import wav_duration
from .inference import Inference
from .inference_errors import InferenceError

MAX_SECONDS = 15
MAX_BYTES = MAX_SECONDS * 32000 + 4096
TIMEOUT_SECONDS = 10
COOLDOWN_SECONDS = 10


class PreviewLimit:
    def __init__(self):
        self.active = False
        self.next_at = 0.0

    def admit(self):
        if self.active or time.monotonic() < self.next_at:
            raise HTTPException(429, "Wait before requesting another preview",
                                headers={"Retry-After": str(COOLDOWN_SECONDS)})
        self.active = True
        self.next_at = time.monotonic() + COOLDOWN_SECONDS


async def recognize_preview(memory, data: bytes) -> dict:
    """Reuse STT routing/vocabulary while disabling expensive optional enhancement passes."""
    inference = memory.inference
    if isinstance(inference, Inference):
        inference = copy(inference)
        inference.settings = memory.settings.model_copy(update={
            "stt_enhancement_mode": "off", "stt_chunk_seconds": MAX_SECONDS,
            "inference_timeout_seconds": TIMEOUT_SECONDS,
        })
        recorder = inference.usage_recorder
        if recorder is not None:
            # Billable attempts remain visible without counting this temporary
            # copy as another captured recording or storing a filter observation.
            def record(event):
                if event["kind"] != "filter":
                    recorder({**event, "recording_id": None, "task": "transcription_preview"})
            inference.usage_recorder = record
    result = await inference.transcribe_result(data)
    return {"text": result["text"], "provisional": True, "saved": False}


async def until_disconnect(request: Request):
    while not await request.is_disconnected():
        await asyncio.sleep(0.05)
    raise HTTPException(499, "Preview cancelled")


async def cancel_on_disconnect(request: Request, operation):
    recognition = asyncio.create_task(operation)
    disconnect = asyncio.create_task(until_disconnect(request))
    try:
        completed, _ = await asyncio.wait({recognition, disconnect},
                                          return_when=asyncio.FIRST_COMPLETED)
        if disconnect in completed:
            return await disconnect
        return await recognition
    finally:
        for task in (recognition, disconnect):
            task.cancel()
        for task in (recognition, disconnect):
            with suppress(asyncio.CancelledError, Exception):
                await task


def transcription_preview_routes(get_memory):
    router = APIRouter(prefix="/v1/transcription-preview")
    limits = WeakKeyDictionary()

    @router.get("")
    def capability(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        memory = get_memory()
        return {"enabled": memory.settings.stt_preview_enabled and memory.settings.stt_enabled,
                "maximum_seconds": MAX_SECONDS, "cooldown_seconds": COOLDOWN_SECONDS}

    @router.post("")
    async def preview(request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        memory = get_memory()
        if not memory.settings.stt_preview_enabled:
            raise HTTPException(409, "Provisional transcription preview is disabled")
        memory.settings.require_inference("stt")
        if request.headers.get("content-type", "").split(";")[0].strip() != "audio/wav":
            raise HTTPException(415, "Send a mono 16 kHz WAV preview")
        limit = limits.setdefault(memory, PreviewLimit())
        limit.admit()
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise HTTPException(413, "Preview audio exceeds 15 seconds")
                try:
                    duration = wav_duration(bytes(data))
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                if not 0 < duration <= MAX_SECONDS:
                    raise HTTPException(422, "Preview audio must contain up to 15 seconds")
                return await cancel_on_disconnect(
                    request, recognize_preview(memory, bytes(data)),
                )
        except TimeoutError as exc:
            raise HTTPException(504, "Preview timed out; capture continues") from exc
        except InferenceError as exc:
            raise HTTPException(502, "Preview unavailable; capture continues") from exc
        finally:
            limit.active = False

    return router
