"""Validation and canonical text mapping for speech recognition responses."""

import math

from .domain import wav_duration
from .inference_errors import InferenceError


def transcription_result(payload: dict, data: bytes) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or len(text) > 20000:
        raise InferenceError("Invalid STT response")
    canonical_text = text.strip()
    leading_trim = len(text) - len(text.lstrip())
    trailing_end = len(text.rstrip())
    duration = wav_duration(data)
    raw_segments = payload.get("segments", [])
    if not isinstance(raw_segments, list):
        return {"text": canonical_text, "segments": [],
                "timestamp_status": "discarded_invalid"}

    explicit_anchors = any(
        isinstance(segment, dict)
        and any(field in segment for field in ("start_offset", "end_offset"))
        for segment in raw_segments
    )
    if explicit_anchors:
        segments = []
        invalid = False
        previous_text_end, previous_time_end = 0, 0.0
        for segment in raw_segments:
            try:
                start, end = segment["start"], segment["end"]
                left, right = segment["start_offset"], segment["end_offset"]
                segment_text = segment["text"]
                valid_numbers = (
                    type(start) in (int, float) and type(end) in (int, float)
                    and math.isfinite(start) and math.isfinite(end)
                )
                if (
                    not valid_numbers or type(left) is not int or type(right) is not int
                    or not isinstance(segment_text, str)
                    or not 0 <= leading_trim <= left < right <= trailing_end
                    or text[left:right] != segment_text
                    or not 0 <= start < min(end, duration) or end > duration + 0.5
                    or left < previous_text_end or start < previous_time_end
                ):
                    raise ValueError
                item = {
                    "start": float(start), "end": min(float(end), duration),
                    "text": segment_text,
                    "start_offset": left - leading_trim,
                    "end_offset": right - leading_trim,
                }
                if segment.get("kind") in {"word", "segment", "token"}:
                    item["kind"] = segment["kind"]
                segments.append(item)
                previous_text_end, previous_time_end = right, end
            except (KeyError, TypeError, ValueError, OverflowError):
                invalid = True
        result = {"text": canonical_text, "segments": segments}
        if invalid:
            result["timestamp_status"] = "partial" if segments else "discarded_invalid"
        return result

    segments = []
    anchor_raw_text_end = 0
    anchor_time_end = 0.0
    native_text_map_valid = True
    missing_anchor = False
    for segment in raw_segments:
        try:
            start, end = float(segment["start"]), float(segment["end"])
            segment_text = segment["text"]
            if (not 0 <= start < min(end, duration) or not end <= duration + 0.5
                    or not isinstance(segment_text, str)):
                raise ValueError
            item = {"start": start, "end": min(end, duration),
                    "text": segment_text[:20000]}
            native_times = (
                type(segment.get("start")) in (int, float)
                and type(segment.get("end")) in (int, float)
                and math.isfinite(segment["start"]) and math.isfinite(segment["end"])
                and 0 <= segment["start"] < segment["end"] <= duration
                and segment["start"] >= anchor_time_end
            )
            # Segment text supplies no character offsets. Accept it only at the
            # exact next position in the provider transcript; never search or normalize.
            raw_first = anchor_raw_text_end
            native_text_match = (
                native_text_map_valid and bool(segment_text)
                and text.startswith(segment_text, raw_first)
            )
            raw_last = raw_first + len(segment_text) if native_text_match else -1
            if native_text_match:
                anchor_raw_text_end = raw_last
            else:
                native_text_map_valid = False
            first, last = max(raw_first, leading_trim), min(raw_last, trailing_end)
            clip_first = first - raw_first
            clip_last = last - raw_first
            exact_text = segment_text[clip_first:clip_last] if native_text_match else ""
            canonical_first, canonical_last = first - leading_trim, last - leading_trim
            native_text = (
                native_text_match and len(segment_text) <= 20000
                and canonical_first < canonical_last
                and canonical_text[canonical_first:canonical_last] == exact_text
            )
            if native_times and native_text:
                item.update(text=exact_text, start_offset=canonical_first,
                            end_offset=canonical_last, kind="segment")
                anchor_time_end = segment["end"]
            else:
                missing_anchor = True
            segments.append(item)
        except (KeyError, TypeError, ValueError, OverflowError):
            # Timing is optional. Do not invent alignment or repeat a successful
            # recognition request because the provider's timing is unusable.
            return {"text": canonical_text, "segments": [],
                    "timestamp_status": "discarded_invalid"}
    result = {"text": canonical_text, "segments": segments}
    if missing_anchor:
        result["timestamp_status"] = "partial" if any(
            item.get("kind") == "segment" for item in segments
        ) else "discarded_invalid"
    return result
