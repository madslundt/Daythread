"""Derived recording titles and topic boundaries anchored in the original transcript."""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .domain import stamp, utc_now
from .inference import summary_language_instruction

if TYPE_CHECKING:
    from .service import Memory

logger = logging.getLogger(__name__)
VERSION = "recording-topics-2-broad-subjects"
MAX_INPUT = 12000

SCHEMA = """
CREATE TRIGGER IF NOT EXISTS recording_topics_correct
AFTER UPDATE OF text,segments,source_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.segments IS NOT NEW.segments
 OR OLD.source_version IS NOT NEW.source_version BEGIN
 UPDATE audio SET topic_details='{}' WHERE id=NEW.id;
END;
"""


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    start_quote: str = Field(min_length=1, max_length=300)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    sections: list[Topic] = Field(min_length=1, max_length=24)


def input_windows(text: str):
    offset = 0
    while offset < len(text):
        end = min(offset + MAX_INPUT, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", offset + MAX_INPUT // 2, end),
                           text.rfind(". ", offset + MAX_INPUT // 2, end))
            if boundary >= 0:
                end = boundary + 1
        yield offset, text[offset:end]
        offset = end


async def analyze(memory: Memory, row: dict) -> dict:
    text = row["text"]
    model = memory.inference
    if hasattr(model, "for_task"):
        model = model.for_task("summary")
    titles, sections = [], []
    for base, excerpt in input_windows(text):
        task = (
            'Give this recording a concise, descriptive title and divide its transcript into '
            'consecutive topic sections. Return JSON: {"title":"...","sections":'
            '[{"title":"...","start_quote":"..."}]}. '
            'Use the fewest useful sections. Default to one section for a coherent discussion; '
            'split only for a sustained, clearly different subject. Keep related subtopics, '
            'examples, brief tangents, clarifications and decisions together. A short recording '
            'usually needs one to three sections, not one per detail or sentence. '
            'A language switch, speaker change or processing window is not a topic change. '
            'Reuse the same concise topic title when a subject continues. '
            'Each start_quote must copy an exact, unique, contiguous phrase (8–300 characters) '
            'from the beginning of that topic in the supplied transcript, in transcript order '
            '(use the full text for transcripts shorter than eight characters). '
            'The first section must begin at the start of the transcript. Include every topic '
            'through the end. Titles should describe what was discussed without endorsing '
            'unverified claims. Do not add summaries or rewrite transcript words.'
            + summary_language_instruction(memory.settings.summary_language)
        )
        result = Analysis.model_validate_json(await model.generate_json(task, excerpt))
        titles.append(result.title)
        previous = -1
        for index, topic in enumerate(result.sections):
            start = excerpt.find(topic.start_quote)
            if start < 0 or excerpt.count(topic.start_quote) != 1 or start <= previous:
                raise ValueError("Topic boundary must identify a unique ordered transcript span")
            if index == 0:
                if excerpt[:start].strip():
                    raise ValueError("First topic must cover the start of the transcript")
                start = 0
            previous = start
            normalized = " ".join(re.findall(r"\w+", topic.title.casefold()))
            last_title = sections[-1]["title"] if sections else ""
            last_normalized = " ".join(re.findall(r"\w+", last_title.casefold()))
            if sections and normalized and normalized == last_normalized:
                continue
            sections.append({"title": topic.title, "start_offset": base + start})
    for index, section in enumerate(sections):
        section["end_offset"] = (
            sections[index + 1]["start_offset"] if index + 1 < len(sections) else len(text)
        )
    unique_titles = {" ".join(re.findall(r"\w+", title.casefold())): title for title in titles}
    title = " · ".join(unique_titles.values())[:160]
    return {"title": title, "sections": sections}


async def process_topics(memory: Memory, now: datetime | None = None) -> bool:
    """One consumer per archive; retries survive restart and never requeue successful STT."""
    now = now or utc_now()
    identity = {
        "version": VERSION, "model": memory.settings.llm_route("summary").identity,
        "language": memory.settings.summary_language,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    with memory.store.connect() as db:
        found = db.execute(
            "SELECT * FROM audio WHERE state='done' AND length(trim(coalesce(text,'')))>0 "
            "AND (coalesce(json_extract(topic_details,'$.fingerprint'),'')!=? "
            "OR (json_extract(topic_details,'$.retry_at') IS NOT NULL "
            "AND json_extract(topic_details,'$.retry_at')<=?)) "
            "ORDER BY started_at,id LIMIT 1", (fingerprint, stamp(now)),
        ).fetchone()
    if found is None:
        return False
    row = dict(found)
    try:
        result = await analyze(memory, row)
    except Exception as exc:
        logger.warning("Recording topic analysis will retry (%s)", type(exc).__name__)
        result = {"retry_at": stamp(now + timedelta(minutes=1))}
    # Conditional publication rejects corrections/deletion/retention during inference.
    with memory.store.connect() as db:
        db.execute(
            "UPDATE audio SET topic_details=? WHERE id=? AND source_version=? "
            "AND transcript_version=? AND text=? AND segments=? AND state='done'",
            (json.dumps(result | {"fingerprint": fingerprint}, ensure_ascii=False), row["id"],
             row["source_version"], row["transcript_version"], row["text"], row["segments"]),
        )
    return True


async def run_topics(memory: Memory) -> None:
    while True:
        try:
            worked = await process_topics(memory)
        except Exception as exc:
            logger.warning("Recording topic worker will retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.2 if worked else 5)
