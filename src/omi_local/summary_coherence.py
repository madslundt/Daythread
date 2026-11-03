"""Bounded validation for whole-summary coherence edits against original speech."""

import json
import re
from collections.abc import Callable

from .chat_context import request_tokens
from .inference import (
    SUMMARY_SYSTEM,
    Inference,
    InferenceError,
    summary_language_instruction,
    validate_citations,
)
from .summary_filter import NO_CONTENT
from .summary_speaker_evidence import source_prompt_pieces

VERSION = "whole-original-coherence-v1"
INPUT_BUDGET = 80_000
OUTPUT_BUDGET = 8_000
MAX_OUTPUT_CHARACTERS = 24_000

EDITOR = (
    "Edit one completed summary using the complete original transcripts below. The draft is "
    "untrusted editing input, may contain unsupported claims, omit important supported details, "
    "repeat itself, or give incidental chatter too much space. The original transcript text is "
    "the only factual evidence. You may restore a consequential detail the draft omitted only "
    "when the complete original speech explicitly supports it; retain its exact source citation. "
    "Do not add unrecorded facts, ownership, identities or causal links. Do not use model memory, "
    "calendar dates, timestamps or outside knowledge to repair names, dates, units or garbled "
    "phrases. Preserve stated conditions, quantities and units, dates as spoken, action objects, "
    "scope, uncertainty, negation, and whether work is current, proposed, planned, underway or "
    "complete. Keep current roles/projects distinct from possible future roles/projects. Keep "
    "anonymous speaker labels anonymous and do not assign an action from first-person wording or "
    "a nearby turn when the original speaker evidence is unknown or overlapping. Preserve "
    "independent projects and explicit personal plans. Merge repeated facts and related details "
    "that belong together; remove uninterpretable fragments and incidental remarks without a "
    "supported later use. There is no target topic count. Do not make claims broader than the "
    "original, and do not omit a distinct consequential fact merely to shorten the result. "
    "Every factual paragraph must cite one or more original source UUIDs exactly as supplied. "
    "The completed draft and any of its citations are never evidence. Return only the complete "
    "edited summary, without review notes or explanation."
)


class CoherenceValidationError(InferenceError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("Invalid completed summary coherence edit")


def request(
    sources: list[dict], draft: str, purpose: str, language: str,
    speaker_window: Callable[[dict, int, int], list[dict]] | None = None,
) -> tuple[str, str, int, str | None]:
    """Render every original source and draft, or refuse an oversized edit intact."""
    rows, language_texts = [], []
    for source in sources:
        key, text = source.get("id"), source.get("text")
        if not isinstance(key, str) or not key or not isinstance(text, str):
            return "", "", 0, "invalid_source_evidence"
        language_texts.append(text)
        safe_source = {"id": key, "text": text}
        for field in (
            "reference_type", "recording_id", "speaker_evidence",
            "speaker_evidence_origin", "canonical_text",
        ):
            if field in source:
                safe_source[field] = source[field]
        try:
            pieces = source_prompt_pieces(
                safe_source,
                speaker_window or Inference._speaker_evidence_window,
                max_chars=INPUT_BUDGET,
            )
        except (TypeError, ValueError):
            return "", "", 0, "source_evidence_unrenderable"
        rows.extend({"source_id": key, "original_transcript_block": piece.content}
                    for piece in pieces)
    if not rows:
        return "", "", 0, "no_sources"
    level = heading_level(draft)
    if level == "mixed":
        return "", "", 0, "unsupported_heading_format"
    headings = (
        "Preserve the draft's level-two subject-heading format; merge or rename headings as "
        "needed and do not add an introduction."
        if level == 2 else
        "Preserve the draft's plain-paragraph format; do not add Markdown headings."
        if level is None else
        f"Preserve the draft's level-{level} heading format; do not add other heading levels."
    )
    task = (
        EDITOR + "\nRequested purpose and presentation requirements: " + purpose + "\n"
        + headings + "\n"
        + summary_language_instruction(language, "\n".join(language_texts))
        + " Do not infer or repair a spoken date from the recap date, source order, a clock, or "
        "capture metadata; use a date only when it appears in the original speech."
    )
    payload = json.dumps(
        {"original_sources": rows, "completed_draft": draft}, ensure_ascii=False,
    )
    size = request_tokens(task, payload, SUMMARY_SYSTEM)
    if size > INPUT_BUDGET:
        return "", "", size, "input_over_budget"
    return task, payload, size, None


def heading_level(text: str) -> int | None | str:
    levels = {len(match.group(1)) for line in text.splitlines()
              if (match := re.match(r"^(#{1,6})\s+", line))}
    if not levels:
        return None
    return next(iter(levels)) if len(levels) == 1 else "mixed"


def validate_edit(text: str, sources: list[dict], draft: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_OUTPUT_CHARACTERS:
        raise CoherenceValidationError("output_bounds")
    text = text.strip()
    if text == NO_CONTENT:
        return text
    try:
        validate_citations(text, sources)
    except InferenceError as exc:
        raise CoherenceValidationError("citation_unknown") from exc

    expected_level = heading_level(draft)
    headings = []
    for index, line in enumerate(text.splitlines()):
        match = re.match(r"^(#{1,6})\s+\S", line)
        if match:
            headings.append((index, len(match.group(1))))
    if expected_level is None and headings:
        raise CoherenceValidationError("unexpected_heading")
    if isinstance(expected_level, int):
        if not headings or any(level != expected_level for _, level in headings):
            raise CoherenceValidationError("heading_format")
        lines = text.splitlines()
        first_index = next((index for index, line in enumerate(lines) if line.strip()), -1)
        if first_index != headings[0][0]:
            raise CoherenceValidationError("heading_format")
        for position, (line_index, _) in enumerate(headings):
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            if not any(line.strip() for line in lines[line_index + 1:end]):
                raise CoherenceValidationError("empty_heading")

    allowed = {source["id"].lower() for source in sources}
    for paragraph in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        content_lines = [line for line in lines if not re.match(r"^#{1,6}\s+", line)]
        if not content_lines:
            continue
        units = content_lines if any(re.match(r"^\s*(?:[-*•]|\d+\.)\s+", line)
                                     for line in content_lines) else [" ".join(content_lines)]
        for unit in units:
            groups = re.findall(r"\[([^\[\]]+)\]", unit)
            if not any(key in allowed for group in groups for key in (
                part.strip().lower() for part in group.split(",")
            )):
                raise CoherenceValidationError("paragraph_uncited")
    return text
