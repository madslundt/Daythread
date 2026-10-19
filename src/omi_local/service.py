import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .audio import coverage_gaps
from .completeness import audio_availability, revision
from .completion_events import generating_job
from .conversation_state import meeting_timeline
from .domain import ChatScopeConflict, ChatScopeNotFound, stamp, utc_now
from .hybrid import HybridRetriever, bound_sources
from .inference import Inference, InferenceError, validate_citations
from .recaps import Recaps
from .retrieval import archive_recall, contextual_query, date_scope, topic_query
from .settings import Settings
from .speakers import Speakers
from .store import Store
from .summary_filter import NO_CONTENT
from .usage import UsageLedger
from .vendor_catalog import vendor_definition
from .vendor_imports import transcript_import
from .voices import Voices


def public_source(row: dict) -> dict:
    result = {
        key: row[key] for key in ("id", "device_id", "started_at", "ended_at", "state", "text")
    }
    result["audio_available"] = bool(row.get("audio_available", True))
    result["segments"] = json.loads(row.get("segments", "[]"))
    result["source_version"] = row.get("source_version", "")
    result["source_kind"] = row.get("source_kind", "")
    result["source_id"] = row.get("source_id", "")
    result["timestamp_basis"] = (
        "receipt_end_estimate" if row.get("source_kind") == "omi_audio_webhook" else "capture"
    )
    if row.get("tags"):
        result["tags"] = row["tags"]
    result["source_metadata"] = json.loads(row.get("source_metadata", "{}"))
    if row.get("manual_location"):
        result["manual_location"] = row["manual_location"]
    vendor_id = result["source_metadata"].get("vendor_id")
    if not vendor_id and transcript_import(result["source_kind"]):
        vendor_id = result["source_kind"].rsplit("_", 1)[0]
    if not vendor_id and (result["source_kind"] == "omi_audio_webhook" or
                          result["device_id"].startswith("omi")):
        vendor_id = "omi"
    if vendor_id:
        result["vendor_id"] = vendor_id
        try:
            result["vendor_label"] = vendor_definition(vendor_id).label
        except ValueError:
            result["vendor_label"] = vendor_id
    result["transcription_details"] = json.loads(row.get("transcription_details", "{}"))
    topics = json.loads(row.get("topic_details") or "{}")
    result["title"] = topics.get("title")
    result["sections"] = topics.get("sections", [])
    for key in (
        "received_at",
        "processing_started_at",
        "processed_at",
        "error_code",
        "transcription_outcome",
    ):
        result[key] = row.get(key)
    result["audio_availability"] = audio_availability(row)
    result["transcript_version"] = row.get("transcript_version")
    for field in ("matches", "context_for"):
        if field in row:
            result[field] = row[field]
    if "alternate_sources" in row:
        result["alternate_sources"] = [public_source(source) for source in row["alternate_sources"]]
    return result


