"""Conservative attributed transcript projection from exact word timings."""

import json
import math
from datetime import datetime


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
