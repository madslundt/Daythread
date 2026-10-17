"""Plaud's documented transcript exports; no private consumer API is used.

Plaud documents TXT and SRT transcript exports at
https://support.plaud.ai/hc/en-us/articles/51023259082393
Consumer account access requires Plaud's own OAuth CLI/MCP, not an API key:
https://support.plaud.ai/hc/en-us/articles/60726890231449
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .domain import stamp
from .vendor_imports import TranscriptSegment, VendorRecording

EXPORT_NAMESPACE = "https://www.plaud.ai/exports"
EXPORT_HELP_URL = (
    "https://support.plaud.ai/hc/en-us/articles/51023259082393-"
    "Export-recordings-transcripts-and-summaries"
)
MAX_EXPORT_BYTES = 1_000_000
_TIMING = re.compile(
    r"(?P<start>\d{2,3}:[0-5]\d:[0-5]\d,\d{3})[ \t]+-->[ \t]+"
    r"(?P<end>\d{2,3}:[0-5]\d:[0-5]\d,\d{3})"
)


class PlaudExport(BaseModel):
    """Capture dates describe the original recording, never the exported file."""

    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=MAX_EXPORT_BYTES)
    format: Literal["txt", "srt"] = "srt"
    source_id: str | None = Field(default=None, min_length=1, max_length=200)
    started_at: AwareDatetime
    ended_at: AwareDatetime

    @field_validator("source_id")
    @classmethod
    def valid_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("A recording ID must contain non-whitespace characters")
        return value

    @field_validator("content")
    @classmethod
    def bounded_content(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_EXPORT_BYTES:
            raise ValueError("Export exceeds 1 MB")
        if not value.lstrip("\ufeff").strip() or "\x00" in value:
            raise ValueError("Export must contain transcript text")
        return value


def _seconds(value: str) -> float:
    hours, minutes, seconds, milliseconds = map(int, re.split("[:,]", value))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _srt_segments(content: str) -> list[TranscriptSegment]:
    # Preserve speaker labels as exported text: Plaud does not document their syntax.
    blocks = re.split(r"\n[ \t]*\n", content.strip())
    if len(blocks) > 10000:
        raise ValueError("Export has too many transcript segments")
    segments: list[TranscriptSegment] = []
    previous_index = 0
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise ValueError("Invalid SRT subtitle block")
        index = int(lines[0])
        timing = _TIMING.fullmatch(lines[1].strip())
        if index <= previous_index or not timing:
            raise ValueError("Invalid SRT sequence or timestamp")
        text = "\n".join(lines[2:]).strip()
        if not text:
            raise ValueError("Empty SRT subtitle")
        segment = TranscriptSegment(
            start=_seconds(timing["start"]), end=_seconds(timing["end"]), text=text,
        )
        if segments and segment.start < segments[-1].start:
            raise ValueError("SRT subtitles must be in capture order")
        segments.append(segment)
        previous_index = index
    return segments


def normalize_plaud_export(body: PlaudExport) -> VendorRecording:
    """Normalize original transcript content for the shared atomic archive importer.

    TXT has no fabricated segment times. SRT uses original relative subtitle times.
    If an upstream recording ID is absent, identity includes the unchanged export
    and capture interval so equal speech recorded on different days stays separate.
    Re-exporting altered content requires the original recording ID to deduplicate.
    """
    identity = json.dumps(
        [body.format, stamp(body.started_at), stamp(body.ended_at), body.content],
        ensure_ascii=False, separators=(",", ":"),
    )
    source_id = body.source_id or f"export-{hashlib.sha256(identity.encode()).hexdigest()}"
    content = body.content.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    segments = _srt_segments(content) if body.format == "srt" else []
    text = "\n".join(segment.text for segment in segments) if segments else content.strip()
    return VendorRecording(
        source_id=source_id, started_at=body.started_at, ended_at=body.ended_at,
        text=text, segments=segments,
    )
