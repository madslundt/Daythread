"""Passage provenance copied from authoritative sources without inferred timing."""

import json
import math
from bisect import bisect_left, bisect_right
from datetime import datetime


def json_object(value: str) -> dict:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def prepare_source_provenance(source: dict) -> dict:
    """One ephemeral, linear preparation per source; never cache transcript data globally."""
    from .embedding_chunking import topic_regions

    sections = json_object(source.get("topic_details") or "{}").get("sections")
    return {"topics": topic_regions(source["text"], sections),
            "anchors": segment_anchors(source),
            "grouping": json_object(source.get("source_metadata") or "{}")}


def passage_metadata(
    source: dict, span: dict, manifest: dict, provenance: dict | None = None,
) -> dict:
    provenance = provenance if provenance is not None else prepare_source_provenance(source)
    result = {
        "source_id": source.get("audio_id", source.get("id")),
        "transcript_version": source.get("version", source.get("transcript_version")),
        "source_version": source.get("source_version"),
        "source_start": source["started_at"],
        "source_end": source["ended_at"],
        "timing": "source",
        "embedding_model": manifest.get("model"),
        "embedding_revision": manifest.get("revision"),
        "chunking_strategy": manifest.get("chunking_strategy", "character-v1"),
    }
    grouping = provenance["grouping"]
    for key in ("recording_id", "conversation_source_id"):
        if grouping.get(key):
            result[key] = grouping[key]
    for start, end, index in provenance["topics"]:
        if (index is not None and start <= span["start_offset"] < span["end_offset"] <= end
                and manifest.get("chunking_strategy") == "topic-token-v1"):
            result.update({
                "topic_id": str(index),
                "topic_start_offset": start,
                "topic_end_offset": end,
            })
            break
    result.update(segment_metadata(source, span, provenance["anchors"]))
    return result


def segment_anchors(source: dict) -> list[tuple]:
    """Validate a complete chronological segmentation against the original text in O(n).

    Requiring complete alignment avoids both ambiguous repeated phrases and an expensive
    transcript-wide find/count for every segment. Partial or rewritten segments have no
    trustworthy passage timing and retain the known source bounds instead.
    """
    text = source["text"]
    try:
        segments = json.loads(source.get("segments") or "[]")
        duration = (datetime.fromisoformat(source["ended_at"])
                    - datetime.fromisoformat(source["started_at"])).total_seconds()
    except (TypeError, ValueError):
        return []
    if not isinstance(segments, list) or not segments:
        return []
    anchors = []
    previous_offset, previous_seconds = 0, 0
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            return []
        value = segment["text"].strip()
        seconds = (segment.get("start"), segment.get("end"))
        if not value or any(type(v) not in (float, int) or not math.isfinite(v) for v in seconds):
            return []
        if not 0 <= previous_seconds <= seconds[0] < seconds[1] <= duration + 0.05:
            return []
        offset = previous_offset
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if not text.startswith(value, offset):
            return []
        anchors.append((offset, offset + len(value), segment))
        previous_offset, previous_seconds = offset + len(value), seconds[1]
    return [] if text[previous_offset:].strip() else anchors


def segment_metadata(source: dict, span: dict, anchors: list[tuple] | None = None) -> dict:
    """Timing exists only for exact, unambiguous complete segment boundaries."""
    anchors = anchors if anchors is not None else segment_anchors(source)
    if not anchors:
        return {}
    text = source["text"]
    start, end = span["start_offset"], span["end_offset"]
    excerpt = text[start:end]
    left = start + len(excerpt) - len(excerpt.lstrip())
    right = end - len(excerpt) + len(excerpt.rstrip())
    first = bisect_left(anchors, left, key=lambda a: a[0])
    last = bisect_right(anchors, right - 1, key=lambda a: a[0])
    selected = anchors[first:last]
    if not selected or selected[0][0] != left or selected[-1][1] != right:
        return {}
    if any(text[a[1]:b[0]].strip() for a, b in zip(selected, selected[1:])):
        return {}
    result = {
        "timing": "segment", "start_seconds": selected[0][2]["start"],
        "end_seconds": selected[-1][2]["end"],
    }
    speakers = [a[2].get("speaker_id") for a in selected]
    if all(isinstance(value, str) and value for value in speakers) and len(set(speakers)) == 1:
        result["speaker_id"] = speakers[0]
    return result


def refresh_passage_metadata(source: dict, passage: dict, provenance: dict | None = None) -> dict:
    """Resolve mutable topic/timing provenance against the selected live source."""
    old = passage.get("metadata") or {}
    manifest = {
        "model": old.get("embedding_model"), "revision": old.get("embedding_revision"),
        "chunking_strategy": old.get("chunking_strategy"),
    }
    metadata = passage_metadata(source, passage, manifest, provenance)
    return metadata | {"chunk_index": passage["ordinal"]}
