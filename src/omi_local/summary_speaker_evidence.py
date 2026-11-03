"""Bounded, compact speaker-evidence projections for summary prompts."""

import json
import math
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


def _valid_time_pair(item: dict) -> tuple[int | float, int | float] | None:
    start, end = item.get("start"), item.get("end")
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or start < 0 or end <= start):
        return None
    try:
        if not math.isfinite(start) or not math.isfinite(end):
            return None
    except OverflowError:
        return None
    return start, end


def _is_nonlexical_gap(item: dict) -> bool:
    text = item.get("text")
    return (
        item.get("attribution") == "unknown"
        and isinstance(text, str)
        and bool(text)
        and all(
            char.isspace() or unicodedata.category(char).startswith("P")
            for char in text
        )
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


def _overlap_runs(items: list[dict]) -> list[dict]:
    """Join only consecutive overlap words across punctuation/space separators."""
    result, index = [], 0
    while index < len(items):
        first = items[index]
        first_time = _valid_time_pair(first)
        if (first.get("attribution") != "overlap" or not first_time
                or not _valid_span(first)):
            result.append(first)
            index += 1
            continue
        candidates = tuple(first.get("candidate_source_ids") or [])
        run = [first]
        separators = []
        cursor = index + 1
        while cursor < len(items):
            candidate = items[cursor]
            candidate_time = _valid_time_pair(candidate)
            gap = None
            if _is_nonlexical_gap(candidate) and cursor + 1 < len(items):
                following = items[cursor + 1]
                following_time = _valid_time_pair(following)
                if (following.get("attribution") == "overlap" and following_time
                        and _valid_span(candidate) and _valid_span(following)
                        and tuple(following.get("candidate_source_ids") or []) == candidates
                        and candidate.get("start_offset") == run[-1].get("end_offset")
                        and candidate.get("end_offset") == following.get("start_offset")):
                    gap = candidate
                    candidate = following
                    candidate_time = following_time
            if (candidate.get("attribution") != "overlap" or not candidate_time
                    or not _valid_span(candidate)
                    or tuple(candidate.get("candidate_source_ids") or []) != candidates
                    or candidate.get("start_offset") != (
                        gap.get("end_offset") if gap is not None
                        else run[-1].get("end_offset")
                    )):
                break
            separators.append(gap)
            run.append(candidate)
            cursor += 2 if gap is not None else 1

        if len(run) == 1:
            result.append(first)
            index += 1
            continue
        text = first["text"]
        times = [[first["start_offset"], first["end_offset"], *first_time]]
        for gap, item in zip(separators, run[1:]):
            text += (gap["text"] if gap is not None else "") + item["text"]
            pair = _valid_time_pair(item)
            times.append([item["start_offset"], item["end_offset"], *pair])
        result.append({
            "text": text,
            "start_offset": first["start_offset"],
            "end_offset": run[-1]["end_offset"],
            "attribution": "overlap",
            "candidate_source_ids": list(candidates),
            "overlap_word_times": times,
        })
        index = cursor
    return result


def compact_speaker_evidence(
    items: list[dict], speaker_aliases: dict[str, str] | None = None,
) -> dict:
    """Whitelist evidence fields and put reviewed names in one scoped legend."""
    items = _overlap_runs(items)
    aliases = _speaker_aliases(items) if speaker_aliases is None else speaker_aliases
    spans, names_by_label = [], defaultdict(set)
    owner_metadata: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"labels": set(), "names": set()},
    )
    for item in items:
        if item.get("attribution") != "attributed":
            continue
        alias = aliases.get(_speaker_id(item) or "")
        if alias is None:
            continue
        label, name = item.get("speaker_label"), item.get("speaker_name")
        if isinstance(label, str) and label:
            owner_metadata[alias]["labels"].add(label)
        if isinstance(name, str) and name:
            owner_metadata[alias]["names"].add(name)

    for item in items:
        text = item.get("text")
        start, end = item.get("start_offset"), item.get("end_offset")
        attribution = item.get("attribution")
        if (not _valid_span(item) or text.isspace()
                or not isinstance(attribution, str)
                or attribution not in {"attributed", "overlap", "unknown"}):
            continue
        span = {
            "text": text,
            "start_offset": start,
            "end_offset": end,
            "attribution": attribution,
        }
        label = item.get("speaker_label")
        name = item.get("speaker_name")
        alias = aliases.get(_speaker_id(item) or "") if attribution == "attributed" else None
        if alias is not None:
            span["owner"] = alias
        elif attribution == "attributed" and isinstance(label, str) and label:
            span["speaker_label"] = label
            if isinstance(name, str) and name:
                names_by_label[label].add(name)
        if item.get("kind") == "boundary_word":
            span["kind"] = "boundary_word"
        if item.get("attribution") == "overlap" and item.get("overlap_word_times"):
            span["word_times"] = item["overlap_word_times"]
        pair = _valid_time_pair(item)
        if pair is not None:
            span["start"], span["end"] = pair
        spans.append(span)

    speaker_names = {
        label: next(iter(names))
        for label, names in sorted(names_by_label.items())
        if len(names) == 1
    }
    # Conflicting names for one cluster indicate inconsistent/stale evidence. Leave
    # that cluster anonymous in this prompt instead of choosing a name arbitrarily.
    speakers = {}
    for alias, metadata in owner_metadata.items():
        speaker_id = next(
            key for key, value in aliases.items() if value == alias
        )
        speaker = {"id": speaker_id}
        if len(metadata["labels"]) == 1:
            speaker["label"] = next(iter(metadata["labels"]))
        if len(metadata["names"]) == 1:
            speaker["name"] = next(iter(metadata["names"]))
        speakers[alias] = speaker
    return {"speakers": speakers, "speaker_names": speaker_names, "spans": spans}


def _source_prefix(source: dict) -> str:
    prefix = (
        f"[{source['id']}] {source.get('started_at', '')} "
        f"source_kind={source.get('source_kind', '')} "
        f"important_hint={source.get('important', False)} "
        f"meeting_hint_ids={source.get('meeting_hint_ids', [])} "
    )
    if source.get("subject_hint"):
        prefix += (
            "subject_hint=" + json.dumps(source["subject_hint"], ensure_ascii=False)
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
    except (TypeError, ValueError):
        origin = 0
    safe_boundaries = {
        index for index, char in enumerate(text, start=1)
        if char.isspace()
    }
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
                if (0 < local < len(text) and
                        (text[local - 1].isspace() or text[local].isspace())):
                    safe_boundaries.add(local)
                    preferred_boundaries.add(local)
    speaker_aliases = _speaker_aliases(source.get("speaker_evidence", []))

    def render(first: int, last: int) -> str:
        value = prefix + text[first:last]
        if actionable:
            evidence = compact_speaker_evidence(
                speaker_window(source, origin + first, origin + last), speaker_aliases,
            )
            value += (
                "\nSpeaker evidence (JSON; exact transcript character spans; only listed "
                "attributed spans have an owner; overlap is unresolved; all unlisted text "
                "remains unknown): "
                + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
            )
        if first == 0 and source.get("diary_summaries"):
            value += "\nDiary recaps: " + json.dumps(
                source["diary_summaries"], ensure_ascii=False, separators=(",", ":"),
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
