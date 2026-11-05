#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#   "fastapi>=0.115,<1",
#   "python-multipart>=0.0.20,<1",
#   "uvicorn>=0.30,<1",
#   "sherpa-onnx==2.0.1",
#   "numpy==2.5.4",
# ]
# ///
"""Local Nemotron diarization adapter for the existing Daythread /diarize API.

This wrapper invokes a pinned, locally provisioned NeMo-Speech.cpp executable
with an explicit local GGUF path. It never downloads a model at request time.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

try:
    from .nemotron_provenance import verify_installation
    from .speaker_embeddings import SpeakerEmbeddings
except ImportError:  # direct `python scripts/nemotron_diarization_server.py` entrypoint
    from nemotron_provenance import verify_installation
    from speaker_embeddings import SpeakerEmbeddings

MODEL_ID = "nvidia/Nemotron-3-Diarization"
MODEL_REVISION = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"
RUNTIME_VERSION = "0.3.0"
MAX_DURATION_SECONDS = 8 * 60 * 60
MAX_AUDIO_BYTES = MAX_DURATION_SECONDS * 16000 * 2 + 65536
MAX_REQUEST_BYTES = MAX_AUDIO_BYTES + 65536
MAX_TURNS = 100000
PROCESS_TIMEOUT_SECONDS = 3600


class RequestTooLarge(MultiPartException):
    def __init__(self) -> None:
        super().__init__("Audio upload is too large.")


class BodyLimit:
    """Bound multipart uploads when Content-Length is absent or dishonest."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > MAX_REQUEST_BYTES
        except ValueError:
            too_large = True
        if too_large:
            await JSONResponse({"detail": "Audio upload is too large."}, 413)(scope, receive, send)
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_REQUEST_BYTES:
                raise RequestTooLarge
            return message

        await self.app(scope, bounded_receive, send)


