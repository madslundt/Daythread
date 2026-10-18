import io
import math
import sys
import wave
from array import array
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .domain import stamp


def transcription_chunks(
    data: bytes, seconds: int = 30, boundary_search_seconds: float = 0
) -> Iterator[tuple[float, bytes]]:
    """Bound model requests while retaining sample-exact offsets in the original WAV."""
    with wave.open(io.BytesIO(data), "rb") as source:
        rate = source.getframerate()
        frames = seconds * rate
        if source.getnframes() <= frames:
            yield 0.0, data
            return
        offset = 0
        while offset < source.getnframes():
            end = min(offset + frames, source.getnframes())
            if end < source.getnframes() and boundary_search_seconds:
                start = max(offset, end - round(boundary_search_seconds * rate))
                source.setpos(start)
                samples = array("h", source.readframes(end - start))
                if sys.byteorder != "little":
                    samples.byteswap()
                # Quiet is only a boundary hint: even below-threshold speech is retained.
                window = max(1, round(rate * 0.02))
                run, boundary = None, None
                has_louder_audio = False
                for index in range(0, len(samples), window):
                    frame = samples[index : index + window]
                    if sum(s * s for s in frame) <= 35 * 35 * len(frame):
                        if run is None:
                            run = index
                    else:
                        has_louder_audio = True
                        if run is not None and index - run >= rate * 0.1:
                            boundary = start + (run + index) // 2
                        run = None
                if run is not None and len(samples) - run >= rate * 0.1:
                    boundary = start + (run + len(samples)) // 2
                # An entirely faint window could be continuous quiet speech.
                if has_louder_audio and boundary is not None and boundary > offset:
                    end = boundary
            source.setpos(offset)
            output = io.BytesIO()
            with wave.open(output, "wb") as chunk:
                chunk.setparams(source.getparams())
                chunk.writeframes(source.readframes(end - offset))
            yield offset / rate, output.getvalue()
            offset = end


def contains_speech_energy(data: bytes, threshold: int | None) -> bool:
    """Conservative RMS silence gate, with no model or network dependency.

    None skips only exact digital zero; 0 disables the legacy gate. Positive floors
    can reject quiet speech and must be explicitly enabled. Noise is kept for STT.
    """
    if threshold == 0:
        return True
    with wave.open(io.BytesIO(data), "rb") as stream:
        samples = array("h", stream.readframes(stream.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    if threshold is None:
        return any(samples)
    for offset in range(0, len(samples), 1600):
        frame = samples[offset : offset + 1600]
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        if rms > threshold:
            return True
    return False


def amplify_quiet_audio(data: bytes, max_gain_db: float) -> tuple[bytes, float]:
    """Uniform gain, capped by RMS target and peak headroom; never remove samples."""
    if max_gain_db == 0:
        return data, 0.0
    with wave.open(io.BytesIO(data), "rb") as stream:
        params = stream.getparams()
        samples = array("h", stream.readframes(stream.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max((abs(sample) for sample in samples), default=0)
    if not peak:
        return data, 0.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    gain = min(10 ** (max_gain_db / 20), 32768 * 10 ** (-25 / 20) / rms, 0.9 * 32767 / peak)
    if gain <= 1:
        return data, 0.0
    amplified = array("h", (round(sample * gain) for sample in samples))
    if sys.byteorder != "little":
        amplified.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams(params)
        stream.writeframes(amplified.tobytes())
    return output.getvalue(), round(20 * math.log10(gain), 3)


def coverage_gaps(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    cursor = start
    gaps = []
    for row in rows:
        left = max(start, datetime.fromisoformat(row["started_at"]))
        right = min(end, datetime.fromisoformat(row["ended_at"]))
        if left > cursor:
            gaps.append({"start": stamp(cursor), "end": stamp(left)})
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append({"start": stamp(cursor), "end": stamp(end)})
    return gaps


def render_window(
    rows: list[dict], start: datetime, end: datetime, *, read_audio=None,
) -> tuple[bytes, list[dict]]:
    frames = round((end - start).total_seconds() * 16000)
    if not 0 < frames <= 60 * 60 * 16000:
        raise ValueError("Rewind window must be between zero and 60 minutes")
    pcm = bytearray(frames * 2)
    for row in rows:
        at = datetime.fromisoformat(row["started_at"])
        left = max(start, at)
        right = min(end, datetime.fromisoformat(row["ended_at"]))
        offset = round((left - at).total_seconds() * 16000)
        target = round((left - start).total_seconds() * 16000)
        count = min(round((right - left).total_seconds() * 16000), frames - target)
        if count <= 0:
            continue
        source_file = io.BytesIO(read_audio(row["id"])) if read_audio else str(Path(row["path"]))
        with wave.open(source_file, "rb") as source:
            source.setpos(offset)
            data = source.readframes(count)
        if len(data) != count * 2:
            raise ValueError("Stored audio is incomplete")
        pcm[target * 2 : (target + count) * 2] = data
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        wav.writeframes(pcm)
    return output.getvalue(), coverage_gaps(rows, start, end)
