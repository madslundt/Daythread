"""Derived recording titles and topic boundaries anchored in the original transcript."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .domain import stamp, utc_now
from .inference import summary_language_instruction
from .inference_errors import InferenceDisabled, InferenceUnavailable
from .semantic_metadata import segment_anchors, segment_metadata
from .summary_filter import clean_summary_text
from .topic_analysis import Analysis, generate_analysis, topic_offsets
from .topic_context import TopicContext, topic_contexts

if TYPE_CHECKING:
    from .service import Memory

logger = logging.getLogger(__name__)
VERSION = "recording-topics-5-source-language"
MAX_INPUT = 12000

SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_topic_sources (
 context_key TEXT NOT NULL, source_id TEXT NOT NULL,
 PRIMARY KEY(context_key,source_id)
);
CREATE INDEX IF NOT EXISTS recording_topic_source ON recording_topic_sources(source_id);
CREATE TRIGGER IF NOT EXISTS recording_topics_correct
AFTER UPDATE OF text,segments,source_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.segments IS NOT NEW.segments
 OR OLD.source_version IS NOT NEW.source_version BEGIN
 UPDATE audio SET topic_details='{}' WHERE id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS recording_topics_context_correct
AFTER UPDATE OF text,segments,source_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.segments IS NOT NEW.segments
 OR OLD.source_version IS NOT NEW.source_version BEGIN
 UPDATE audio SET topic_details='{}' WHERE id=NEW.id OR json_extract(topic_details,'$.context') IN (
  SELECT context_key FROM recording_topic_sources WHERE source_id=NEW.id
 );
END;
CREATE TRIGGER IF NOT EXISTS recording_topics_context_delete AFTER DELETE ON audio BEGIN
 UPDATE audio SET topic_details='{}' WHERE json_extract(topic_details,'$.context') IN (
  SELECT context_key FROM recording_topic_sources WHERE source_id=OLD.id
 );
 DELETE FROM recording_topic_sources WHERE source_id=OLD.id;
END;
"""


class _TopicContextChanged(Exception):
    """Selected evidence changed while a topic analysis was running."""


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


async def analyze(
    memory: Memory, row: dict, progress: dict | None = None, *,
    cached_windows: list[dict] | None = None, completed_windows: list[dict] | None = None,
    on_window: Callable[[list[dict]], None] | None = None,
) -> dict:
    text = row["text"]
    model = memory.inference
    if hasattr(model, "for_task"):
        model = model.for_task("summary")
    titles, sections = [], []
    language_instruction = summary_language_instruction(memory.settings.summary_language, text)
    if progress is not None:
        progress.update(completed_chunks=0, total_chunks=sum(1 for _ in input_windows(text)))
    for window_index, (base, excerpt) in enumerate(input_windows(text)):
        task = (
            'Give this recording a concise, descriptive title and divide its transcript into '
            'consecutive topic sections. Return JSON: {"title":"...","continues_previous":false,'
            '"sections":'
            '[{"title":"...","start_quote":"..."}]}. '
            'Use the fewest useful sections. Default to one section for a coherent discussion; '
            'split only for a sustained, clearly different subject. Keep related subtopics, '
            'examples, brief tangents, clarifications and decisions together. A short recording '
            'usually needs one to three sections, not one per detail or sentence. '
            'A language switch, speaker change or processing window is not a topic change. '
            'Audio upload boundaries and elapsed time are not topic changes either. '
            'Choose boundaries where the discussion actually moves to a different subject, '
            'starting with the sentence introducing it. Titles must name the specific subject '
            'so someone scanning a list can find the right passage. '
            'Reuse the same concise topic title when a subject continues. '
            'Each start_quote must copy an exact, unique, contiguous phrase (8–300 characters) '
            'from the beginning of that topic in the supplied transcript, in transcript order '
            '(use the full text for transcripts shorter than eight characters). '
            'The first section must begin at the start of the transcript. Include every topic '
            'through the end. Titles should describe what was discussed without endorsing '
            'unverified claims. Do not add summaries or rewrite transcript words.'
            + language_instruction
        )
        if sections:
            task += (
                '\nThis excerpt continues the same recording. Set continues_previous=true '
                'if its opening still discusses the preceding subject, even if you would '
                'phrase its title differently. Otherwise use false. The preceding subject '
                'and transcript tail below are context only; copy start_quote from the new '
                'excerpt, never from this context.\n'
                + json.dumps({"previous_subject": sections[-1]["title"],
                              "previous_transcript_tail": text[max(0, base - 1200):base]},
                             ensure_ascii=False)
            )
        cached = bool(cached_windows and window_index < len(cached_windows))
        if cached:
            result = Analysis.model_validate(cached_windows[window_index])
            offsets = topic_offsets(result, excerpt)
        else:
            result, offsets = await generate_analysis(model, task, excerpt)
        titles.append(result.title)
        for index, (topic, start) in enumerate(zip(result.sections, offsets, strict=True)):
            normalized = " ".join(re.findall(r"\w+", topic.title.casefold()))
            last_title = sections[-1]["title"] if sections else ""
            last_normalized = " ".join(re.findall(r"\w+", last_title.casefold()))
            if sections and ((index == 0 and result.continues_previous)
                             or (normalized and normalized == last_normalized)):
                continue
            sections.append({"title": topic.title, "start_offset": base + start})
        if completed_windows is not None:
            completed_windows.append(result.model_dump())
            if on_window is not None and not cached:
                on_window(completed_windows)
        if progress is not None:
            progress["completed_chunks"] += 1
    for index, section in enumerate(sections):
        section["end_offset"] = (
            sections[index + 1]["start_offset"] if index + 1 < len(sections) else len(text)
        )
    names = titles if len(titles) == 1 else [section["title"] for section in sections]
    unique_titles = {" ".join(re.findall(r"\w+", title.casefold())): title for title in names}
    title = " · ".join(unique_titles.values())[:160]
    return {"title": title, "sections": sections}


