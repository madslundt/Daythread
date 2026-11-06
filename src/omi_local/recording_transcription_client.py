"""Bounded private WAV spooling and the pinned local CPP request contract."""

import json
import math
import os
import re
import struct
from pathlib import Path

import httpx

from .merged_recordings import pcm

SPOOL_DIR = ".recording-transcription-spool"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
UNKNOWN_NATIVE_TIMING = ("unknown_zero_duration", "unknown_out_of_bounds")
DECODING_FIELDS = {
    "model": "whisper", "response_format": "verbose_json", "language": "auto",
    "max_context": "0", "max_len": "-1", "beam_size": "5", "best_of": "5",
    "temperature": "0", "temperature_inc": "0.2",
    "carry_initial_prompt": "false", "translate": "false", "no_timestamps": "false",
}


def recording_decoding_fields(settings) -> dict[str, str]:
    return DECODING_FIELDS | settings.stt_language_candidate_fields()


def _spool_wav(store, recording, path: Path, maximum_bytes: int, maximum_seconds: int, on_part):
    frames = recording.frames
    expected_frames = sum(frames)
    byte_count = 44 + expected_frames * 2
    if expected_frames <= 0 or expected_frames / 16000 > maximum_seconds:
        raise ValueError("duration_cap")
    if byte_count > maximum_bytes:
        raise ValueError("byte_cap")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    data_size = expected_frames * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        data_size + 36,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        16000,
        32000,
        2,
        16,
        b"data",
        data_size,
    )
    written_frames = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(header)
        for index, (row, count) in enumerate(zip(recording.rows, frames), start=1):
            part = pcm(store, row, count)
            if written_frames * 2 + len(part) + 44 > maximum_bytes:
                raise ValueError("byte_cap")
            output.write(part)
            written_frames += count
            del part
            on_part(index, len(frames))
    if written_frames != expected_frames or path.stat().st_size != byte_count:
        raise ValueError("invalid_audio")
    return byte_count, expected_frames


async def _request(settings, path: Path):
    headers = {}
    secret = settings.recording_transcription_api_key
    if secret is None:
        secret = settings.stt_route("auto").token
    if secret:
        value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
        if value:
            headers["Authorization"] = f"Bearer {value}"
    timeout = httpx.Timeout(settings.recording_transcription_timeout_seconds, connect=10, pool=10)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        with path.open("rb") as audio:
            async with client.stream(
                "POST",
                f"{settings.recording_transcription_base_url.rstrip('/')}/audio/transcriptions",
                headers=headers,
                data=recording_decoding_fields(settings),
                files={"file": ("recording.wav", audio, "audio/wav")},
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError("response_cap")
                return dict(response.headers), json.loads(body)


def _validate_response(headers, payload, settings, duration):
    expected = {
        "x-homecompute-stt-model-sha256": (settings.recording_transcription_expected_model_sha256),
        "x-homecompute-stt-model-snapshot": (
            settings.recording_transcription_expected_model_snapshot
        ),
        "x-homecompute-stt-runtime-commit": (
            settings.recording_transcription_expected_runtime_commit
        ),
    }
    for name, configured in (
        ("homecompute-revision", settings.recording_transcription_expected_homecompute_revision),
        ("image-id", settings.recording_transcription_expected_image_id),
    ):
        if configured:
            expected[f"x-homecompute-stt-{name}"] = configured
    provenance = {key.removeprefix("x-homecompute-stt-"): headers.get(key) for key in expected}
    provenance["runtime"] = headers.get("x-homecompute-stt-runtime")
    if not provenance["runtime"] or any(
        not value or headers.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("provenance_mismatch")
    for name, pattern in (
        ("homecompute-revision", r"[0-9a-f]{40}"),
        ("image-id", r"sha256:[0-9a-f]{64}"),
    ):
        value = headers.get(f"x-homecompute-stt-{name}")
        if value is not None:
            if not re.fullmatch(pattern, value):
                raise ValueError("provenance_mismatch")
            provenance[name] = value
    text = payload.get("text") if isinstance(payload, dict) else None
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if (not isinstance(text, str) or len(text) > MAX_RESPONSE_BYTES
            or not isinstance(segments, list)):
        raise ValueError("invalid_response")
    native, last = [], 0.0
    exact_join = []
    for item in segments:
        if not isinstance(item, dict):
            raise ValueError("invalid_response")
        start, end = item.get("start"), item.get("end")
        part_text = item.get("text")
        unknown = (start is None and end is None
                   and item.get("timing_status") == "unknown_out_of_bounds")
        if item.get("timing_status") == "unknown_out_of_bounds" and not unknown:
            raise ValueError("invalid_segments")
        if not isinstance(part_text, str) or not unknown and (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < last
            or end < start
            or start < 0
            or end > duration
        ):
            raise ValueError("invalid_segments")
        if not unknown:
            last = end
        exact_join.append(part_text)
        segment = {"start": start, "end": end, "text": part_text, "kind": "segment"}
        if unknown:
            segment["timing_status"] = "unknown_out_of_bounds"
        elif end == start:
            # Keep exact words and character anchors, but do not invent a usable
            # time interval for a zero-duration native envelope.
            segment.update(
                start=None, end=None, timing_status="unknown_zero_duration",
            )
        native.append(segment)

    # The local CPP adapter may publish exact character anchors for its canonical
    # newline-joined serialization. Trust only those anchors when each exact slice
    # matches. Never search segment phrases in the full text: repeated phrases make
    # text matching ambiguous. For older compatible adapters without anchors, use
    # only a whole-response serialization that exactly reproduces the full text.
    anchored = True
    any_anchor_fields = any(
        "start_offset" in source_segment or "end_offset" in source_segment
        for source_segment in segments
    )
    prior_end = 0
    for source_segment, item in zip(segments, native, strict=True):
        left, right = source_segment.get("start_offset"), source_segment.get("end_offset")
        if left is None and right is None:
            anchored = False
            break
        if (
            type(left) is not int
            or type(right) is not int
            or not 0 <= left < right <= len(text)
            or left < prior_end
            or text[left:right] != item["text"]
        ):
            anchored = False
            break
        item["start_offset"], item["end_offset"] = left, right
        prior_end = right
    if not anchored:
        for item in native:
            item.pop("start_offset", None)
            item.pop("end_offset", None)
        joined = "".join(exact_join)
        separator = None if any_anchor_fields else (
            "" if joined == text else "\n" if "\n".join(exact_join) == text else None
        )
        if separator is not None:
            cursor = 0
            for item in native:
                item["start_offset"] = cursor
                cursor += len(item["text"])
                item["end_offset"] = cursor
                cursor += len(separator)
    for item in native:
        if "start_offset" not in item:
            item["start_offset"] = item["end_offset"] = None
        if (item.get("timing_status") == "unknown_out_of_bounds"
                and item["start_offset"] is None):
            raise ValueError("invalid_segments")
    return text, native, provenance
