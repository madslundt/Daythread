"""Conservative attributed transcript projection from exact word timings."""

import json
import math
from datetime import datetime

SAMPLE_RATE = 16000


def conversation_audio_parts(scope: dict) -> dict[str, dict]:
    """Map scoped source clips to the concatenated audio order, without reading audio."""
    try:
        left, right = map(datetime.fromisoformat, scope["bounds"])
    except (KeyError, TypeError, ValueError):
        return {}
    parts = {}
    offset_frames = 0
    for row in scope.get("rows", []):
        try:
            source_start = datetime.fromisoformat(row["started_at"])
            source_end = datetime.fromisoformat(row["ended_at"])
            clip_start = max(0.0, (left - source_start).total_seconds())
            clip_end = (min(right, source_end) - source_start).total_seconds()
            first_frame = round(clip_start * SAMPLE_RATE)
            last_frame = round(clip_end * SAMPLE_RATE)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if last_frame <= first_frame:
            continue
        parts[row["id"]] = {
            "part_index": len(parts) + 1,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "offset_frames": offset_frames,
        }
        offset_frames += last_frame - first_frame
    for part in parts.values():
        part["part_count"] = len(parts)
    return parts


def add_conversation_audio_times(
    items: list[dict], parts: dict[str, dict], audio_available: bool,
) -> None:
    """Add display-only concatenated elapsed times, keeping source-local times intact."""
    for item in items:
        part = parts.get(item.get("source_id"))
        item["part_index"] = part["part_index"] if part else None
        item["part_count"] = part["part_count"] if part else None
        item["audio_start"] = item["audio_end"] = None
        if not part or not audio_available:
            continue
        start, end = item.get("start"), item.get("end")
        if (
            type(start) not in (int, float)
            or type(end) not in (int, float)
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end
        ):
            continue
        source_left, source_right = start * SAMPLE_RATE, end * SAMPLE_RATE
        if source_left < part["first_frame"] or source_right > part["last_frame"]:
            continue
        first_frame, last_frame = round(source_left), round(source_right)
        if last_frame <= first_frame:
            continue
        item["audio_start"] = round(
            (part["offset_frames"] + first_frame - part["first_frame"]) / SAMPLE_RATE, 6,
        )
        item["audio_end"] = round(
            (part["offset_frames"] + last_frame - part["first_frame"]) / SAMPLE_RATE, 6,
        )


def project_utterances(
    turns: list[dict],
    rows: list[dict],
    ready: bool,
    speakers: list[dict],
) -> list[dict]:
    """Project exact timed transcript segments onto source-local speaker turns."""
    result = []
    speaker_by_id = {speaker["id"]: speaker for speaker in speakers}
    for row in rows:
        try:
            segments = json.loads(row.get("segments") or "[]")
        except TypeError, json.JSONDecodeError:
            segments = []
        if not isinstance(segments, list):
            segments = []
        local_turns = [t for t in turns if t["source_id"] == row["id"]]
        text = row.get("text")
        text = text if isinstance(text, str) else ""
        if not text:
            continue
        duration = max(
            0.0,
            (
                datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])
            ).total_seconds(),
        )
        spans = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            left, right = segment.get("start_offset"), segment.get("end_offset")
            if (
                type(left) is int
                and type(right) is int
                and 0 <= left < right
                and right <= len(text)
                and text[left:right] == segment.get("text")
            ):
                spans.append((left, right, segment))
        spans.sort(key=lambda item: (item[0], item[1]))
        accepted = []
        cursor = 0
        for left, right, segment in spans:
            if left < cursor:
                continue
            start, end = segment.get("start"), segment.get("end")
            try:
                timed = (
                    type(start) in (int, float)
                    and type(end) in (int, float)
                    and math.isfinite(start)
                    and math.isfinite(end)
                    and 0 <= start < end <= duration
                )
            except OverflowError, TypeError, ValueError:
                timed = False
            accepted.append((left, right, segment, timed))
            cursor = right
        last_end = -1.0
        source_items = []
        cursor = 0
        for index, (left, right, segment, timed) in enumerate(accepted):
            if cursor < left:
                source_items.append(
                    {
                        "id": f"{row['id']}:unknown:{cursor}:{left}",
                        "source_id": row["id"],
                        "start": None,
                        "end": None,
                        "text": text[cursor:left],
                        "speaker_id": None,
                        "speaker_label": None,
                        "speaker_name": None,
                        "attribution": "unknown",
                        "start_offset": cursor,
                        "end_offset": left,
                    }
                )
            start, end = segment.get("start"), segment.get("end")
            if timed and start < last_end:
                timed = False
            if timed:
                last_end = end
            overlapping = (
                [t for t in local_turns if min(t["end"], end) > max(t["start"], start)]
                if timed and ready
                else []
            )
            owner = None
            status = "unknown"
            if timed and ready and overlapping:
                # Require complete containment and exactly one turn through the word.
                covering = [t for t in overlapping if t["start"] <= start and t["end"] >= end]
                if len(overlapping) == 1 and len(covering) == 1:
                    owner, status = covering[0]["speaker_id"], "attributed"
                elif any(
                    left_turn["speaker_id"] != right_turn["speaker_id"]
                    and min(end, left_turn["end"], right_turn["end"])
                    > max(start, left_turn["start"], right_turn["start"])
                    for i, left_turn in enumerate(overlapping)
                    for right_turn in overlapping[i + 1 :]
                ):
                    status = "overlap"
            source_items.append(
                {
                    "id": f"{row['id']}:{index}",
                    "source_id": row["id"],
                    "start": start if timed else None,
                    "end": end if timed else None,
                    "text": text[left:right],
                    "speaker_id": owner,
                    "speaker_label": speaker_by_id.get(owner, {}).get("label"),
                    "speaker_name": speaker_by_id.get(owner, {}).get("name"),
                    **({"speaker_name_origin": "automatic"}
                       if speaker_by_id.get(owner, {}).get("automatic_match") else {}),
                    "attribution": status,
                    "start_offset": left,
                    "end_offset": right,
                }
            )
            cursor = right
        if cursor < len(text):
            source_items.append(
                {
                    "id": f"{row['id']}:unknown:{cursor}:{len(text)}",
                    "source_id": row["id"],
                    "start": None,
                    "end": None,
                    "text": text[cursor:],
                    "speaker_id": None,
                    "speaker_label": None,
                    "speaker_name": None,
                    "attribution": "unknown",
                    "start_offset": cursor,
                    "end_offset": len(text),
                }
            )
        result.extend(source_items)
    return coalesce_utterances(result)