def topic_fingerprint(memory: Memory) -> str:
    identity = {
        "version": VERSION, "model": memory.settings.llm_route("summary").identity,
        "language": memory.settings.summary_language,
        "thinking": memory.settings.effective_summary_thinking,
        "thinking_token_budget": memory.settings.llm_summary_thinking_token_budget,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def source_sections(rows: list[dict], result: dict, context: str) -> dict[str, dict]:
    """Intersect semantic chapters with originals, preserving exact local offsets."""
    offset, prepared = 0, {}
    for row in rows:
        text = row["text"] or ""
        if not text.strip():
            prepared[row["id"]] = {"sections": []}
            continue
        end = offset + len(text)
        anchors = segment_anchors(row)
        sections = []
        for section in result["sections"]:
            left, right = max(offset, section["start_offset"]), min(end, section["end_offset"])
            if left >= right:
                continue
            span = {"start_offset": left - offset, "end_offset": right - offset}
            timing = segment_metadata(row, span, anchors)
            sections.append({
                "title": section["title"], **span,
                "chapter_id": f'{context}:{section["start_offset"]}',
                **{key: timing[key] for key in ("start_seconds", "end_seconds") if key in timing},
            })
        prepared[row["id"]] = {"title": result["title"], "sections": sections}
        offset = end + 1  # One newline joins neighboring nonempty source texts.
    return prepared


async def organize_context(
    memory: Memory, context: TopicContext, now: datetime, *, force: bool = False,
) -> bool:
    fingerprint = topic_fingerprint(memory)
    loaded = memory.store.get_audio_many(context.ids)
    if len(loaded) != len(context.ids):
        return False
    rows = [loaded[key] for key in context.ids]
    previous = json.loads(rows[0]["topic_details"] or "{}").get("analysis_checkpoint", {})
    cached_windows = previous.get("windows") if (
        not force and previous.get("fingerprint") == fingerprint
        and previous.get("context") == context.key
        and previous.get("window_size") == MAX_INPUT
    ) else None
    completed_windows = []

    def unchanged(db) -> bool:
        if (topic_fingerprint(memory) != fingerprint or not any(
            current.key == context.key and current.ready for current in topic_contexts(db, now)
        )):
            return False
        current = db.execute(
            "SELECT id,text,segments FROM audio WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(context.ids),),
        )
        return all(row[field] == loaded[row["id"]][field]
                   for row in current for field in ("text", "segments"))

    def checkpoint(windows: list[dict]) -> None:
        with memory.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not unchanged(db):
                raise _TopicContextChanged
            details = json.loads(db.execute(
                "SELECT topic_details FROM audio WHERE id=?", (context.ids[0],),
            ).fetchone()[0] or "{}")
            details["analysis_checkpoint"] = {
                "fingerprint": fingerprint, "context": context.key,
                "window_size": MAX_INPUT, "windows": windows,
            }
            db.execute("UPDATE audio SET topic_details=? WHERE id=?",
                       (json.dumps(details, ensure_ascii=False), context.ids[0]))
            db.executemany("INSERT OR IGNORE INTO recording_topic_sources VALUES (?,?)",
                           [(context.key, key) for key in context.ids])

    try:
        with memory.processing_activity("organizing", progress={}) as progress:
            text = "\n".join(row["text"] for row in rows if (row["text"] or "").strip())
            meaningful, _ = clean_summary_text(text)
            if meaningful:
                result = await analyze(
                    memory, {"text": text}, progress, cached_windows=cached_windows,
                    completed_windows=completed_windows, on_window=checkpoint,
                )
                prepared = source_sections(rows, result, context.key)
            else:
                prepared = {key: {"sections": []} for key in context.ids}
    except _TopicContextChanged:
        return True
    except InferenceUnavailable as exc:
        retry_at = stamp(
            max(now, utc_now()) + timedelta(seconds=exc.retry_after_seconds),
        )
        prepared = {key: {"retry_at": retry_at} for key in context.ids}
    except InferenceDisabled:
        return False
    except Exception as exc:
        logger.warning("Recording topic analysis will retry (%s)", type(exc).__name__)
        prepared = {key: {"retry_at": stamp(max(now, utc_now()) + timedelta(minutes=1))}
                    for key in context.ids}
    if completed_windows and "retry_at" in prepared[context.ids[0]]:
        prepared[context.ids[0]]["analysis_checkpoint"] = {
            "fingerprint": fingerprint, "context": context.key,
            "window_size": MAX_INPUT, "windows": completed_windows,
        }
    # Publish all parts together, rejecting corrections, late parts and changed grouping.
    with memory.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not unchanged(db):
            return True
        for key, result in prepared.items():
            details = result | {"fingerprint": fingerprint, "context": context.key}
            db.execute("UPDATE audio SET topic_details=? WHERE id=?",
                       (json.dumps(details, ensure_ascii=False), key))
            db.execute("INSERT OR IGNORE INTO recording_topic_sources VALUES (?,?)",
                       (context.key, key))
        db.execute(
            "DELETE FROM recording_topic_sources WHERE context_key NOT IN "
            "(SELECT coalesce(json_extract(topic_details,'$.context'),'') FROM audio "
            "UNION SELECT coalesce(json_extract(topic_details,'$.analysis_checkpoint.context'),'') "
            "FROM audio)"
        )
    return True


async def ensure_topics(memory: Memory, source_ids: set[str], *, force: bool = False) -> None:
    """Recap preparation shares the background worker's subject map and cache."""
    if not memory.settings.llm_enabled or not hasattr(memory.inference, "generate_json"):
        return
    async with memory.topic_lock:
        now = utc_now()
        with memory.store.connect() as db:
            contexts = topic_contexts(db, now)
        for context in contexts:
            if (source_ids.intersection(context.ids)
                    and context.ready
                    and (force or context.due(topic_fingerprint(memory), stamp(now)))):
                if force:
                    await organize_context(memory, context, now, force=True)
                else:
                    await organize_context(memory, context, now)


async def process_topics(memory: Memory, now: datetime | None = None) -> bool:
    """One chapter analysis per logical recording, independently of STT request limits."""
    if not memory.settings.llm_enabled:
        return False
    async with memory.topic_lock:
        now = now or utc_now()
        fingerprint = topic_fingerprint(memory)
        with memory.store.connect() as db:
            context = next((item for item in topic_contexts(db, now)
                            if item.due(fingerprint, stamp(now))), None)
        return await organize_context(memory, context, now) if context else False


async def run_topics(memory: Memory) -> None:
    while True:
        try:
            worked = await process_topics(memory)
        except Exception as exc:
            logger.warning("Recording topic worker will retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.2 if worked else 5)
