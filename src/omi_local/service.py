import asyncio
import hashlib
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from .audio import coverage_gaps
from .domain import ChatScopeConflict, ChatScopeNotFound, project_events, stamp, utc_now
from .hybrid import HybridRetriever, bound_sources
from .inference import Inference, validate_citations
from .retrieval import archive_recall, date_scope, topic_query
from .settings import Settings
from .store import Store


def public_source(row: dict) -> dict:
    result = {
        key: row[key] for key in ("id", "device_id", "started_at", "ended_at", "state", "text")
    }
    result["audio_available"] = bool(row.get("audio_available", True))
    result["segments"] = json.loads(row.get("segments", "[]"))
    result["source_version"] = row.get("source_version", "")
    result["source_kind"] = row.get("source_kind", "")
    result["source_id"] = row.get("source_id", "")
    result["transcript_version"] = row.get("transcript_version")
    for field in ("matches", "context_for"):
        if field in row:
            result[field] = row[field]
    return result


class Memory:
    def __init__(self, store: Store, settings: Settings, inference: Inference):
        self.store = store
        store.save_decision_timezone(settings.timezone)
        self.settings = settings
        self.inference = inference
        self.recap_lock = asyncio.Lock()
        self.decision_lock = asyncio.Lock()
        self.retriever = HybridRetriever(store, settings)

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
            **hints,
            "hint_timing": "approximate",
            "transcript_boundaries": (
                "Transcripts cover whole overlapping chunks; audio is clipped."
            ),
        }
        return result

    async def window(
        self, start: datetime, end: datetime, purpose: str, summarize: bool = True
    ) -> dict:
        result = self.window_data(start, end)
        done = [row for row in result["sources"] if row["state"] == "done" and row["text"]]
        if summarize and done:
            result["summary"] = validate_citations(
                await self.inference.summarize(done, purpose), done
            )
            with self.store.connect() as db:
                self.store.verify_sources(db, done)
        return result

    def fingerprint(self, start: datetime, end: datetime) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "rows": [public_source(row) for row in self.store.window(start, end)],
                    "hints": self.hints(start, end),
                    "gaps": self.store.capture_gaps(start, end),
                    "model": self.settings.llm_model,
                    "timezone": self.settings.timezone,
                    "prompt_version": 3,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def cached_recap(self, day: date) -> dict:
        start, end = self.day_window(day)
        row = self.store.diary(day.isoformat())
        if row:
            return json.loads(row["content"]) | {
                "stale": row["fingerprint"] != self.fingerprint(start, end)
            }
        return self.window_data(start, end) | {
            "day": day.isoformat(),
            "timezone": self.settings.timezone,
            "generated_at": None,
            "stale": False,
        }

    async def recap(self, day: date) -> dict:
        async with self.recap_lock:
            start, end = self.day_window(day)
            fingerprint = self.fingerprint(start, end)
            existing = self.store.diary(day.isoformat())
            if existing and existing["fingerprint"] == fingerprint:
                result = json.loads(existing["content"])
                self.store.export_diary(day.isoformat(), result)
                return result
            result = await self.window(start, end, f"a diary recap for {day.isoformat()}")
            result.update(
                day=day.isoformat(),
                timezone=self.settings.timezone,
                model=self.settings.llm_model,
                generated_at=stamp(utc_now()),
            )
            if result["summary"] is not None:
                self.store.save_diary(day.isoformat(), fingerprint, result)
            return result

    def timeline(self) -> dict:
        return project_events(self.store.events(), utc_now()) | {
            "capture_gaps": self.store.capture_gaps()
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
        for diary in self.store.diaries():
            day_start, day_end = self.day_window(date.fromisoformat(diary["day"]))
            if start and end and (day_start >= end or day_end <= start):
                continue
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
        history = self.store.chat_session(session_id)["messages"] if session_id else []
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
                    prior = next(
                        (row["content"] for row in reversed(history) if row["role"] == "user"), ""
                    )
                    scope = date_scope(prior, today)
            start = self.day_window(scope[0])[0] if scope else None
            end = self.day_window(scope[1])[1] if scope else None
            if day and not self.store.window(start, end) and not self.store.diary(day.isoformat()):
                raise ChatScopeNotFound("No diary or recordings found for this date")
        query = topic_query(question)
        details = {"mode": "sample", "embedding": self.retriever.status()}
        if not query:
            rows = (
                self.store.window(start, end)
                if start and end
                else self.store.transcripts(None, None, "", 2**31 - 1, 0)[0]
            )
            rows = [row for row in rows if row["state"] == "done" and row["text"]]
            details["recordings_sampled"] = len(rows) > limit
            rows = self.sample_sources(rows, limit)
        else:
            rows, details = await self.retriever.retrieve(
                question,
                query,
                limit,
                start,
                end,
                mode=retrieval_mode,
            )
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
            sources, truncated = bound_sources(list(sources.values()), query, limit)
            details.update(evidence_mode="bounded_excerpts", evidence_character_budget=12000)
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
        )
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
        if not sources:
            answer = "No matching recorded evidence was found. Try a specific name or topic."
        else:
            # Query embedding awaits may outlive corrections to evidence used only by a
            # prior answer. Recheck those versions immediately before model context.
            history = [
                message
                for message in history
                if message["role"] != "assistant"
                or not self.store.sources_stale(message["sources"])
            ]
            answer = validate_citations(
                await self.inference.chat(question, sources, history), sources
            )
        if not sources and result["retrieval_details"].get("fallback_reason"):
            answer += " Semantic search is unavailable; only keyword search was used."
        self.store.save_exchange(
            result["session_id"],
            question,
            answer,
            sources,
            result["retrieval_details"],
        )
        return result | {"answer": answer}
