"""Optional, offline speaker voiceprints over clean Nemotron speaking turns.

The encoder is a separate local ONNX model, never a download or cloud fallback.
Both the model checksum and the runtime/extraction identity accompany every vector.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import wave
from array import array
from collections.abc import Callable
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any, BinaryIO

RUNTIME_VERSION = "2.0.1"
MODEL_FILE = "nemo_en_titanet_small.onnx"
MODEL_SHA256 = "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"
SAMPLE_RATE = 16000
MIN_SECONDS = 10
MAX_SAMPLES = 60 * SAMPLE_RATE


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_encoder(path: Path) -> tuple[Callable, int, dict]:
    """Use the official sherpa-onnx speaker identification API on CPU only."""
    import sherpa_onnx

    if version("sherpa-onnx") != RUNTIME_VERSION \
            or version("sherpa-onnx-core") != RUNTIME_VERSION:
        raise ValueError("Unsupported local speaker embedding runtime")
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(path), num_threads=2, provider="cpu", debug=False,
    )
    if not config.validate():
        raise ValueError("Invalid local speaker embedding configuration")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    digest = hashlib.sha256()
    for package_name in ("sherpa-onnx", "sherpa-onnx-core"):
        package = distribution(package_name)
        files = sorted((item for item in package.files or []
                        if str(item).endswith((".py", ".so", ".dylib"))), key=str)
        if not files:
            raise ValueError("Local speaker runtime provenance is unavailable")
        for item in files:
            digest.update(str(item).encode())
            digest.update(bytes.fromhex(file_hash(Path(package.locate_file(item)))))

    def encode(samples: array) -> Any:
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE,
                               waveform=[value / 32768.0 for value in samples])
        stream.input_finished()
        if not extractor.is_ready(stream):
            raise ValueError("Speaker sample is not ready")
        return extractor.compute(stream)

    return encode, int(extractor.dim), {
        "runtime": "sherpa-onnx", "version": RUNTIME_VERSION,
        "implementation_sha256": digest.hexdigest(),
        "numpy_version": version("numpy"),
    }


def unit_vector(values: Any, dimension: int) -> list[float]:
    if len(values) != dimension:
        raise ValueError("Invalid speaker vector dimension")
    vector = [float(value) for value in values]
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Invalid speaker vector")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Empty speaker vector")
    return [value / norm for value in vector]


def union(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    output = []
    for start, end in sorted(intervals):
        if output and start <= output[-1][1]:
            output[-1] = (output[-1][0], max(output[-1][1], end))
        else:
            output.append((start, end))
    return output


def clean_windows(turns: list[dict], speaker: str) -> tuple[list[tuple[int, int]], float]:
    own = union([(turn["start"], turn["end"]) for turn in turns if turn["speaker"] == speaker])
    others = union([(turn["start"], turn["end"]) for turn in turns if turn["speaker"] != speaker])
    clean, scan = [], 0
    for start, end in own:
        cursor = start
        while scan < len(others) and others[scan][1] <= start:
            scan += 1
        index = scan
        while index < len(others) and others[index][0] < end:
            left, right = others[index]
            if left > cursor:
                clean.append((cursor, min(left, end)))
            cursor = max(cursor, right)
            index += 1
        if cursor < end:
            clean.append((cursor, end))
    total = sum(end - start for start, end in own)
    overlap = 1 - sum(end - start for start, end in clean) / total if total else 1.0
    windows, remaining = [], MAX_SAMPLES
    for start, end in sorted(clean, key=lambda item: item[1] - item[0], reverse=True):
        first = math.ceil((start + 0.15) * SAMPLE_RATE)
        last = math.floor((end - 0.15) * SAMPLE_RATE)
        while min(last - first, remaining) >= 3 * SAMPLE_RATE:
            stop = first + min(last - first, remaining, 10 * SAMPLE_RATE)
            windows.append((first, stop))
            remaining -= stop - first
            first = stop
    return windows, min(1.0, max(0.0, overlap))


class SpeakerEmbeddings:
    def __init__(self, model: str | None = None, expected_sha256: str | None = None,
                 loader: Callable = load_encoder) -> None:
        self.path = Path(model or os.environ.get("OMI_SPEAKER_EMBEDDING_MODEL", ""))
        self.expected = expected_sha256 or os.environ.get("OMI_SPEAKER_EMBEDDING_SHA256", "")
        self.loader = loader
        self._signature = None
        self._verified = False
        self._encoder = None
        self._identity = None

    def configured(self) -> bool:
        try:
            if len(self.expected) != 64 or any(c not in "0123456789abcdef" for c in self.expected):
                return False
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature != self._signature:
                self._signature = signature
                self._encoder = self._identity = None
                self._verified = (self.path.is_file() and 0 < stat.st_size <= 256_000_000
                                  and file_hash(self.path) == self.expected)
            return self._verified
        except OSError:
            self._signature = None
            self._encoder = self._identity = None
            self._verified = False
            return False

    def health(self) -> dict:
        configured = self.configured()
        return {"configured": configured, "loaded": configured and self._encoder is not None}

    def extract(self, stream: BinaryIO, turns: list[dict]) -> dict:
        if not self.configured() or not turns:
            return {"voices": []}
        try:
            if self._encoder is None:
                encoder, dimension, runtime = self.loader(self.path)
                if type(dimension) is not int or not 1 <= dimension <= 4096:
                    raise ValueError("Unsupported speaker embedding dimension")
                if not self.configured():
                    return {"voices": []}
                descriptor = {"model_sha256": self.expected, "runtime": runtime,
                              "extraction_sha256": file_hash(Path(__file__)),
                              "dimension": dimension}
                revision = hashlib.sha256(json.dumps(
                    descriptor, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode()).hexdigest()
                self._identity = {"id": "sherpa-onnx-clean-speech", "revision": revision,
                                  "dimension": dimension, "normalization": "l2-v1"}
                self._encoder = encoder
            dimension = self._identity["dimension"]
            voices = []
            stream.seek(0)
            with wave.open(stream, "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                        audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                    return {"voices": []}
                duration = audio.getnframes() / SAMPLE_RATE
                if len(turns) > 100000 or any(
                    not isinstance(t.get("speaker"), str)
                    or type(t.get("start")) not in (int, float)
                    or type(t.get("end")) not in (int, float)
                    or not 0 <= t["start"] < t["end"] <= duration
                    for t in turns
                ):
                    return {"voices": []}
                speakers = {t["speaker"] for t in turns}
                if len(speakers) > 64:
                    return {"voices": []}
                for speaker in sorted(speakers):
                    windows, overlap = clean_windows(turns, speaker)
                    if sum(last - first for first, last in windows) < MIN_SECONDS * SAMPLE_RATE:
                        continue
                    try:
                        accumulated, count, clipped = [0.0] * dimension, 0, 0
                        for first, last in windows:
                            audio.setpos(first)
                            samples = array("h", audio.readframes(last - first))
                            if sys.byteorder != "little":
                                samples.byteswap()
                            if len(samples) != last - first or not any(samples):
                                raise ValueError("Incomplete or silent voice sample")
                            vector = unit_vector(self._encoder(samples), dimension)
                            clipped += sum(abs(value) >= 32760 for value in samples)
                            size = len(samples)
                            accumulated = [a + b * size for a, b in zip(accumulated, vector)]
                            count += size
                        voices.append({"speaker": speaker,
                                       "embedding": unit_vector(accumulated, dimension),
                                       "clean_seconds": count / SAMPLE_RATE,
                                       "overlap_ratio": overlap,
                                       "clipped_ratio": clipped / count})
                    except Exception:
                        # Never replace failed or unsupported vectors with fabricated values.
                        continue
            return {"embedding_model": self._identity, "voices": voices}
        except Exception:
            # The optional encoder must never break ordinary anonymous diarization.
            return {"voices": []}
        finally:
            stream.seek(0)