class Memory:
    def __init__(self, store: Store, settings: Settings, inference: Inference):
        self.store = store
        if settings.background_processing_enabled:
            store.save_decision_timezone(settings.timezone)
        self.settings = settings
        self.inference = inference
        self.background_activity: dict[str, int] = {}
        self.usage = UsageLedger(store)
        if isinstance(inference, Inference):
            inference.usage_recorder = self.usage.record
        self.recap_lock = asyncio.Lock()
        self.topic_lock = asyncio.Lock()
        self.recaps = Recaps(self)
        self.translation_lock = asyncio.Lock()
        self.speakers = Speakers(self)
        self.voices = Voices(self)
        self.decision_lock = asyncio.Lock()
        self.retriever = HybridRetriever(store, settings)

    @contextmanager
    def processing_activity(self, stage: str):
        # Durable requests already appear in generation_jobs; count each task once.
        if generating_job.get():
            yield
            return
        self.background_activity[stage] = self.background_activity.get(stage, 0) + 1
        try:
            yield
        finally:
            self.background_activity[stage] -= 1

    def day_window(self, day: date) -> tuple[datetime, datetime]:
        zone = ZoneInfo(self.settings.timezone)
        start = datetime.combine(day, time.min, zone).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
        return start, end

    def hints(self, start: datetime, end: datetime) -> dict:
        # Replay through now so an event just outside this query can still supply nearby context.
        timeline = self.timeline()
        return {
            kind: [
                hint
                for hint in timeline[kind]
                if datetime.fromisoformat(hint["context_start"]) < end
                and datetime.fromisoformat(hint["context_end"]) > start
            ]
            for kind in ("markers", "meetings")
        }

    def window_data(self, start: datetime, end: datetime) -> dict:
        rows = self.store.window(start, end)
        sources = [public_source(row) for row in rows]
        hints = self.hints(start, end)
        for source in sources:
            if source.get("source_kind") == "manual_upload":
                source.update(important=False, meeting_hint_ids=[])
                continue
            source["important"] = any(
                source["started_at"] < mark["context_end"]
                and source["ended_at"] > mark["context_start"]
                for mark in hints["markers"]
            )
            source["meeting_hint_ids"] = [
                meeting["id"]
                for meeting in hints["meetings"]
                if source["started_at"] < meeting["context_end"]
                and source["ended_at"] > meeting["context_start"]
            ]
        result = {
            "start": stamp(start),
            "end": stamp(end),
            "sources": sources,
            "pending_chunks": sum(row["state"] in ("queued", "transcribing") for row in rows),
            "audio_gaps": coverage_gaps(
                [row for row in rows if row["audio_available"]], start, end
            ),
            "capture_gaps": self.store.capture_gaps(start, end),
            "summary": None,
            "completeness": self.store.completeness(start, end),
            **hints,
            "hint_timing": "approximate",
            "transcript_boundaries": (
                "Transcripts cover whole overlapping chunks; audio is clipped. "
                "Official webhook times are receipt estimates; overlaps can mask audio in "
                "combined playback. Inspect individual sources for every accepted chunk."
            ),
        }
        return result

    async def window(
        self,
        start: datetime,
        end: datetime,
        purpose: str,
        summarize: bool = True,
        kind: str = "summary",
    ) -> dict:
        result = self.window_data(start, end)
        done = [row for row in result["sources"] if row["state"] == "done" and row["text"]]
        if summarize and done:
            self.settings.require_inference("llm")
            counts = {
                "eligible_transcripts": len(done),
                "pending_chunks": result["pending_chunks"],
                "blocked_chunks": sum(r["state"] == "blocked" for r in result["sources"]),
                "selection_observed_at": result["completeness"]["as_of"],
            }
            trace = None
            with self.processing_activity("summarizing"):
                if hasattr(self.inference, "summarize_traced"):
                    summary, trace = await self.inference.summarize_traced(done, purpose)
                else:
                    summary = await self.inference.summarize(done, purpose)
            if summary == NO_CONTENT:
                return result | {"no_recap_content": True}
            result["summary"] = validate_citations(summary, done)
            output_id = str(uuid4())
            evidence = self.evidence_metadata(
                done,
                result["summary"],
                "all_transcripts",
                start,
                end,
                result["completeness"],
                trace=trace,
                counts=counts,
                kind=kind,
            )
            if kind != "recap":
                self.store.save_evidence(output_id, evidence, done, summary=result["summary"])
            result.update(output_id=output_id, evidence=evidence)
        return result

    def evidence_metadata(
        self,
        sources: list[dict],
        text: str,
        mode: str,
        start: datetime | None,
        end: datetime | None,
        completeness: dict | None = None,
        trace: dict | None = None,
        counts: dict | None = None,
        kind: str = "chat",
    ) -> dict:
        counts = counts if counts is not None else self.store.selection_counts(start, end)
        return {
            "generated_at": stamp(utc_now()),
            "kind": kind,
            "model": self.settings.llm_route("chat" if kind == "chat" else "summary").model,
            "prompt_version": 3,
            "retrieval_mode": mode,
            "scope": {"start": stamp(start), "end": stamp(end)} if start and end else None,
            **counts,
            "selected": [
                {
                    "id": r["id"],
                    "revision": revision(r),
                    "source_version": r.get("source_version"),
                    "transcript_version": r.get("transcript_version"),
                    "supplied_ranges": [
                        {"start_offset": m["start_offset"], "end_offset": m["end_offset"]}
                        for m in r.get("matches", [])
                    ],
                }
                for r in sources
            ],
            "cited_ids": [r["id"] for r in sources if r["id"].lower() in text.lower()],
            "input_handling": "reduction_capable_pipeline",
            "pipeline": trace,
            "completeness": completeness,
            "limitation": "Source selection and citations do not verify answer accuracy.",
        }

    def fingerprint(self, start: datetime, end: datetime) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "rows": [public_source(row) for row in self.store.window(start, end)],
                    "hints": self.hints(start, end),
                    "gaps": self.store.capture_gaps(start, end),
                    "model": self.settings.llm_route("summary").identity,
                    "timezone": self.settings.timezone,
                    "summary_language": self.settings.summary_language.lower(),
                    "prompt_version": 3,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def cached_recap(self, day: date, tag_id=None, zone_id=None) -> dict:
        if tag_id or zone_id:
            from .scoped_recaps import ScopedRecaps
            return ScopedRecaps(self, tag_id, zone_id).cached(day, utc_now())
        return self.recaps.cached(day, utc_now())

    def cached_recaps(self, days: list[date]) -> list[dict]:
        if not days:
            return []
        now = utc_now()
        groups = self.recaps.groups(now)
        return [self.recaps.cached(day, now, groups) for day in days]

    async def recap(self, day: date, tag_id=None, zone_id=None, expected_fingerprint=None) -> dict:
        self.settings.require_inference("llm")
        async with self.recap_lock:
            with self.processing_activity("summarizing"):
                if tag_id or zone_id:
                    from .scoped_recaps import ScopedRecaps
                    return await ScopedRecaps(self, tag_id, zone_id).generate(
                        day, utc_now(), expected_fingerprint,
                    )
                return await self.recaps.generate(day, utc_now())

    def timeline(self) -> dict:
        snapshot = self.store.recap_snapshot()
        return meeting_timeline(snapshot, utc_now()) | {
            "capture_gaps": snapshot["gaps"],
        }

    @staticmethod
    def sample_sources(rows: list[dict], limit: int) -> list[dict]:
        if len(rows) <= limit:
            return rows
        if limit == 1:
            return rows[-1:]
        return [rows[round(index * (len(rows) - 1) / (limit - 1))] for index in range(limit)]

    def diary_chat_sources(
        self,
        query: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        matched_ids: set[str],
    ) -> list[dict]:
        """Retrieve recaps alongside their current, citable transcript dependencies."""
        words = set(re.findall(r"\w+", query.lower()))
        diaries = []
        scoped_days = []
        for diary in self.store.diaries():
            day_start, day_end = self.day_window(date.fromisoformat(diary["day"]))
            if start and end and (day_start >= end or day_end <= start):
                continue
            scoped_days.append(date.fromisoformat(diary["day"]))
        for diary in self.cached_recaps(scoped_days):
            summary = diary.get("summary") or ""
            relevant = (
                not words
                or words & set(re.findall(r"\w+", summary.lower()))
                or any(source["id"] in matched_ids for source in diary["sources"])
            )
            if summary and relevant:
                diaries.append(diary)
        sources = {}
        for diary in self.sample_sources(diaries, limit):
            dependencies = []
            for source in diary["sources"]:
                row = self.store.get_audio(source["id"])
                if not row or row["state"] != "done" or row["text"] != source["text"]:
                    break
                if (
                    start
                    and end
                    and not (row["started_at"] < stamp(end) and row["ended_at"] > stamp(start))
                ):
                    break
                dependencies.append(public_source(row))
            else:
                if dependencies:
                    for source in dependencies:
                        sources.setdefault(source["id"], source)
                    first = sources[dependencies[0]["id"]]
                    first.setdefault("diary_summaries", []).append(
                        {
                            "day": diary["day"],
                            "summary": diary["summary"],
                            "source_ids": [item["id"] for item in dependencies],
                        }
                    )
        return list(sources.values())

    async def retrieve(
        self,
        question: str,
        limit: int,
        session_id: str | None = None,
        day: date | None = None,
        meeting_id: str | None = None,
        scope_supplied: bool = False,
        start_day: date | None = None,
        end_day: date | None = None,
        retrieval_mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
        query_override: str | None = None,
    ) -> dict:
        if (start_day is None) != (end_day is None) or (
            start_day is not None and start_day > end_day
        ):
            raise ChatScopeConflict("Provide an ordered start_day and end_day pair")
        if sum(value is not None for value in (day, meeting_id, start_day)) > 1:
            raise ChatScopeConflict("Choose a date, a date range, or a meeting")
        scope_supplied = scope_supplied or any(
            value is not None for value in (day, meeting_id, start_day, end_day)
        )
        if session_id:
            session = self.store.ensure_session(
                session_id,
                question,
                day.isoformat() if day else None,
                meeting_id,
                scope_supplied,
                start_day.isoformat() if start_day else None,
                end_day.isoformat() if end_day else None,
            )
            day = date.fromisoformat(session["day"]) if session["day"] else None
            meeting_id = session["meeting_id"]
            start_day = date.fromisoformat(session["start_day"]) if session["start_day"] else None
            end_day = date.fromisoformat(session["end_day"]) if session["end_day"] else None
        session = (
            session
            if session_id
            else {
                "id": None,
                "day": day.isoformat() if day else None,
                "meeting_id": meeting_id,
                "start_day": start_day.isoformat() if start_day else None,
                "end_day": end_day.isoformat() if end_day else None,
            }
        )
        history = (
            (await asyncio.to_thread(self.store.chat_session, session_id, 6) or {})["messages"]
            if session_id
            else []
        )
        today = utc_now().astimezone(ZoneInfo(self.settings.timezone)).date()
        scope = (day, day) if day else ((start_day, end_day) if start_day else None)
        if meeting_id:
            meeting = next(
                (item for item in self.timeline()["meetings"] if item["id"] == meeting_id), None
            )
            if meeting is None:
                raise ChatScopeNotFound("Meeting not found")
            start = datetime.fromisoformat(meeting["context_start"])
            end = datetime.fromisoformat(meeting["context_end"])
        else:
            if scope is None:
                scope = date_scope(question, today)
                if not scope and history and not archive_recall(question):
                    scope = next(
                        (
                            inferred
                            for row in reversed(history)
                            if row["role"] == "user"
                            and (inferred := date_scope(row["content"], today))
                        ),
                        None,
                    )
            start = self.day_window(scope[0])[0] if scope else None
            end = self.day_window(scope[1])[1] if scope else None
            if (
                day
                and not await asyncio.to_thread(self.store.has_recordings, start, end)
                and not self.store.diary(day.isoformat())
            ):
                raise ChatScopeNotFound("No diary or recordings found for this date")
        query, search_question = contextual_query(question, history)
        if query_override is not None:
            query = query_override
        if not query:
            rows, total = await asyncio.to_thread(self.store.sample_transcripts, limit, start, end)
            details = {
                "mode": "sample",
                "embedding": await asyncio.to_thread(self.retriever.status, start, end),
                "recordings_sampled": total > limit,
            }
        else:
            rows, details = await self.retriever.retrieve(
                search_question,
                query,
                limit,
                start,
                end,
                mode=retrieval_mode,
            )
        details["contextual_query"] = search_question != question
        if not query and not start and not end and history and not archive_recall(question):
            previous = next(
                (message for message in reversed(history) if message["role"] == "assistant"), None
            )
            previous_rows = [
                row
                for source in (previous["sources"] if previous else [])
                if (row := self.store.get_audio(source["id"]))
                and row["state"] == "done"
                and row["text"]
            ][:limit]
            rows = previous_rows or rows
        sources = {row["id"]: public_source(row) for row in rows}
        if not meeting_id:
            for source in self.diary_chat_sources(
                query if day is None else "",
                start,
                end,
                limit,
                set(sources),
            ):
                sources.setdefault(source["id"], source).update(
                    {"diary_summaries": source["diary_summaries"]}
                    if "diary_summaries" in source
                    else {}
                )
        if query:
            sources, truncated = bound_sources(
                list(sources.values()), query, limit,
                character_budget=self.settings.retrieval_evidence_character_budget,
            )
            details.update(
                evidence_mode="bounded_excerpts",
                evidence_character_budget=self.settings.retrieval_evidence_character_budget,
            )
        else:
            details["recordings_sampled"] |= len(sources) > limit
            sources, _ = bound_sources(list(sources.values()), query, limit, full_transcripts=True)
            truncated = False
            details.update(
                evidence_mode="full_selected_transcripts", evidence_character_budget=None
            )
        details["evidence_truncated"] = truncated
        if "context_sources" in details:
            included = {source["id"] for source in sources}
            details["context_sources"] = [
                key for key in details["context_sources"] if key in included
            ]
        if details["mode"] == "sample":
            notice = (
                "Uses sampled recordings. Full selected transcripts are reduced in bounded "
                "batches when needed."
            )
        elif details.get("fallback_reason"):
            notice = "Semantic search unavailable. Used keyword search."
        elif details["mode"] == "semantic":
            notice = "Used semantic search (evaluation mode)."
        elif details["mode"] == "hybrid":
            notice = "Used keyword and semantic search."
        else:
            notice = "Used keyword search. Semantic search is disabled."
        coverage = details["embedding"]
        if coverage["set_id"] and coverage["indexed"] < coverage["total"]:
            notice += (
                f" Semantic indexing is incomplete ({coverage['indexed']}/{coverage['total']} "
                "recordings); unindexed recordings use keywords."
            )
        if truncated and details["mode"] != "sample":
            notice += " Evidence uses bounded excerpts; open sources for full context."
        details["notice"] = notice
        with self.store.connect() as db:
            self.store.verify_sources(db, sources)
        return {
            "sources": sources,
            "retrieval": details["mode"],
            "retrieval_details": details,
            "session_id": session["id"],
            "day": session["day"],
            "meeting_id": session["meeting_id"],
            "start_day": session["start_day"],
            "end_day": session["end_day"],
            "scope": {"start": stamp(start), "end": stamp(end)} if start and end else None,
            "_history": history,
        }

    async def search(
        self,
        question: str,
        limit: int,
        session_id: str | None = None,
        day: date | None = None,
        meeting_id: str | None = None,
        scope_supplied: bool = False,
        start_day: date | None = None,
        end_day: date | None = None,
        retrieval_mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
    ) -> dict:
        result = await self.retrieve(
            question,
            limit,
            session_id,
            day,
            meeting_id,
            scope_supplied,
            start_day,
            end_day,
            retrieval_mode,
        )
        result.pop("_history")
        return result

    async def chat(
        self,
        question: str,
        limit: int,
        session_id: str | None = None,
        day: date | None = None,
        meeting_id: str | None = None,
        scope_supplied: bool = False,
        start_day: date | None = None,
        end_day: date | None = None,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        self.settings.require_inference("llm")
        began = perf_counter()
        first_token = None
        if on_event:
            await on_event({"type": "status", "stage": "searching"})
        result = await self.retrieve(
            question,
            limit,
            session_id,
            day,
            meeting_id,
            scope_supplied,
            start_day,
            end_day,
        )
        scope = result["scope"]
        start = datetime.fromisoformat(scope["start"]) if scope else None
        end = datetime.fromisoformat(scope["end"]) if scope else None
        counts = await asyncio.to_thread(self.store.selection_counts, start, end)
        expansion_ms = 0
        if (
            not result["sources"]
            and topic_query(question)
            and self.settings.chat_query_expansion
            and isinstance(self.inference, Inference)
            and counts["eligible_transcripts"] > 0
        ):
            expansion_start = perf_counter()
            try:
                if on_event:
                    await on_event({"type": "status", "stage": "expanding"})
                previous = next(
                    (
                        message["content"]
                        for message in reversed(result["_history"])
                        if message["role"] == "user"
                    ),
                    "",
                )
                async with asyncio.timeout(self.settings.chat_query_expansion_timeout_seconds):
                    query = await self.inference.expand_query(question, previous)
                if query and query != topic_query(question):
                    result = await self.retrieve(
                        question,
                        limit,
                        session_id,
                        day,
                        meeting_id,
                        scope_supplied,
                        start_day,
                        end_day,
                        query_override=query,
                    )
                    result["retrieval_details"]["query_expanded"] = True
                    result["retrieval_details"]["notice"] += " Used alternate search wording."
            except httpx.HTTPError, InferenceError, TimeoutError:
                result["retrieval_details"]["query_expansion_unavailable"] = True
                result["retrieval_details"]["notice"] += " Alternate search wording unavailable."
            expansion_ms = (perf_counter() - expansion_start) * 1000
        retrieval_ms = (perf_counter() - began) * 1000
        history = result.pop("_history")
        sources = result["sources"]
        if not result["session_id"]:
            session = self.store.ensure_session(
                None,
                question,
                result["day"],
                result["meeting_id"],
                start_day=result["start_day"],
                end_day=result["end_day"],
            )
            result["session_id"] = session["id"]
        trace = None
        generation_start = perf_counter()

        async def delta(text: str):
            nonlocal first_token
            if first_token is None:
                first_token = (perf_counter() - began) * 1000
            await on_event({"type": "delta", "text": text})

        if not sources:
            answer = "No matching recorded evidence was found. Try a specific name or topic."
        else:
            if on_event:
                await on_event({"type": "status", "stage": "generating"})
            # Query embedding awaits may outlive corrections to evidence used only by a
            # prior answer. Recheck those versions immediately before model context.
            history = [
                message
                for message in history
                if message["role"] != "assistant"
                or not self.store.sources_stale(message["sources"])
            ]
            if on_event and isinstance(self.inference, Inference):
                answer, trace = await self.inference.chat_traced(question, sources, history, delta)
            elif hasattr(self.inference, "chat_traced"):
                answer, trace = await self.inference.chat_traced(question, sources, history)
            else:
                answer = await self.inference.chat(question, sources, history)
            answer = validate_citations(answer, sources)
        generation_ms = (perf_counter() - generation_start) * 1000
        if not sources and result["retrieval_details"].get("fallback_reason"):
            answer += " Semantic search is unavailable; only keyword search was used."
        output_id = str(uuid4())
        evidence = self.evidence_metadata(
            sources, answer, result["retrieval"], start, end, trace=trace, counts=counts
        )
        evidence["retrieval_details"] = result["retrieval_details"]
        timings = {
            "retrieval_ms": round(retrieval_ms, 2),
            "expansion_ms": round(expansion_ms, 2),
            "generation_ms": round(generation_ms, 2),
            "first_token_ms": round(first_token, 2) if first_token is not None else None,
            "total_ms": round((perf_counter() - began) * 1000, 2),
        }
        evidence["timings"] = timings
        message_ids = self.store.save_exchange(
            result["session_id"],
            question,
            answer,
            sources,
            retrieval_details=result["retrieval_details"],
            evidence=evidence,
            output_id=output_id,
        )
        return result | {
            "answer": answer,
            "output_id": output_id,
            "evidence": evidence,
            "timings": timings,
            **message_ids,
        }
