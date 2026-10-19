"""Prepare transcribed recordings once, then synthesize versioned daily recaps."""

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from .completion_events import publish_recap
from .conversation_state import Conversation, activity, finished_file, manual_upload, recordings
from .domain import EvidenceChanged, stamp
from .embedding_chunking import topic_regions
from .inference import InferenceError, validate_citations
from .recording_topics import VERSION as TOPIC_VERSION
from .recording_topics import ensure_topics
from .summary_filter import NO_CONTENT
from .summary_filter import VERSION as SUMMARY_FILTER_VERSION
from .vendor_imports import transcript_import
from .voice_activity import analyze_voice

if TYPE_CHECKING:
    from .service import Memory

PROMPT_VERSION = f"subject-chapters-8-{TOPIC_VERSION}-{SUMMARY_FILTER_VERSION}"
DAY_PROMPT_VERSION = "daily-topics-3-subject-continuity"
BOUNDARY_NOTE = (
    "Uploaded recordings are included after transcription. Live conversations wait for the "
    "configured pause in recorded speech. Missing uploads do not count as silence. "
    "Conversation boundaries are approximate."
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def topic_sources(sources: list[dict]) -> list[dict]:
    """Request copies share the transcript's subjects while retaining original UUIDs."""
    result = []
    for source in sources:
        text, sections = source["text"] or "", source.get("sections") or []
        for start, end, index in topic_regions(text, sections):
            subject = sections[index]["title"] if index is not None else ""
            result.append({**source, "text": text[start:end], "subject_hint": subject})
    return result


class Recaps:
    def __init__(self, memory: Memory):
        self.memory = memory

    def groups(self, now: datetime, db: sqlite3.Connection | None = None) -> list[Conversation]:
        return recordings(self.memory.store.recap_snapshot(db), now,
                          5 * 60)

    def for_day(self, groups: list[Conversation], day: date) -> list[Conversation]:
        start, end = map(stamp, self.memory.day_window(day))
        # Cross-midnight conversations remain whole; both days disclose full source context.
        return [group for group in groups if group.start < end and group.end > start]

    def group_key(self, group: Conversation) -> str:
        settings = self.memory.settings
        return digest({
            "sources": [(r["id"], r["text"], r["source_version"], r["transcript_version"])
                        for r in group.sources],
            "boundary": (group.closed_at, group.basis, group.proof),
            "hints": group.hints,
            "model": settings.llm_route("summary").identity,
            "language": settings.summary_language.lower(), "timezone": settings.timezone,
            "prompt": PROMPT_VERSION,
        })

    def day_key(self, groups: list[Conversation]) -> str:
        return digest({
            "conversations": [self.group_key(group) for group in groups if group.ready],
            "prompt": DAY_PROMPT_VERSION,
        })

    def guard(
        self, day: date, now: datetime, groups: list[Conversation],
    ) -> Callable[[sqlite3.Connection], None]:
        expected = {self.group_key(group) for group in groups if group.ready}

        def check(db):
            current = self.for_day(self.groups(now, db), day)
            actual = {self.group_key(group) for group in current if group.ready}
            # New independent recordings can join the next recap. Corrections,
            # deletion or regrouping of selected evidence must still reject it.
            if not expected <= actual:
                raise EvidenceChanged("Transcribed recording evidence changed; retry the recap")
        return check

    def status(self, groups: list[Conversation]) -> dict:
        ready = [group for group in groups if group.ready]
        pending = [group for group in groups if not group.ready]
        note = BOUNDARY_NOTE
        if any(r["source_kind"] == "omi_audio_webhook" for g in groups for r in g.sources):
            note += (" Raw Omi audio forwarding has estimated timestamps; import finished Omi "
                     "conversations to establish reliable conversation boundaries.")
        return {
            "included_through": max((g.end for g in ready), default=None),
            "deferred_conversations": len(pending),
            "deferred_sources": sum(len(g.sources) for g in pending),
            "waiting_for_pause": sum(
                not g.closed_at and any(not finished_file(r) for r in g.sources) for g in pending
            ),
            "pending_upload_sources": sum(
                len(g.sources) for g in pending if all(finished_file(r) for r in g.sources)
            ),
            "boundary_note": note,
        }

    def cached(self, day: date, now: datetime, groups: list[Conversation] | None = None) -> dict:
        memory = self.memory
        start, end = memory.day_window(day)
        groups = self.for_day(self.groups(now) if groups is None else groups, day)
        row = memory.store.diary(day.isoformat())
        if row:
            result = json.loads(row["content"])
            stale = row["fingerprint"] != self.day_key(groups)
            pending_ids = {r["id"] for g in groups if not g.ready for r in g.sources}
            if any(source["id"] in pending_ids for source in result["sources"]):
                # Late speech or a related upload can reopen selected evidence.
                # Hide its old interpretation while that group is still pending.
                result.update(summary=None, sources=[], output_id=None, evidence=None,
                              included_through=None, no_recap_content=False)
            # Preserve the generation-time cutoff; live pending counts are separate.
            return result | self.status(groups) | {
                "included_through": result.get("included_through"), "stale": stale,
                "completeness": memory.store.completeness(start, end),
            }
        result = memory.window_data(start, end)
        return result | self.status(groups) | {
            "sources": [], "day": day.isoformat(), "timezone": memory.settings.timezone,
            "generated_at": None, "stale": False, "no_recap_content": False,
            "included_through": None,
        }

    async def observe(self) -> None:
        """Backfill voice observations used for live pause detection and speaker grouping."""
        store = self.memory.store
        for row in store.recap_snapshot()["rows"]:
            if (manual_upload(row) or transcript_import(row["source_kind"])
                    or row["state"] != "done" or activity(row)):
                continue
            if row["audio_available"] and await asyncio.to_thread(store.has_audio, row["id"]):
                data = await asyncio.to_thread(store.read_audio, row["id"])
                value = await asyncio.to_thread(analyze_voice, data)
                store.save_recap_activity(row["id"], row["digest"], value)

    def sources(self, group: Conversation) -> list[dict]:
        from .service import public_source

        hints = group.hints
        rows = self.memory.store.get_audio_many([
            r["id"] for r in group.sources if r["state"] == "done" and r["text"]
        ])
        sources = [public_source(rows[r["id"]]) for r in group.sources if r["id"] in rows]
        for source in sources:
            manual = manual_upload(source)
            source["important"] = not manual and any(
                source["started_at"] < mark["context_end"]
                and source["ended_at"] > mark["context_start"] for mark in hints["markers"]
            )
            source["meeting_hint_ids"] = [] if manual else [
                meeting["id"] for meeting in hints["meetings"]
                if source["started_at"] < meeting["context_end"]
                and source["ended_at"] > meeting["context_start"]
            ]
        return sources

    async def prepare_group(
        self, group: Conversation, guard: Callable[[sqlite3.Connection], None],
    ) -> str:
        memory = self.memory
        key = self.group_key(group)
        cached = memory.store.recap_summary(key)
        if cached is not None:
            return cached
        await ensure_topics(memory, {source["id"] for source in group.sources})
        sources = self.sources(group)
        if not sources:
            summary = NO_CONTENT
        else:
            summary = await memory.inference.summarize(
                topic_sources(sources),
                "notes from a finished upload or a live conversation followed by a long pause. "
                "An uploaded file may contain only part of a conversation. "
                "Preserve short meaningful answers in context, dates, "
                "agreements and decisions without claiming the conversation has ended. "
                "Omit fillers, unintelligible fragments and isolated "
                "words without supported meaning. Do not infer events from background noise. "
                f"If nothing useful is supported, return exactly {NO_CONTENT} and nothing else. "
                "Multiple sources can record the same meeting. Describe shared points once, "
                "retain every unique detail from each source including its beginning and end, "
                "and disclose conflicting readings instead of guessing. "
                "Keep related details together by subject. A recording can cover several "
                "subjects; preserve each without forcing event, decision or question categories. "
                "Use specific subject headings (##) matching the supplied subject hints when "
                "they fit the transcript. Hints are navigation labels, never additional facts. "
                "A subject continuing across audio parts belongs in the same section, even "
                "when its title is phrased differently. Split at sustained changes of subject, "
                "never at upload boundaries, timestamps, speaker changes or language switches. "
                "Do not repeat the same point in different categories or add empty placeholders. "
                "Otherwise preserve original source UUID citations for every useful point",
            )
            summary = validate_citations(summary, sources).strip()
            if summary != NO_CONTENT and not any(s["id"].lower() in summary.lower()
                                                 for s in sources):
                raise InferenceError("Conversation notes require original source citations")
        memory.store.save_recap_summary(key, summary, sources, guard)
        return summary

    async def prepare(self, now: datetime) -> None:
        groups = self.groups(now)
        for group in groups:
            if not group.ready:
                continue
            # Guard the conversation itself rather than unrelated archive arrivals.
            expected = self.group_key(group)

            def guard(db, expected=expected):
                if not any(g.ready and self.group_key(g) == expected for g in self.groups(now, db)):
                    raise EvidenceChanged("Conversation changed during preparation")

            await self.prepare_group(group, guard)

    async def generate(self, day: date, now: datetime) -> dict:
        memory = self.memory
        start, end = memory.day_window(day)
        groups = self.for_day(self.groups(now), day)
        key = self.day_key(groups)
        guard = self.guard(day, now, groups)
        existing = memory.store.diary(day.isoformat())
        if existing and existing["fingerprint"] == key:
            return self.cached(day, now)
        ready = [g for g in groups if g.ready]
        result = memory.window_data(start, end) | self.status(groups)
        blocked = sum(r["state"] == "blocked" for r in result["sources"])
        sources, inputs = [], []
        for group in ready:
            notes = await self.prepare_group(group, guard)
            originals = self.sources(group)
            sources.extend(originals)
            if notes != NO_CONTENT and originals:
                inputs.extend(
                    {
                        **source,
                        "text": notes if index == 0 else "",
                        **({"citation_source_ids": [item["id"] for item in originals]}
                           if index == 0 else {}),
                    }
                    for index, source in enumerate(originals)
                )
        # Sources are originals; bounded cached notes are used only for daily synthesis.
        result.update(sources=sources, day=day.isoformat(), timezone=memory.settings.timezone,
                      model=memory.settings.llm_route("summary").model,
                      generated_at=stamp(now), no_recap_content=bool(ready) and not inputs,
                      stale=False)
        trace = {"conversation_preparation": "cached_transcribed_recordings",
                 "conversation_count": len(ready)}
        if inputs:
            purpose = (
                f"a readable diary recap for {day.isoformat()} from transcribed recording notes. "
                "Use the fewest meaningful topic sections that keep it easy to read. "
                "For a recording covering three distinct subjects, use three topic sections "
                "with one summary each, not extra category sections. "
                "Group related discussions across recordings, rather than making a section "
                "for each recording, utterance or minor detail. Use short, specific subject "
                "headings (##) that make the subject recognizable when scanning. Merge continued "
                "subjects even when the recording notes use different titles for them. "
                "Upload boundaries, pauses and elapsed time alone do not define recap topics. "
                "Write one coherent summary per topic: one concise paragraph, "
                "or two when needed to retain important details. Do not create separate short "
                "and long summaries, a preview followed by repeated bullet points, nested point "
                "lists or subheadings. Each topic summary must contain only that topic's "
                "relevant details; do not mix in unrelated subjects from the same recording. "
                "Start directly with useful content, without a recap title, date heading, "
                "introductory overview or generic highlights/events section. "
                "Keep decisions, commitments and genuine unresolved questions in their relevant "
                "topic section; do not create separate decisions/commitments or open-questions "
                "sections. Never add empty sections, placeholders or statements such as "
                "'no open questions were recorded'. "
                "Merge new details into the relevant topic. Rewrite, rename, merge or reorganize "
                "sections whenever that makes the whole recap clearer; "
                "do not just append sections. "
                "State each fact or decision only once, even when repeated across conversations, "
                "while preserving unique useful details and original source citations. "
                "Keep claims and suggestions qualified as recorded, without turning them into "
                "verified facts or decisions. Omit fillers and unsupported fragments. "
                "Do not describe pending conversations or claim the entire day is complete"
            )
            if hasattr(memory.inference, "summarize_traced"):
                summary, pipeline = await memory.inference.summarize_traced(inputs, purpose)
                trace["daily_synthesis"] = pipeline
            else:
                summary = await memory.inference.summarize(inputs, purpose)
            result["summary"] = validate_citations(summary, sources)
            result["output_id"] = str(uuid4())
            counts = {"eligible_transcripts": len(sources),
                      "pending_chunks": result["pending_chunks"],
                      "blocked_chunks": blocked,
                      "selection_observed_at": result["completeness"]["as_of"]}
            result["evidence"] = memory.evidence_metadata(
                sources, result["summary"], "transcribed_recordings", start, end,
                result["completeness"], trace, counts, kind="recap",
            )
        if ready or existing:
            memory.store.save_diary(day.isoformat(), key, result, guard=guard)
            publish_recap(memory, result, key, guard=guard)
            return self.cached(day, now)
        return result
