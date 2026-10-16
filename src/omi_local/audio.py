import io
import math
import sys
import wave
from array import array
from datetime import datetime
from pathlib import Path

from .domain import stamp


def contains_speech_energy(data: bytes, threshold: int) -> bool:
    """Conservative RMS silence gate, with no model or network dependency.

    Only discard chunks entirely below the configured PCM16 energy floor. This is
    not speaker recognition and deliberately keeps noise for the STT provider to handle.
    """
    if threshold == 0:
        return True
    with wave.open(io.BytesIO(data), "rb") as stream:
        samples = array("h", stream.readframes(stream.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    for offset in range(0, len(samples), 1600):
        frame = samples[offset : offset + 1600]
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        if rms > threshold:
            return True
    return False


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


def render_window(rows: list[dict], start: datetime, end: datetime) -> tuple[bytes, list[dict]]:
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
        with wave.open(str(Path(row["path"])), "rb") as source:
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
