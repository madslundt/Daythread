"""Project existing speaker and timing evidence without changing transcript text."""

import json
import math
import re
from datetime import datetime

EXPORTED_SPEAKER = re.compile(r"^(Speaker (?:\d{1,4}|[A-Z])):\s+(.+)$", re.DOTALL)


def _valid_seconds(value, duration):
    return type(value) in (int, float) and 0 <= value <= duration and math.isfinite(value)


def transcript_turns(row: dict, attributed: list[dict] | None = None) -> list[dict]:
    text = row.get("text") or ""
    if not text or row["state"] != "done":
        return []
    if attributed and any(turn["attribution"] != "unknown" for turn in attributed):
        if "".join(turn["text"] for turn in attributed) == text:
            return [{key: turn[key] for key in (
                "text", "start_offset", "end_offset", "start", "end", "speaker_id",
                "speaker_name", "speaker_label", "attribution",
            ) if turn.get(key) is not None} for turn in attributed]
    segments = json.loads(row.get("segments") or "[]")
    if not isinstance(segments, list):
        return []
    duration = (datetime.fromisoformat(row["ended_at"])
                - datetime.fromisoformat(row["started_at"])).total_seconds()
    turns, offset, previous = [], 0, 0
    speakers = {}
    speaker_fields = ("speaker_name", "speaker_label", "speaker_number")
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            return []
        body = segment["text"].strip()
        if not body:
            continue
        start = offset
        while start < len(text) and text[start].isspace():
            start += 1
        name = segment.get("speaker_name")
        label = segment.get("speaker_label")
        identity = segment.get("speaker_id")
        prefix = f"{name or label}: " if name or label else ""
        prefixed = bool(prefix and text.startswith(prefix + body, start))
        evidence = prefix + body if prefixed else body
        if not text.startswith(evidence, start):
            return []
        offset = start + len(evidence)
        if not (name or label) and (export := EXPORTED_SPEAKER.fullmatch(body)):
            label, body = export.groups()
        turn = {"text": body, "start_offset": start, "end_offset": offset}
        if name:
            turn["speaker_name"] = name
        if label:
            turn["speaker_label"] = label
        if type(identity) in (int, str) and identity != "":
            turn["speaker_number"] = speakers.setdefault(identity, len(speakers) + 1)
        timestamp, end = segment.get("start"), segment.get("end")
        if _valid_seconds(timestamp, duration) and previous <= timestamp:
            turn["start"] = previous = timestamp
            if _valid_seconds(end, duration) and timestamp < end:
                turn["end"] = end
        # Unattributed word alignment is not a sequence of speaker changes.
        if (turns and segment.get("kind") == "word" and not (name or label) and identity is None
                and not any(key in turns[-1] for key in speaker_fields)
                and len(turns[-1]["text"]) < 240
                and not turns[-1]["text"].rstrip().endswith((".", "?", "!"))):
            turns[-1]["text"] = text[turns[-1]["start_offset"]:offset]
            turns[-1]["end_offset"] = offset
            if "start" in turns[-1] and "end" in turns[-1] and "end" in turn:
                turns[-1]["end"] = max(turns[-1]["end"], turn["end"])
            else:
                turns[-1].pop("end", None)
            continue
        turns.append(turn)
    return [] if text[offset:].strip() else turns


def saved_speaker_turns(memory, rows: list[dict], db=None) -> dict[str, list[dict]]:
    """Reuse ready speaker detection only; reading never runs a model."""
    from .conversation_state import Conversation

    if db is None:
        with memory.store.connect() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM speaker_scopes LIMIT 1").fetchone():
                return {}
            current = [stored for row in rows
                       if (stored := memory.store.get_audio(row["id"], db))
                       and stored["source_version"] == row["source_version"]]
            return saved_speaker_turns(memory, current, db)
    if not rows or not db.execute("SELECT 1 FROM speaker_scopes LIMIT 1").fetchone():
        return {}
    active = [row for row in rows if row["state"] == "done" and row.get("text")]
    if not active:
        return {}
    projections = memory.recaps.speaker_projection(Conversation(active), db)
    result = {source_id: [] for source_id, projection in projections.items()
              if projection["state"] == "ready"}
    seen = set()
    for source_id, projection in projections.items():
        if source_id not in result or id(projection) in seen:
            continue
        seen.add(id(projection))
        for turn in projection["utterances"]:
            if projections.get(turn["source_id"]) is projection:
                result[turn["source_id"]].append(turn)
    return result
