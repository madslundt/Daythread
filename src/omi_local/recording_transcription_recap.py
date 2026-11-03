"""Typed selected recording sources and their recording-timeline speaker evidence."""

import hashlib
import json
import sqlite3

from .conversation_state import Conversation
from .merged_recordings import load_recording
from .recording_transcription_selection import SelectedRecordingVariant
from .recording_transcription_store import pipeline_fingerprint, project_words, source_fingerprint


def selected_recording_variants(
    recaps, group: Conversation, db=None,
) -> dict[str, SelectedRecordingVariant]:
    """Select variants only when the recap contains every part of the complete recording."""
    if db is None:
        with recaps.memory.store.connect() as connection:
            connection.execute("BEGIN")
            return selected_recording_variants(recaps, group, connection)
    by_recording: dict[str, list[dict]] = {}
    for row in group.sources:
        metadata = json.loads(row.get("source_metadata") or "{}")
        recording_id = metadata.get("recording_id")
        if recording_id:
            by_recording.setdefault(recording_id, []).append(row)
    selected = {}
    settings = recaps.memory.settings
    for recording_id, members in by_recording.items():
        recording = load_recording(recaps.memory.store, recording_id, db)
        member_ids = [row["id"] for row in recording.rows]
        allowed_ids = [row["id"] for row in members]
        if not recording.complete or member_ids != allowed_ids:
            continue
        view = recaps.memory.store.selected_recording_transcription(recording, settings, db)
        if view.get("state") != "selected":
            continue
        variant = view["transcript"]
        if (
            variant.get("state") != "ready"
            or variant.get("input_fingerprint") != source_fingerprint(recording)
            or variant.get("pipeline_fingerprint") != pipeline_fingerprint(settings)
            or variant.get("text") is None
        ):
            continue
        selected[recording_id] = {
            "recording": recording, "variant": variant,
            "selection_version": view["selection_version"],
            "input_fingerprint": view["input_fingerprint"],
            "pipeline_fingerprint": view["pipeline_fingerprint"],
            "source_ids": member_ids,
        }
    return selected


def recording_speaker_evidence(
    recaps, group: Conversation, recording_id: str, variant: dict,
    db: sqlite3.Connection | None = None,
) -> tuple[list[dict], str | None, str]:
    """Map source-local diarized turns onto exact full-recording aligned word spans."""
    metadata_by_id = {
        row["id"]: json.loads(row.get("source_metadata") or "{}") for row in group.sources
    }
    member_ids = [row["id"] for row in group.sources
                  if metadata_by_id[row["id"]].get("recording_id") == recording_id]
    projections = recaps.speaker_projection(group, db)
    if not member_ids:
        return [], None, "idle"
    scope_values = [projections.get(source_id, {}) for source_id in member_ids]
    versions = sorted({scope.get("evidence_version") for scope in scope_values
                       if scope.get("evidence_version")})
    if any(scope.get("state") != "ready" for scope in scope_values):
        state = "stale" if any(s.get("state") == "stale" for s in scope_values) else "idle"
        return [], None, state
    evidence_version = hashlib.sha256(
        json.dumps(versions, sort_keys=True).encode(),
    ).hexdigest()
    labels, turns = {}, []
    seen_turns = set()
    for scope in scope_values:
        labels.update({speaker["id"]: speaker for speaker in scope.get("speakers", [])})
        for turn in scope.get("turns", []):
            identity = (turn.get("source_id"), turn.get("start"), turn.get("end"),
                        turn.get("speaker_id"))
            if identity not in seen_turns:
                seen_turns.add(identity)
                turns.append(turn)
    source_map = variant.get("source_map") or []
    by_source = {part["source_id"]: part for part in source_map}
    timeline = []
    for turn in turns:
        part = by_source.get(turn.get("source_id"))
        start, end, speaker_id = turn.get("start"), turn.get("end"), turn.get("speaker_id")
        if (not part or type(start) not in (int, float) or type(end) not in (int, float)
                or not 0 <= start < end <= part["end"] - part["start"]):
            continue
        timeline.append({"start": part["start"] + start, "end": part["start"] + end,
                         "speaker_id": speaker_id, "source_id": turn["source_id"]})
    timeline.sort(key=lambda item: (item["start"], item["end"], item["speaker_id"] or ""))
    boundaries = {
        (left["source_id"], right["source_id"], round(left["end"], 6))
        for left, right in zip(source_map, source_map[1:])
        if abs(left["end"] - right["start"]) <= 1 / 16000
    }
    merged = []
    for turn in timeline:
        if (merged and merged[-1]["speaker_id"] == turn["speaker_id"]
                and any(
                    merged[-1]["source_id"] == left_id
                    and turn["source_id"] == right_id
                    and abs(merged[-1]["end"] - boundary) <= 1 / 16000
                    and abs(turn["start"] - boundary) <= 1 / 16000
                    for left_id, right_id, boundary in boundaries
                )):
            merged[-1]["end"] = turn["end"]
        else:
            merged.append(dict(turn))
    output = []
    for item in project_words(variant.get("text") or "", variant.get("word_segments") or [],
                              source_map):
        evidence = {**item, "recording_id": recording_id,
                    "speaker_id": None, "speaker_label": None, "speaker_name": None,
                    "attribution": "unknown"}
        start, end = item.get("start"), item.get("end")
        if (start is not None and end is not None
                and item.get("kind") in {"word", "boundary_word"}):
            overlapping = [
                turn for turn in merged if turn["start"] < end and turn["end"] > start
            ]
            speaker_ids = {turn["speaker_id"] for turn in overlapping if turn["speaker_id"]}
            if len(speaker_ids) > 1:
                evidence["attribution"] = "overlap"
            elif len(speaker_ids) == 1:
                speaker_id = next(iter(speaker_ids))
                spans = sorted((max(start, turn["start"]), min(end, turn["end"]))
                               for turn in overlapping if turn["speaker_id"] == speaker_id)
                covered = start
                for left, right in spans:
                    if left > covered + 1 / 16000:
                        break
                    covered = max(covered, right)
                if covered >= end - 1 / 16000:
                    speaker = labels.get(speaker_id, {})
                    evidence.update(attribution="attributed", speaker_id=speaker_id,
                                    speaker_label=speaker.get("label"),
                                    speaker_name=speaker.get("name"))
        output.append(evidence)
    return output, evidence_version, "ready"
