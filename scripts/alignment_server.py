# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#   "fastapi==0.115.12",
#   "numpy==2.2.6",
#   "python-multipart==0.0.20",
#   "torch==2.8.0",
#   "torchaudio==2.8.0",
#   "transformers==4.48.3",
#   "tokenizers==0.21.1",
#   "huggingface-hub==0.36.0",
#   "uvicorn==0.34.0",
#   "whisperx==3.3.1",
# ]
# ///
"""Optional CPU forced alignment; no recognition and no hosted inference.

Run with `uv run --no-project --python 3.12 scripts/alignment_server.py` after
provisioning model files. The process does not read .env and never downloads weights.
See docs/alignment.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import io
import math
import os
import re
import sys
import threading
import types
import wave
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MODEL_DA = "CoRal-project/roest-v3-wav2vec2-315m"
MODEL_DA_REVISION = "beb3e790246d6b9dec1df596b0b21d5c42f4d99c"
MODEL_EN = "facebook/wav2vec2-base-960h"
MODEL_EN_REVISION = "22aad52d435eb6dbaf354bdad9b0da84ce7d6156"
MAX_SECONDS = 30
MAX_AUDIO_BYTES = MAX_SECONDS * 16000 * 2 + 65536
MAX_REQUEST_BYTES = MAX_AUDIO_BYTES + 128 * 1024
MODEL_REVISION = ("whisperx-3.3.1;da=" + MODEL_DA + "@" + MODEL_DA_REVISION
                  + ";en=" + MODEL_EN + "@" + MODEL_EN_REVISION)
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"


class RequestTooLarge(MultiPartException):
    def __init__(self) -> None:
        super().__init__("Request is too large.")


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            oversized = int(headers.get(b"content-length", b"0")) > MAX_REQUEST_BYTES
        except ValueError:
            oversized = True
        if oversized:
            await JSONResponse({"detail": "Request is too large."}, 413)(scope, receive, send)
            return
        size = 0

        async def bounded_receive() -> Message:
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > MAX_REQUEST_BYTES:
                raise RequestTooLarge
            return message

        await self.app(scope, bounded_receive, send)


def read_wav(stream: io.BytesIO) -> tuple[Any, float]:
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
            if not 0 < frames <= MAX_SECONDS * 16000:
                raise HTTPException(413, "Audio duration is outside the supported range.")
            raw = audio.readframes(frames)
            if len(raw) != frames * 2:
                raise HTTPException(422, "Audio WAV data is truncated.")
    except (wave.Error, EOFError, OSError, ValueError):
        raise HTTPException(422, "Audio must be a valid PCM WAV file.") from None
    import numpy as np

    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0, frames / 16000


class AlignmentRuntime:
    def __init__(self) -> None:
        self.models: dict[str, tuple[Any, Any]] = {}
        self.lock = threading.Lock()
        self.status = "unloaded"

    def configured(self) -> bool:
        return any(Path(os.environ.get(name, "").strip()).is_dir() for name in (
            "OMI_ALIGNMENT_DA_MODEL_DIR", "OMI_ALIGNMENT_EN_MODEL_DIR",
        ))

    def health(self) -> dict[str, Any]:
        return {"configured": self.configured(), "ready": bool(self.models),
                "status": self.status if self.configured() else "unconfigured",
                "model": MODEL_REVISION}

    def load(self, language: str) -> tuple[Any, Any, Any]:
        if language not in {"da", "en"}:
            raise HTTPException(422, "Alignment language must be da or en.")
        if language not in self.models:
            key = "OMI_ALIGNMENT_DA_MODEL_DIR" if language == "da" else "OMI_ALIGNMENT_EN_MODEL_DIR"
            directory = os.environ.get(key, "").strip()
            if not directory or not Path(directory).is_dir():
                raise HTTPException(503, "Local alignment model is not configured.")
            os.environ["HF_HUB_OFFLINE"] = "1"
            import torch
            whisperx = alignment_module()

            device = os.environ.get("OMI_ALIGNMENT_DEVICE", "cpu")
            if device not in {"cpu", "cuda", "mps"}:
                raise HTTPException(503, "Unsupported alignment device.")
            try:
                cpu_threads = int(os.environ.get("OMI_ALIGNMENT_CPU_THREADS", "2"))
            except ValueError:
                raise HTTPException(503, "Invalid CPU thread limit.") from None
            if not 1 <= cpu_threads <= 16:
                raise HTTPException(503, "Invalid CPU thread limit.")
            torch.set_num_threads(cpu_threads)
            model, metadata = whisperx.load_align_model(
                language_code=language, device=device,
                model_name=directory,
            )
            self.models[language] = (model, metadata)
            self.status = "ready"
        import torch

        return (*self.models[language], torch)

    def align(self, audio_stream: io.BytesIO, text: str, language: str) -> dict[str, Any]:
        with self.lock:
            samples, duration = read_wav(audio_stream)
            try:
                model, metadata, torch = self.load(language)
                self.status = "ready"
                with torch.inference_mode(), contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    result = alignment_module().align(
                        [{"start": 0.0, "end": duration, "text": text}],
                        model, metadata, samples, os.environ.get("OMI_ALIGNMENT_DEVICE", "cpu"),
                        return_char_alignments=True,
                    )
                aligned_chars = [char for segment in result.get("segments", [])
                                 for char in segment.get("chars", [])]
                if "".join(char.get("char", "") for char in aligned_chars) != text:
                    return {"status": "partial", "model": MODEL_REVISION, "segments": []}
                tokens = list(re.finditer(r"\S+", text))
                segments = []
                previous = 0.0
                for token in tokens:
                    indices = [index for index in range(token.start(), token.end())
                               if text[index].isalnum()]
                    if not indices:
                        continue
                    bounds = []
                    for index in indices:
                        char = aligned_chars[index]
                        start, end = char.get("start"), char.get("end")
                        if (isinstance(start, bool) or isinstance(end, bool)
                                or not isinstance(start, (int, float))
                                or not isinstance(end, (int, float))
                                or not math.isfinite(start) or not math.isfinite(end)
                                or not 0 <= start < end <= duration):
                            bounds = []
                            break
                        bounds.append((start, end))
                    if not bounds:
                        continue
                    start, end = min(a for a, _ in bounds), max(b for _, b in bounds)
                    if start < previous:
                        continue
                    previous = end
                    segments.append({"start": start, "end": end,
                                     "start_offset": token.start(), "end_offset": token.end(),
                                     "text": token.group()})
                status = "aligned" if len(segments) == len(tokens) else "partial"
                return {"status": status, "model": MODEL_REVISION, "segments": segments}
            except HTTPException:
                raise
            except Exception:
                self.status = "error"
                return {"status": "unavailable", "model": MODEL_REVISION, "segments": []}


app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(BodyLimit)
runtime = AlignmentRuntime()
inflight_lock = asyncio.Lock()


def alignment_module() -> Any:
    """Import WhisperX's aligner without its ASR or diarization runtime."""
    loaded = sys.modules.get("whisperx.alignment")
    if loaded is not None:
        return loaded
    spec = importlib.util.find_spec("whisperx")
    if spec is None or spec.origin is None:
        raise RuntimeError("WhisperX alignment package is unavailable.")
    package = sys.modules.get("whisperx")
    if package is None:
        package = types.ModuleType("whisperx")
        package.__path__ = [str(Path(spec.origin).parent)]
        package.__package__ = "whisperx"
        package.__file__ = spec.origin
        sys.modules["whisperx"] = package
    return importlib.import_module("whisperx.alignment")


@app.exception_handler(RequestTooLarge)
async def too_large(_: Request, __: RequestTooLarge) -> JSONResponse:
    return JSONResponse({"detail": "Request is too large."}, 413)


@app.exception_handler(StarletteHTTPException)
async def safe_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"detail": str(exc.detail)}, exc.status_code)


@app.get("/health")
async def health() -> dict[str, Any]:
    return runtime.health()


@app.post("/align")
async def align(
    file: UploadFile, text: str = Form(...), language: str = Form("da"),
) -> dict[str, Any]:
    if not 1 <= len(text) <= 20000:
        raise HTTPException(422, "Text is empty or too large.")
    if language == "auto":
        return {"status": "unavailable", "language": language,
                "model": MODEL_REVISION, "segments": []}
    stream = io.BytesIO(await file.read(MAX_AUDIO_BYTES + 1))
    if stream.tell() > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio upload is too large.")
    if inflight_lock.locked():
        raise HTTPException(503, "Alignment service is busy.")
    async with inflight_lock:
        task = asyncio.create_task(asyncio.to_thread(runtime.align, stream, text, language))
        try:
            result = await asyncio.shield(task)
            return result | {"language": language}
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8791, access_log=False)