def coalesce_utterances(utterances: list[dict]) -> list[dict]:
    """Join adjacent aligned words when doing so cannot hide unknown speech."""
    output = []
    for item in utterances:
        if item["attribution"] == "attributed" and len(output) >= 2:
            prior, gap = output[-2], output[-1]
            if (
                gap["attribution"] == "unknown"
                and prior["source_id"] == item["source_id"] == gap["source_id"]
                and prior["attribution"] == "attributed"
                and prior["speaker_id"] == item["speaker_id"]
                and prior["end_offset"] == gap["start_offset"]
                and gap["end_offset"] == item["start_offset"]
                and not any(char.isalnum() for char in gap["text"])
            ):
                prior["text"] += gap["text"] + item["text"]
                prior["end_offset"] = item["end_offset"]
                prior["end"] = item["end"]
                output.pop()
                continue
        if output:
            prior = output[-1]
            same_source = prior["source_id"] == item["source_id"]
            same_owner = prior["speaker_id"] is not None and (
                prior["speaker_id"] == item["speaker_id"]
            )
            next_same = (
                item["attribution"] == "attributed"
                and same_owner
                and prior["attribution"] == "attributed"
            )
            if same_source and next_same and prior["end_offset"] == item["start_offset"]:
                prior["text"] += item["text"]
                prior["end_offset"] = item["end_offset"]
                prior["end"] = item["end"]
                continue
            if (
                same_source
                and item["attribution"] == "unknown"
                and prior["attribution"] == "unknown"
                and prior["end_offset"] == item["start_offset"]
            ):
                prior["text"] += item["text"]
                prior["end_offset"] = item["end_offset"]
                continue
        output.append(dict(item))
    return output


def attach_text(turns: list[dict], rows: list[dict]) -> None:
    # Assign each timestamped segment once; never invent word-level speaker alignment.
    for row in rows:
        try:
            segments = json.loads(row.get("segments") or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(segments, list):
            continue
        matching = [t for t in turns if t["source_id"] == row["id"]]
        duration = max(
            0.0,
            (datetime.fromisoformat(row["ended_at"])
             - datetime.fromisoformat(row["started_at"])).total_seconds(),
        )
        for segment in segments:
            if not isinstance(segment, dict) or not matching or not segment.get("text"):
                continue
            start, end = segment.get("start"), segment.get("end")
            try:
                valid = (
                    type(start) in (int, float) and type(end) in (int, float)
                    and math.isfinite(start) and math.isfinite(end)
                    and 0 <= start < end <= duration
                )
            except (OverflowError, TypeError, ValueError):
                valid = False
            if not valid:
                continue
            overlapping = [t for t in matching if min(t["end"], end) > max(t["start"], start)]
            if not overlapping or len({t["speaker_id"] for t in overlapping}) != 1:
                continue
            best = max(overlapping, key=lambda t: min(t["end"], end) - max(t["start"], start))
            if min(best["end"], end) - max(best["start"], start) >= (end - start) * 0.8:
                best["text"] = (best.get("text", "") + " " + segment["text"]).strip()