def inspect_wav(stream: BinaryIO) -> float:
    stream.seek(0, 2)
    if stream.tell() > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio upload is too large.")
    stream.seek(0)
    try:
        with wave.open(stream, "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                    audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise HTTPException(422, "Audio must be 16 kHz mono PCM16 WAV.")
            frames = audio.getnframes()
            duration = frames / 16000
            if frames <= 0:
                raise HTTPException(422, "Audio is empty.")
            if duration > MAX_DURATION_SECONDS:
                raise HTTPException(413, "Audio exceeds the eight hour limit.")
            remaining = frames * 2
            while remaining:
                block = audio.readframes(min(remaining // 2, 65536))
                if not block:
                    raise HTTPException(422, "Audio WAV data is truncated.")
                remaining -= len(block)
    except (wave.Error, EOFError, OSError, ValueError):
        raise HTTPException(422, "Audio must be a valid PCM WAV file.") from None
    finally:
        stream.seek(0)
    return duration


def normalize_segments(payload: Any, duration: float) -> list[dict[str, Any]]:
    """Validate JSON and retain per-speaker overlapping segments as separate tracks."""
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("Invalid diarizer result")
    if len(payload["segments"]) > MAX_TURNS:
        raise ValueError("Too many diarization segments")
    turns = []
    for segment in payload["segments"]:
        if not isinstance(segment, dict):
            raise ValueError("Invalid diarization segment")
        start, end, speaker = segment.get("start"), segment.get("end"), segment.get("speaker")
        if (isinstance(start, bool) or not isinstance(start, (int, float))
                or not math.isfinite(start) or start < 0
                or isinstance(end, bool) or not isinstance(end, (int, float))
                or not math.isfinite(end) or end <= start or end > duration + 0.1
                or isinstance(speaker, bool) or not isinstance(speaker, int)
                or not 1 <= speaker <= 8):
            raise ValueError("Invalid diarization segment")
        bounded_end = min(float(end), duration)
        if start >= bounded_end:
            raise ValueError("Invalid diarization segment")
        turns.append({"start": float(start), "end": bounded_end,
                      "speaker": f"SPEAKER_{speaker - 1:02d}"})
    return sorted(turns, key=lambda turn: (turn["start"], turn["end"], turn["speaker"]))


class NemotronRuntime:
    def __init__(
        self,
        binary: str | None = None,
        model: str | None = None,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        verifier: Callable[..., dict | None] | None = None,
        embeddings: SpeakerEmbeddings | None = None,
    ) -> None:
        self.binary = Path(binary or os.environ.get("OMI_NEMO_SPEECH_BIN", ""))
        self.model = Path(model or os.environ.get("OMI_NEMO_DIARIZATION_MODEL", ""))
        configured_provenance = os.environ.get("OMI_NEMO_PROVENANCE", "").strip()
        self.provenance = Path(configured_provenance) if configured_provenance else (
            self.model.parent / "PROVENANCE.txt"
        )
        self.run = run
        self.verifier = verifier or verify_installation
        self._verification_signature = None
        self._verification_result = None
        self.lock = threading.Lock()
        self.embeddings = embeddings or SpeakerEmbeddings()

    def verified_metadata(self) -> dict | None:
        try:
            paths = (self.binary, self.model, self.provenance)
            signature = tuple((path.stat().st_ino, path.stat().st_size,
                               path.stat().st_mtime_ns) for path in paths)
        except OSError:
            return None
        if signature != self._verification_signature:
            self._verification_signature = signature
            self._verification_result = self.verifier(
                self.binary, self.model, self.provenance,
            )
        return self._verification_result

    def configured(self) -> bool:
        return self.verified_metadata() is not None

    def health(self) -> dict[str, Any]:
        metadata = self.verified_metadata()
        files_exist = all(path.is_file() for path in (self.binary, self.model, self.provenance))
        return {"configured": metadata is not None, "ready": metadata is not None,
                "status": ("ready" if metadata else "unverified" if files_exist
                           else "unconfigured"),
                "model": metadata.get("model") if metadata else None,
                "model_revision": metadata.get("model_revision") if metadata else None,
                "runtime": metadata.get("runtime") if metadata else None,
                "runtime_version": metadata.get("runtime_version") if metadata else None,
                "device": "cpu", "voice_embeddings": self.embeddings.health()}

    def diarize(self, stream: BinaryIO, num_speakers: int | None = None) -> dict[str, Any]:
        if num_speakers is not None:
            raise HTTPException(422, "Nemotron uses automatic speaker-count detection.")
        if self.verified_metadata() is None:
            raise HTTPException(503, "Local speaker runtime is not provisioned.")
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, "Speaker detection is busy. Retry later.")
        try:
            duration = inspect_wav(stream)
            try:
                with tempfile.TemporaryDirectory(prefix="omi-nemotron-") as directory:
                    audio_path = Path(directory) / "input.wav"
                    output_path = Path(directory) / "turns.json"
                    stream.seek(0)
                    with audio_path.open("wb") as audio:
                        shutil.copyfileobj(stream, audio, length=1024 * 1024)
                    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
                    result = self.run(
                        [str(self.binary), "diarize", str(audio_path), "--model", str(self.model),
                         "--device", "cpu", "--format", "json", "--quiet", "--output",
                         str(output_path)],
                        check=False, capture_output=True, text=True,
                        timeout=PROCESS_TIMEOUT_SECONDS, env=env,
                    )
                    if result.returncode != 0 or not output_path.is_file():
                        raise RuntimeError("Inference did not produce a result")
                    if output_path.stat().st_size > 32_000_000:
                        raise RuntimeError("Diarization result exceeds the size limit")
                    payload = json.loads(output_path.read_text())
                    turns = normalize_segments(payload, duration)
            except subprocess.TimeoutExpired:
                raise HTTPException(503, "Local speaker detection timed out.") from None
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "Local speaker detection failed.") from None
            try:
                voices = self.embeddings.extract(stream, turns)
            except Exception:
                voices = {"voices": []}
            return {"turns": turns, "model": MODEL_ID, "model_revision": MODEL_REVISION,
                    "runtime": "NeMo-Speech.cpp", "runtime_version": RUNTIME_VERSION,
                    **voices}
        finally:
            self.lock.release()


def create_app(runtime: NemotronRuntime | None = None) -> FastAPI:
    app = FastAPI(title="Omi local Nemotron speaker detection", docs_url=None, redoc_url=None)
    app.add_middleware(BodyLimit)
    runtime = runtime or NemotronRuntime()
    request_lock = asyncio.Lock()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {**runtime.health(), "busy": request_lock.locked() or runtime.lock.locked()}

    @app.post("/diarize")
    async def diarize(request: Request) -> dict[str, Any]:
        if request_lock.locked() or runtime.lock.locked():
            raise HTTPException(429, "Speaker detection is busy. Retry later.")
        async with request_lock:
            try:
                async with request.form(max_files=1, max_fields=1, max_part_size=1024) as form:
                    audio = form.get("file")
                    if not isinstance(audio, UploadFile):
                        raise HTTPException(422, "Provide one audio WAV in the file field.")
                    if set(form) - {"file", "num_speakers"}:
                        raise HTTPException(422, "Unexpected form fields.")
                    raw_count = form.get("num_speakers")
                    if raw_count not in (None, ""):
                        try:
                            count = int(str(raw_count))
                        except (TypeError, ValueError):
                            raise HTTPException(422, "Speaker count must be between 1 and 8.")
                        if not 1 <= count <= 8:
                            raise HTTPException(422, "Speaker count must be between 1 and 8.")
                        return await run_in_threadpool(runtime.diarize, audio.file, count)
                    return await run_in_threadpool(runtime.diarize, audio.file, None)
            except RequestTooLarge:
                raise HTTPException(413, "Audio upload is too large.") from None
            except StarletteHTTPException as exc:
                if exc.status_code == 400 and exc.detail == "Audio upload is too large.":
                    raise HTTPException(413, "Audio upload is too large.") from None
                raise

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("OMI_DIARIZATION_HOST", "127.0.0.1"),
                port=int(os.environ.get("OMI_DIARIZATION_PORT", "8790")), workers=1)
