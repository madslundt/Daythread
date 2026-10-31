"""Recent-audio actions use the capture-time window, even after offline delivery."""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .domain import stamp
from .inference import validate_citations
from .semantic_metadata import segment_anchors
from .service import Memory, public_source
from .settings import translation_language
from .summary_filter import NO_CONTENT
from .translation import translation_targets


class WearableParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["summarize", "answer", "translate"]
    duration_seconds: int = Field(default=30, ge=5, le=300, strict=True)
    end: AwareDatetime
    device_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[\w-]+$")
    language: str | None = Field(default=None, max_length=35)

    @field_validator("language")
    @classmethod
    def normalized_language(cls, value: str | None) -> str | None:
        return translation_language(value) if value is not None else None


class RecentAudioPending(Exception):
    retry_after_seconds = 2


def translation_target(memory: Memory, requested: str | None) -> str:
    language, _ = translation_targets(memory, requested)
    if not language:
        raise HTTPException(422, "Choose a translation language enabled in Settings")
    return language


def excerpt(source: dict, start: datetime, end: datetime) -> dict:
    """Use trustworthy segment timing; never invent word-level timestamps."""
    source_start = datetime.fromisoformat(source["started_at"])
    source_end = datetime.fromisoformat(source["ended_at"])
    if start <= source_start and source_end <= end:
        return source | {"window_timing": "source"}
    anchors = segment_anchors(source)
    if not anchors:
        return source | {"window_timing": "overlapping_chunk"}
    left, right = (start - source_start).total_seconds(), (end - source_start).total_seconds()
    selected = [a for a in anchors if a[2]["start"] < right and a[2]["end"] > left]
    text = " ".join(source["text"][a:b] for a, b, _ in selected)
    return source | {"text": text, "window_timing": "segment", "approximate_window": any(
        segment["start"] < left or segment["end"] > right for _, _, segment in selected
    )}


async def generate_wearable_action(memory: Memory, parameters: dict) -> dict:
    end = datetime.fromisoformat(parameters["end"])
    start = end - timedelta(seconds=parameters["duration_seconds"])
    rows = [row for row in memory.store.window(start, end)
            if not parameters.get("device_id") or row["device_id"] == parameters["device_id"]]
    # Mobile upload barriers place this request after its audio. STT runs separately;
    # defer this job rather than returning an empty or partial answer prematurely.
    if any(row["state"] in {"queued", "transcribing"} for row in rows):
        raise RecentAudioPending()
    selected = [excerpt(row, start, end) for row in rows
                if row["state"] == "done" and row["text"]]
    selected = [row for row in selected if row["text"].strip()]
    action = parameters["action"]
    result = {
        "action": action, "start": stamp(start), "end": stamp(end),
        "duration_seconds": parameters["duration_seconds"],
        "sources": [public_source(row) | {"window_timing": next(
            (s["window_timing"] for s in selected if s["id"] == row["id"]), "source",
        )} for row in rows],
        "pending_chunks": 0, "summary": None,
        "approximate_window": any(row["window_timing"] == "overlapping_chunk"
                                  or row.get("approximate_window") for row in selected),
    }
    if not selected:
        return result | {"text": "No transcribed speech is available in this window."}
    memory.settings.require_inference("llm")
    if action == "translate":
        language = translation_target(memory, parameters.get("language"))
        text = await memory.inference.translate(
            "\n".join(row["text"] for row in selected), language,
        )
        result["language"] = language
    elif action == "answer":
        text = await memory.inference.answer_recent(selected)
        text = validate_citations(text, selected)
    else:
        text = await memory.inference.summarize(
            selected, "a concise recap of this recent audio window, suitable for a notification",
        )
        if text == NO_CONTENT:
            text = "No transcribed speech is available in this window."
        text = validate_citations(text, selected)
        result["summary"] = text
    return result | {"text": text}


def wearable_notice(result: dict) -> tuple[str, str]:
    from .completion_events import preview

    title = {"summarize": "Recent audio summary", "answer": "Answer to recent question",
             "translate": "Recent audio translation"}[result["action"]]
    return title, preview(result["text"], limit=550)
