"""Bounded, compact speaker-evidence projections for summary prompts."""

import json
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SummarySourcePiece:
    """One prompt block and its exact source-text window."""

    start_offset: int
    end_offset: int
    content: str


SpeakerWindow = Callable[[dict, int, int], list[dict]]


def _is_nonlexical_gap(item: dict) -> bool:
    text = item.get("text")
    return (
        item.get("attribution") == "unknown"
        and isinstance(text, str)
        and bool(text)
        and all(char.isspace() or unicodedata.category(char).startswith("P") for char in text)
    )


def _valid_span(item: dict) -> bool:
    text = item.get("text")
    start, end = item.get("start_offset"), item.get("end_offset")
    return (
        isinstance(text, str)
        and bool(text)
        and type(start) is int
        and type(end) is int
        and start >= 0
        and end > start
        and len(text) == end - start
    )


def _speaker_id(item: dict) -> str | None:
    value = item.get("speaker_id")
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _speaker_aliases(items: list[dict]) -> dict[str, str]:
    aliases = {}
    for item in items:
        speaker_id = _speaker_id(item)
        if speaker_id is not None and speaker_id not in aliases:
            aliases[speaker_id] = f"speaker_{len(aliases) + 1}"
    return aliases


def _speaker_legend(items: list[dict], aliases: dict[str, str]) -> dict:
    observations: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"labels": set(), "names": set()},
    )
    for item in items:
        if item.get("attribution") != "attributed":
            continue
        owner = aliases.get(_speaker_id(item) or "")
        if owner is None:
            continue
        label, name = item.get("speaker_label"), item.get("speaker_name")
        if isinstance(label, str) and label:
            observations[owner]["labels"].add(label)
        if isinstance(name, str) and name:
            observations[owner]["names"].add(name)

    ids_by_alias = {alias: speaker_id for speaker_id, alias in aliases.items()}
    result = {}
    for owner, values in observations.items():
        entry = {"id": ids_by_alias[owner]}
        if len(values["labels"]) == 1:
            entry["label"] = next(iter(values["labels"]))
        if len(values["names"]) == 1:
            entry["name"] = next(iter(values["names"]))
        result[owner] = entry
    return result


def _classification(
    item: dict,
    aliases: dict[str, str],
) -> tuple[str, str | tuple[str, ...] | None]:
    attribution = item.get("attribution")
    if attribution == "attributed":
        owner = aliases.get(_speaker_id(item) or "")
        return ("attributed", owner) if owner is not None else ("unknown", None)
    if attribution == "overlap":
        candidates = item.get("candidate_source_ids") or []
        if not isinstance(candidates, list) or not all(
            isinstance(value, str) for value in candidates
        ):
            return "unknown", None
        return "overlap", tuple(sorted(set(candidates)))
    return "unknown", None


def _merge_compatible(left: dict, right: dict) -> bool:
    if left["end_offset"] != right["start_offset"]:
        return False
    if left["attribution"] == right["attribution"] == "unknown":
        return True
    return (
        left["attribution"] in {"attributed", "overlap"}
        and left["attribution"] == right["attribution"]
        and left.get("context") == right.get("context")
    )


def _speech_blocks(
    window_text: str,
    items: list[dict],
    window_start: int,
    aliases: dict[str, str],
) -> list[dict]:
    """Build one exact text partition, assigning conflicts and gaps to unknown."""
    valid = []
    for item in items:
        if not _valid_span(item):
            continue
        start = item["start_offset"] - window_start
        end = item["end_offset"] - window_start
        if not 0 <= start < end <= len(window_text) or window_text[start:end] != item["text"]:
            continue
        attribution, context = _classification(item, aliases)
        valid.append({"start": start, "end": end, "attribution": attribution, "context": context})

    boundaries = {0, len(window_text)}
    start_events, end_events = defaultdict(list), defaultdict(list)
    for index, item in enumerate(valid):
        boundaries.update((item["start"], item["end"]))
        classification = (item["attribution"], item["context"])
        start_events[item["start"]].append((index, classification))
        end_events[item["end"]].append(index)
    points = sorted(boundaries)
    active = {}
    blocks = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        for index in end_events[start]:
            active.pop(index, None)
        active.update(start_events[start])
        classifications = set(active.values())
        if len(classifications) != 1:
            attribution, context = "unknown", None
        else:
            attribution, context = next(iter(classifications))
        block = {
            "text": window_text[start:end],
            "start_offset": window_start + start,
            "end_offset": window_start + end,
            "attribution": attribution,
            "context": context,
        }
        if blocks and _merge_compatible(blocks[-1], block):
            blocks[-1]["text"] += block["text"]
            blocks[-1]["end_offset"] = block["end_offset"]
        else:
            blocks.append(block)

    # Carry separators only when both surrounding lexical spans have matching context.
    merged, index = [], 0
    while index < len(blocks):
        block = dict(blocks[index])
        following_index = index + 1
        while following_index + 1 < len(blocks):
            gap = blocks[following_index]
            following = blocks[following_index + 1]
            same_context = (
                block["attribution"] in {"attributed", "overlap"}
                and block["attribution"] == following["attribution"]
                and block.get("context") == following.get("context")
                and gap["attribution"] == "unknown"
                and _is_nonlexical_gap(gap)
            )
            if not same_context:
                break
            block["text"] += gap["text"] + following["text"]
            block["end_offset"] = following["end_offset"]
            following_index += 2
        merged.append(block)
        index = following_index
    return merged


