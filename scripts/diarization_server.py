# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#   "fastapi>=0.115,<1",
#   "numpy>=2,<3",
#   "pyannote.audio==4.0.4",
#   "python-multipart>=0.0.20,<1",
#   "uvicorn>=0.30,<1",
# ]
# ///
"""Optional local speaker detection; isolated from the backend's Python runtime.

Run: uv run --no-project --python 3.12 scripts/diarization_server.py
See config/omi.env.example for model access and offline configuration. This process
does not read .env automatically: export its runtime variables before starting it.
Only Community-1 runs here; audio is never sent to a hosted inference service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import wave
from collections.abc import Callable, Iterable
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MODEL_ID = "pyannote/speaker-diarization-community-1"
MAX_DURATION_SECONDS = 8 * 60 * 60
MAX_AUDIO_BYTES = MAX_DURATION_SECONDS * 16000 * 2 + 65536
MAX_REQUEST_BYTES = MAX_AUDIO_BYTES + 65536
EMBEDDING_VERSION = "4.0.4"

# Set before importing pyannote/Hugging Face, including when an operator enabled
# telemetry globally. Local meeting metadata must stay local.
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


class RequestTooLarge(MultiPartException):
    def __init__(self) -> None:
        super().__init__("Audio upload is too large.")


class BodyLimit:
    """Bound multipart uploads even when Content-Length is absent or dishonest."""

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


def inspect_wav(stream: BinaryIO) -> tuple[int, float]:
    """Reject unsupported, empty, overlong and truncated WAVs before inference."""
    stream.seek(0, 2)
    if stream.tell() > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio upload is too large.")
    stream.seek(0)
    try:
        with wave.open(stream, "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 16000
                or audio.getcomptype() != "NONE"
            ):
                raise HTTPException(422, "Audio must be 16 kHz mono PCM16 WAV.")
            frames = audio.getnframes()
            duration = frames / 16000
            if frames <= 0:
                raise HTTPException(422, "Audio is empty.")
            if duration > MAX_DURATION_SECONDS:
                raise HTTPException(413, "Audio exceeds the eight hour limit.")
            # Read in small blocks, rather than allocating the declared length of
            # an untrusted WAV data chunk. This also detects truncated uploads.
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
    return frames, duration


def load_pipeline() -> Any:
    """Load only a supplied offline model, or an explicitly authorized download."""
    directory = os.environ.get("OMI_DIARIZATION_MODEL_DIR", "").strip()
    allow_download = os.environ.get("OMI_DIARIZATION_ALLOW_DOWNLOAD", "").lower() == "true"
    token = os.environ.get("HF_TOKEN", "").strip()
    if directory:
        if not Path(directory).is_dir():
            raise RuntimeError("Model directory is unavailable.")
        # Loading a directory must not silently fetch missing weights/configs.
        os.environ["HF_HUB_OFFLINE"] = "1"
    elif not (allow_download and token):
        raise RuntimeError("Local speaker model is not configured.")

    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(directory or MODEL_ID, token=token or False)
    if pipeline is None:
        raise RuntimeError("Local speaker model is unavailable.")
    device = os.environ.get("OMI_DIARIZATION_DEVICE", "cpu")
    if device not in {"cpu", "cuda", "mps"}:
        raise RuntimeError("Unsupported speaker model device.")
    pipeline.to(torch.device(device))
    return pipeline


class DiarizationRuntime:
    def __init__(self, loader: Callable[[], Any] = load_pipeline) -> None:
        self.loader = loader
        self.pipeline: Any = None
        self.status = "unloaded"
        self.lock = threading.Lock()
        self.embedding_adapter: tuple[Any, dict] | None = None
        self.embedding_checked = False

    def health(self) -> dict[str, Any]:
        directory = os.environ.get("OMI_DIARIZATION_MODEL_DIR", "").strip()
        configured = bool(
            (directory and Path(directory).is_dir())
            or (
                not directory
                and os.environ.get("OMI_DIARIZATION_ALLOW_DOWNLOAD", "").lower() == "true"
                and os.environ.get("HF_TOKEN", "").strip()
            )
        )
        return {
            "configured": configured,
            "ready": self.pipeline is not None,
            "status": self.status if configured or self.pipeline is not None else "unconfigured",
            "model": MODEL_ID,
        }

    def diarize(self, stream: BinaryIO, num_speakers: int | None) -> dict[str, Any]:
        # Also protect callers outside the HTTP endpoint; only one heavyweight
        # model execution, including its waveform allocation, may run at a time.
        with self.lock:
            _, duration = inspect_wav(stream)
            if self.pipeline is None:
                self.status = "loading"
                try:
                    self.pipeline = self.loader()
                    if self.pipeline is None:
                        raise RuntimeError("Model loader returned no pipeline.")
                except Exception:
                    self.status = "error"
                    raise HTTPException(503, "Local speaker model is unavailable.") from None
                self.status = "ready"
            try:
                import numpy as np
                import torch

                with wave.open(stream, "rb") as audio:
                    samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
                waveform = torch.tensor(samples, dtype=torch.float32).unsqueeze(0) / 32768.0
                options = {} if num_speakers is None else {"num_speakers": num_speakers}
                with torch.inference_mode():
                    output = self.pipeline({"waveform": waveform, "sample_rate": 16000}, **options)
                labels: dict[str, str] = {}
                turns = normalize_turns(output.exclusive_speaker_diarization, duration, labels)
                result = {"turns": turns, "model": MODEL_ID, "voices": []}
                # Optional recognition must never break basic speaker detection.
                try:
                    if not self.embedding_checked and num_speakers is None:
                        self.embedding_adapter = embedding_adapter(self.pipeline)
                        self.embedding_checked = True
                    # Forced cluster counts can hide distinct/overlapping voices.
                    if self.embedding_adapter and num_speakers is None:
                        extractor, identity = self.embedding_adapter
                        regular = normalize_turns(output.speaker_diarization, duration, labels)
                        result.update(embedding_model=identity, voices=extract_voices(
                            extractor, identity["dimension"], waveform, samples, regular,
                            {turn["speaker"] for turn in turns},
                        ))
                except Exception:
                    # No provider exceptions, file paths or secrets go to clients.
                    pass
                return result
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, "Local speaker detection failed.") from None


def normalize_turns(
    annotation: Any, duration: float, labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Keep timestamps bounded and speaker identifiers stable within this request."""
    turns = []
    labels = {} if labels is None else labels
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        start, end = float(segment.start), float(segment.end)
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("Invalid model timestamps.")
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        label = labels.setdefault(str(speaker), f"SPEAKER_{len(labels):02d}")
        turns.append({"start": start, "end": end, "speaker": label})
        if len(turns) > 100000:
            raise ValueError("Too many speaker turns.")
    return sorted(turns, key=lambda turn: (turn["start"], turn["end"]))


