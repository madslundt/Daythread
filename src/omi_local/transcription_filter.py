"""Select complete STT windows using speech observations; preserve original context."""

import io
import math
import wave
from typing import Literal, TypedDict

from .audio import transcription_chunks
from .voice_activity import analyze_voice

FILTER_VERSION = "webrtcvad-mode0-boosted-padding300ms-merge200ms-request-windows-v2"
_RATE = 16000
_VAD_FRAMES = 320  # analyze_voice observes complete 20 ms frames.
_PADDING_FRAMES = 4800
_MERGE_GAP_FRAMES = 3200


class TranscriptionAudio(TypedDict):
    spans: list[tuple[float, bytes]]
    status: Literal["filtered", "disabled", "unavailable"]
    original_seconds: float
    retained_seconds: float
    detected_speech_seconds: float | None


def _validated_activity(activity: dict, frames: int, raw: bytes) -> list[tuple[float, float]]:
    if not isinstance(activity, dict) or activity.get("status") != "observed":
        raise ValueError("speech_detection_unavailable")
    method = activity.get("method")
    if method not in {"webrtcvad", "digital_silence"}:
        raise ValueError("speech_detector_unknown")
    observed_end = (frames // _VAD_FRAMES * _VAD_FRAMES) / _RATE
    if method == "digital_silence":
        if any(raw):
            raise ValueError("speech_silence_mismatch")
        observed_end = frames / _RATE

    classified = []
    speech = []
    for label in ("speech", "non_speech"):
        ranges = activity.get(label)
        if not isinstance(ranges, list):
            raise ValueError("speech_activity_missing")
        previous_end = 0.0
        for interval in ranges:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise ValueError("speech_activity_range_invalid")
            start, end = interval
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in interval):
                raise ValueError("speech_activity_timestamp_invalid")
            if not 0 <= start < end <= observed_end or start < previous_end:
                raise ValueError("speech_activity_outside_audio")
            if method == "digital_silence" and label == "speech":
                raise ValueError("speech_activity_in_digital_silence")
            classified.append((start, end))
            if label == "speech":
                speech.append((start, end))
            previous_end = end

    # Missing coverage is unknown audio, never evidence that it is safe to discard.
    cursor = 0.0
    for start, end in sorted(classified):
        if abs(start - cursor) > 1e-8:
            raise ValueError("speech_activity_partition_invalid")
        cursor = end
    if abs(cursor - observed_end) > 1e-8:
        raise ValueError("speech_activity_incomplete")
    return speech


def _retained_windows(
    data: bytes, intervals: list[tuple[int, int]],
    chunk_seconds: int, boundary_search_seconds: float,
) -> tuple[list[tuple[float, bytes]], int]:
    if not intervals:
        return [], 0
    spans, retained_frames, index = [], 0, 0
    for offset, chunk in transcription_chunks(data, chunk_seconds, boundary_search_seconds):
        left = round(offset * _RATE)
        with wave.open(io.BytesIO(chunk)) as window:
            right = left + window.getnframes()
        while index < len(intervals) and intervals[index][1] <= left:
            index += 1
        if index < len(intervals) and intervals[index][0] < right:
            spans.append((offset, chunk))
            retained_frames += right - left
    return spans, retained_frames


def filter_transcription_audio(
    data: bytes, *, enabled: bool = True, activity: dict | None = None,
    chunk_seconds: int = 30, boundary_search_seconds: float = 5,
) -> TranscriptionAudio:
    """Keep complete original request windows intersecting padded speech observations.

    Speech detected in either original or boosted audio by analyze_voice is kept,
    including single-frame utterances. Each span gets 300 ms context on both sides;
    padded spans separated by at most 200 ms are merged. Select windows from the
    original chunk/boundary policy, never trim a selected window or concatenate gaps.
    Unobserved partial frames get the same context. Unknown or inconsistent activity
    retains the entire WAV.
    ``activity`` allows a precomputed detector result or deterministic testing.
    """
    result: TranscriptionAudio = {
        "spans": [(0.0, data)], "status": "unavailable", "original_seconds": 0.0,
        "retained_seconds": 0.0, "detected_speech_seconds": None,
    }
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            params = source.getparams()
            frames = source.getnframes()
            raw = source.readframes(frames)
        duration = frames / params.framerate
        result.update(original_seconds=duration, retained_seconds=duration)
        if not enabled:
            result["status"] = "disabled"
            return result
        if (params.nchannels, params.sampwidth, params.framerate) != (1, 2, _RATE):
            return result
        if params.comptype != "NONE" or len(raw) != frames * 2 or frames <= 0:
            return result
        detection = analyze_voice(data) if activity is None else activity
        speech = _validated_activity(detection, frames, raw)
        keep = list(speech)
        observed_frames = frames // _VAD_FRAMES * _VAD_FRAMES
        if detection["method"] != "digital_silence" and observed_frames < frames:
            keep.append((observed_frames / _RATE, duration))

        padded = []
        for start, end in keep:
            # Round outwards, tolerating only floating-point representation noise.
            left = max(0, math.floor(start * _RATE + 1e-8) - _PADDING_FRAMES)
            right = min(frames, math.ceil(end * _RATE - 1e-8) + _PADDING_FRAMES)
            if padded and left - padded[-1][1] <= _MERGE_GAP_FRAMES:
                padded[-1] = (padded[-1][0], max(padded[-1][1], right))
            else:
                padded.append((left, right))

        spans, retained_frames = _retained_windows(
            data, padded, chunk_seconds, boundary_search_seconds,
        )
        result.update(
            spans=spans, status="filtered",
            retained_seconds=retained_frames / _RATE,
            detected_speech_seconds=sum(end - start for start, end in speech),
        )
    except Exception:
        # The original remains transcribable even if WAV parsing or VAD fails.
        pass
    return result