def _render_speech_blocks(
    source: dict,
    speaker_window: SpeakerWindow,
    first: int,
    last: int,
    origin: int,
    aliases: dict[str, str],
    legend: dict,
) -> str:
    window_start, window_end = origin + first, origin + last
    items = speaker_window(source, window_start, window_end)
    window_text = (source.get("text") or "")[first:last]
    blocks = _speech_blocks(window_text, items, window_start, aliases)
    speakers = {
        block["context"]: legend[block["context"]]
        for block in blocks
        if block["attribution"] == "attributed" and block.get("context") in legend
    }
    speech = []
    for block in blocks:
        item = {"text": block["text"]}
        if block["attribution"] == "attributed":
            item["speaker"] = block["context"]
        else:
            item["attribution"] = block["attribution"]
        speech.append(item)
    return json.dumps(
        {"speakers": speakers, "speech": speech}, ensure_ascii=False, separators=(",", ":")
    )


def _source_prefix(source: dict) -> str:
    prefix = (
        f"[{source['id']}] {source.get('started_at', '')} "
        f"source_kind={source.get('source_kind', '')} "
        f"important_hint={source.get('important', False)} "
        f"meeting_hint_ids={source.get('meeting_hint_ids', [])} "
    )
    if source.get("subject_hint"):
        prefix += (
            "subject_hint="
            + json.dumps(source["subject_hint"], ensure_ascii=False)
            + " (navigation label only) "
        )
    return prefix


def source_prompt_pieces(
    source: dict,
    speaker_window: SpeakerWindow,
    *,
    max_chars: int,
    max_text_chars: int = 8000,
) -> list[SummarySourcePiece]:
    """Split text without loss until each compact prompt block fits the char budget."""
    text = source.get("text") or ""
    if not text:
        return []
    prefix = _source_prefix(source)
    actionable = any(
        item.get("attribution") in {"attributed", "overlap"}
        for item in source.get("speaker_evidence", [])
    )
    try:
        origin = source.get("speaker_evidence_origin", 0)
        origin = origin if type(origin) is int and origin >= 0 else 0
    except TypeError, ValueError:
        origin = 0
    safe_boundaries = {index for index, char in enumerate(text, start=1) if char.isspace()}
    preferred_boundaries = set()
    for key in ("speaker_evidence", "native_segments", "segments"):
        for item in source.get(key) or []:
            if not isinstance(item, dict):
                continue
            for field in ("start_offset", "end_offset"):
                offset = item.get(field)
                if type(offset) is not int:
                    continue
                local = offset - origin
                if 0 < local < len(text) and (text[local - 1].isspace() or text[local].isspace()):
                    safe_boundaries.add(local)
                    preferred_boundaries.add(local)
    full_items = speaker_window(source, origin, origin + len(text)) if actionable else []
    if actionable:
        speaker_aliases = _speaker_aliases(full_items)
        speaker_legend = _speaker_legend(full_items, speaker_aliases)
    else:
        speaker_legend = {}

    def render(first: int, last: int) -> str:
        value = prefix
        if actionable:
            value += (
                "\nOrdered speech blocks (join each block's text in order to reconstruct this "
                "exact transcript window; speaker identifies an owner; unknown and overlap have "
                "no owner): "
                + _render_speech_blocks(
                    source,
                    speaker_window,
                    first,
                    last,
                    origin,
                    speaker_aliases,
                    speaker_legend,
                )
            )
        else:
            value += text[first:last]
        if first == 0 and source.get("diary_summaries"):
            value += "\nDiary recaps: " + json.dumps(
                source["diary_summaries"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return value

    pieces, first = [], 0
    while first < len(text):
        upper = min(len(text), first + max_text_chars)

        def fits(last: int) -> bool:
            return len(render(first, last)) <= max_chars

        low, high, best = first + 1, upper, first
        while low <= high:
            middle = (low + high) // 2
            if fits(middle):
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        # Serialization size can change when a span changes from a clipped unknown
        # fragment to its full form. Verify the selected endpoint and shrink safely.
        while best > first and not fits(best):
            best -= max(1, (best - first) // 2)
        if best <= first:
            raise ValueError("summary_source_piece_exceeds_context_budget")
        safe_end = None
        if best < len(text):
            preferred = sorted(
                (boundary for boundary in preferred_boundaries if first < boundary <= best),
                reverse=True,
            )
            whitespace = sorted(
                (boundary for boundary in safe_boundaries if first < boundary <= best),
                reverse=True,
            )
            safe_end = next((boundary for boundary in preferred if fits(boundary)), None)
            if safe_end is None:
                safe_end = next((boundary for boundary in whitespace if fits(boundary)), None)
        if safe_end is not None:
            best = safe_end
        pieces.append(SummarySourcePiece(first, best, render(first, best)))
        first = best
    return pieces