def fingerprint_parts(parts: Iterable[tuple[str, bytes]]) -> str:
    """Content identity, independent of aliases, local paths and access credentials."""
    digest = hashlib.sha256()
    for name, data in parts:
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def embedding_adapter(pipeline: Any) -> tuple[Any, dict] | None:
    """Use only the verified 4.0.4 PyTorch adapter; never load another model.

    Official API: pyannote/pyannote-audio tag 4.0.4, pipelines/speaker_verification.py
    PyannoteAudioPretrainedSpeakerEmbedding.__call__: (batch,1,samples) -> (batch,dimension).
    Pipeline centroids are unsuitable: get_embeddings can fall back to overlapping speech.
    """
    from pyannote.audio.pipelines.speaker_verification import (
        PyannoteAudioPretrainedSpeakerEmbedding,
    )

    extractor = getattr(pipeline, "_embedding", None)
    if version("pyannote-audio") != EMBEDDING_VERSION or not isinstance(
        extractor, PyannoteAudioPretrainedSpeakerEmbedding
    ) or extractor.sample_rate != 16000:
        return None
    dimension = int(extractor.dimension)
    if not 1 <= dimension <= 4096:
        return None
    model = extractor.model_
    model.eval()

    def parts() -> Iterable[tuple[str, bytes]]:
        config = {"class": f"{type(model).__module__}.{type(model).__qualname__}",
                  "parameters": dict(model.hparams), "dimension": dimension,
                  "versions": {p: version(p) for p in
                               ("pyannote-audio", "torch", "torchaudio", "numpy")}}
        yield "config", json.dumps(config, sort_keys=True, allow_nan=False).encode()
        yield "extraction", Path(__file__).read_bytes()
        package = distribution("pyannote-audio")
        implementation = [item for item in package.files or []
                          if str(item).startswith("pyannote/audio/") and str(item).endswith(".py")]
        if not implementation:
            raise ValueError("No installed implementation available for fingerprinting.")
        for item in sorted(implementation, key=str):
            yield str(item), Path(package.locate_file(item)).read_bytes()
        state = model.state_dict()
        if not state:
            raise ValueError("No embedding weights available for fingerprinting.")
        for name, tensor in sorted(state.items()):
            value = tensor.detach().cpu().contiguous()
            yield name + ":shape", json.dumps([str(value.dtype), list(value.shape)]).encode()
            # Byte view also supports scalar and bfloat16 state buffers.
            import torch
            yield name, value.reshape(-1).view(torch.uint8).numpy().tobytes()

    identity = {"id": "pyannote-community-1-clean-speech", "revision": fingerprint_parts(parts()),
                "dimension": dimension, "normalization": "l2-v1"}
    return extractor, identity


