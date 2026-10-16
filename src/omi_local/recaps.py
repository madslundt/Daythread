"""Prepare completed conversations once, then synthesize versioned daily recaps."""

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from .conversation_state import Conversation, activity, conversations, manual_upload
from .domain import EvidenceChanged, stamp
from .inference import InferenceError, validate_citations
from .voice_activity import analyze_voice

if TYPE_CHECKING:
    from .service import Memory

PROMPT_VERSION = "completed-conversations-4-topic-notes"
DAY_PROMPT_VERSION = "daily-topics-1"
NO_CONTENT = "[[NO_RECAP_CONTENT]]"
BOUNDARY_NOTE = (
    "Includes completed conversations and finished uploads. Ongoing or uncertain conversations "
    "stay pending. Speech detection and silence-based boundaries are approximate; uploaded "
    "files may cover only part of a conversation. Missing uploads do not prove silence."
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Recaps:
    def __init__(self, memory: Memory):
        self.memory = memory

    def groups(self, now: datetime, db: sqlite3.Connection | None = None) -> list[Conversation]:
        return conversations(self.memory.store.recap_snapshot(db), now,
                             self.memory.settings.recap_quiet_minutes * 60)

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
            "quiet_minutes": settings.recap_quiet_minutes,
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
        self, day: date, now: datetime, expected: str,
    ) -> Callable[[sqlite3.Connection], None]:
        def check(db):
            current = self.for_day(self.groups(now, db), day)
            if self.day_key(current) != expected:
                raise EvidenceChanged("Completed conversation evidence changed; retry the recap")
        return check

    def status(self, groups: list[Conversation]) -> dict:
        ready = [group for group in groups if group.ready]
        pending = [group for group in groups if not group.ready]
        note = BOUNDARY_NOTE
        if any(r["source_kind"] == "omi_audio_webhook" for g in pending for r in g.sources):
            note += (" Raw Omi audio forwarding has estimated timestamps; import finished Omi "
                     "conversations to establish reliable conversation boundaries.")
        return {
            "included_through": max((g.end for g in ready), default=None),
            "deferred_conversations": len(pending),
            "deferred_sources": sum(len(g.sources) for g in pending),
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
                # Late evidence can reopen a conversation. Never keep its old
                # interpretation in the current recap while it is still active.
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
        """Backfill older recordings; new recordings are observed by the STT worker."""
        store = self.memory.store
        for row in store.recap_snapshot()["rows"]:
            if (manual_upload(row) or row["source_kind"] == "omi_api"
                    or row["state"] != "done" or activity(row)):
                continue
            if row["audio_available"] and Path(row["path"]).is_file():
                data = await asyncio.to_thread(Path(row["path"]).read_bytes)
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
        sources = self.sources(group)
        if not sources:
            summary = NO_CONTENT
        else:
            summary = await memory.inference.summarize(
                sources,
                "notes for one completed conversation. Assess usefulness across the entire "
                "conversation: preserve short meaningful answers in context, including dates, "
                "agreements and decisions. Omit fillers, unintelligible fragments and isolated "
                "words without supported meaning. Do not infer events from background noise. "
                f"If nothing useful is supported, return exactly {NO_CONTENT} and nothing else. "
                "Multiple sources can record the same meeting. Describe shared points once, "
                "retain every unique detail from each source including its beginning and end, "
                "and disclose conflicting readings instead of guessing. "
                "Keep related details together by subject. A recording can cover several "
                "subjects; preserve each without forcing event, decision or question categories. "
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
        await self.observe()
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
        await self.observe()
        start, end = memory.day_window(day)
        groups = self.for_day(self.groups(now), day)
        key = self.day_key(groups)
        guard = self.guard(day, now, key)
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
                inputs.extend({**source, "text": notes if index == 0 else ""}
                              for index, source in enumerate(originals))
        # Sources are originals; bounded cached notes are used only for daily synthesis.
        result.update(sources=sources, day=day.isoformat(), timezone=memory.settings.timezone,
                      model=memory.settings.llm_route("summary").model,
                      generated_at=stamp(now), no_recap_content=bool(ready) and not inputs,
                      stale=False)
        trace = {"conversation_preparation": "cached_completed_conversations",
                 "conversation_count": len(ready)}
        if inputs:
            purpose = (
                f"a readable diary recap for {day.isoformat()} from completed conversation notes. "
                "Use the fewest meaningful topic sections that keep it easy to read. "
                "For a recording covering three subjects, use one compact section with three "
                "clearly labeled points or three topic sections, not extra category sections. "
                "Group related discussions across recordings, rather than making a section "
                "for each recording, utterance or minor detail. Use short, specific subject "
                "headings (##) and concise bullet points; use a flat structure "
                "without subheadings. "
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
                sources, result["summary"], "completed_conversations", start, end,
                result["completeness"], trace, counts, kind="recap",
            )
        if ready or existing:
            memory.store.save_diary(day.isoformat(), key, result, guard=guard)
        return result