def union_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def clean_intervals(turns: list[dict], speaker: str) -> tuple[list[tuple[float, float]], float]:
    """Subtract every other regular-diarization voice, including overlapping tracks."""
    own = union_intervals([(t["start"], t["end"]) for t in turns if t["speaker"] == speaker])
    others = union_intervals([(t["start"], t["end"]) for t in turns if t["speaker"] != speaker])
    clean, index = [], 0
    for start, end in own:
        cursor = start
        while index < len(others) and others[index][1] <= start:
            index += 1
        scan = index
        while scan < len(others) and others[scan][0] < end:
            left, right = others[scan]
            if left > cursor:
                clean.append((cursor, min(left, end)))
            cursor = max(cursor, right)
            scan += 1
        if cursor < end:
            clean.append((cursor, end))
    total = sum(b - a for a, b in own)
    ratio = 1 - sum(b - a for a, b in clean) / total if total else 1.0
    return clean, min(1.0, max(0.0, ratio))


def voice_windows(intervals: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """At most sixty seconds per voice, in 3–10s contiguous, unpadded windows."""
    windows, remaining = [], 60 * 16000
    for start, end in sorted(intervals, key=lambda value: value[1] - value[0], reverse=True):
        # A small boundary collar reduces contamination from imprecise diarization.
        first, last = math.ceil((start + 0.15) * 16000), math.floor((end - 0.15) * 16000)
        while min(last - first, remaining) >= 3 * 16000:
            stop = first + min(last - first, remaining, 10 * 16000)
            windows.append((first, stop))
            remaining -= stop - first
            first = stop
    return windows


def unit_vector(values: Any, dimension: int) -> list[float]:
    if len(values) != dimension:
        raise ValueError("Unexpected embedding dimension.")
    vector = [float(value) for value in values]
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Invalid embedding values.")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Empty embedding.")
    return [value / norm for value in vector]


def extract_voices(
    extractor: Any, dimension: int, waveform: Any, samples: Any,
    regular_turns: list[dict], speakers: set[str],
) -> list[dict]:
    voices = []
    for speaker in sorted(speakers):
        clean, overlap = clean_intervals(regular_turns, speaker)
        windows = voice_windows(clean)
        if sum(last - first for first, last in windows) < 10 * 16000:
            continue
        try:
            accumulated, count, clipped = [0.0] * dimension, 0, 0
            for first, last in windows:
                batch = extractor(waveform[:, first:last].unsqueeze(0))
                if len(batch) != 1:
                    raise ValueError("Unexpected embedding batch.")
                vector = unit_vector(batch[0], dimension)
                size = last - first
                # PCM16 clipping, measured on exactly the audio used for embeddings.
                clipped += int(((samples[first:last] >= 32760) |
                                (samples[first:last] <= -32760)).sum())
                accumulated = [a + b * size for a, b in zip(accumulated, vector)]
                count += size
            if count >= 10 * 16000:
                voices.append({"speaker": speaker, "embedding": unit_vector(accumulated, dimension),
                               "clean_seconds": count / 16000, "overlap_ratio": overlap,
                               "clipped_ratio": clipped / count})
        except Exception:
            # A bad/unsupported voice is omitted, never replaced by a fabricated vector.
            continue
    return voices


def create_app(runtime: DiarizationRuntime | None = None) -> FastAPI:
    app = FastAPI(title="Omi local speaker detection", docs_url=None, redoc_url=None)
    app.add_middleware(BodyLimit)
    runtime = runtime or DiarizationRuntime()
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
                # Starlette spools large uploads to an anonymous temporary file;
                # the form context closes/removes it on success, error or cancel.
                async with request.form(max_files=1, max_fields=1, max_part_size=1024) as form:
                    audio = form.get("file")
                    if not isinstance(audio, UploadFile):
                        raise HTTPException(422, "Provide one audio WAV in the file field.")
                    if set(form) - {"file", "num_speakers"}:
                        raise HTTPException(422, "Unexpected form fields.")
                    count = form.get("num_speakers")
                    if count is None or count == "":
                        num_speakers = None
                    else:
                        try:
                            num_speakers = int(str(count))
                        except (TypeError, ValueError):
                            raise HTTPException(422, "Speaker count must be between 1 and 64.")
                        if not 1 <= num_speakers <= 64:
                            raise HTTPException(422, "Speaker count must be between 1 and 64.")
                    return await run_in_threadpool(runtime.diarize, audio.file, num_speakers)
            except RequestTooLarge:
                raise HTTPException(413, "Audio upload is too large.") from None
            except StarletteHTTPException as exc:
                # Request.form translates parser errors after closing spooled files.
                if exc.status_code == 400 and exc.detail == "Audio upload is too large.":
                    raise HTTPException(413, "Audio upload is too large.") from None
                raise

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        create_app(),
        host=os.environ.get("OMI_DIARIZATION_HOST", "127.0.0.1"),
        port=int(os.environ.get("OMI_DIARIZATION_PORT", "8790")),
        workers=1,
    )
