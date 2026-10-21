"""An explicit, saved five-minute recap of one local calendar week."""

import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from .day_overview import _LINK, MAX_WORDS, linked_overview, reading_text, topic_details
from .domain import EvidenceChanged, stamp, utc_now
from .inference import InferenceError, validate_citations
from .recaps import digest
from .summary_filter import NO_CONTENT, filter_summary_sources
from .weekly_models import week_bounds

if TYPE_CHECKING:
    from .service import Memory

VERSION = "week-overview-1-originals-inline"
SCHEMA = """
CREATE TABLE IF NOT EXISTS week_overviews (
 week_start TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
 content TEXT NOT NULL, sources TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS week_overview_output_delete AFTER DELETE ON week_overviews BEGIN
 DELETE FROM output_evidence WHERE id=json_extract(OLD.content,'$.output_id');
END;
CREATE TRIGGER IF NOT EXISTS week_overview_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM week_overviews WHERE EXISTS (
  SELECT 1 FROM json_each(week_overviews.sources) WHERE json_extract(value,'$.id')=OLD.id
 );
END;
CREATE TRIGGER IF NOT EXISTS week_overview_source_change
AFTER UPDATE OF text,state,source_version,transcript_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.source_version IS NOT NEW.source_version
 OR OLD.transcript_version IS NOT NEW.transcript_version BEGIN
 DELETE FROM week_overviews WHERE EXISTS (
  SELECT 1 FROM json_each(week_overviews.sources) WHERE json_extract(value,'$.id')=OLD.id
 );
END;
"""


def linked_weekly_overview(text: str, topics: list[dict]) -> str:
    """Link exact plain title mentions only when a single saved day owns that title."""
    titles = Counter(topic["title"].casefold() for topic in topics)
    unique = [topic for topic in topics if titles[topic["title"].casefold()] == 1]
    return linked_overview(text, topics, plain_topics=unique)


def restore_links(original: str, reviewed: str, topics: list[dict]) -> str:
    """Restore unchanged linked labels by position, never by ambiguous repeated titles."""
    plain, anchors, offset = "", [], 0
    for match in _LINK.finditer(original):
        plain += original[offset:match.start()]
        start = len(plain)
        plain += match[1]
        anchors.append((start, len(plain), match[0]))
        offset = match.end()
    plain += original[offset:]
    mappings = SequenceMatcher(None, plain, reviewed, autojunk=False).get_matching_blocks()
    insertions = []
    for start, end, link in anchors:
        for block in mappings:
            if block.a <= start and end <= block.a + block.size:
                left = block.b + start - block.a
                insertions.append((left, left + end - start, link))
                break
    for start, end, link in sorted(insertions, reverse=True):
        reviewed = reviewed[:start] + link + reviewed[end:]
    return linked_weekly_overview(reviewed, topics)


class WeekOverview:
    def __init__(self, memory: Memory):
        self.memory = memory

    def snapshot(self, day: date, now: datetime, db=None) -> dict:
        memory, recaps = self.memory, self.memory.recaps
        _, start, end = week_bounds(day, memory.settings.timezone)
        if start > now:
            raise ValueError("Future weeks cannot be reviewed")
        cutoff = min(now, end)
        groups = [group for group in recaps.groups(now, db)
                  if group.start < stamp(cutoff) and group.end > stamp(start)]
        sources = {source["id"]: source for group in groups if group.ready
                   for source in recaps.sources(group)
                   if source["started_at"] < stamp(cutoff) and source["ended_at"] > stamp(start)}
        useful, _ = filter_summary_sources(list(sources.values()))
        # Filtering selects eligible IDs only. Publication, provenance and review
        # must retain authoritative speech; cleanup belongs solely in writer copies.
        useful = [sources[source["id"]] for source in useful]
        policy = {"timezone": memory.settings.timezone,
                  "recap_language": memory.settings.recap_language,
                  "summary_language": memory.settings.summary_language,
                  "model": memory.settings.llm_route("summary").identity,
                  "overview": VERSION}
        keys = sorted(recaps.group_key(group) for group in groups if group.ready)
        return {"week_start": day.isoformat(), "timezone": policy["timezone"],
                "start": stamp(start), "end": stamp(end), "cutoff": stamp(cutoff),
                "partial": cutoff < end, "groups": groups, "group_keys": keys,
                "sources": useful, "policy": policy,
                "fingerprint": digest({"week": day.isoformat(), "groups": keys,
                                       "policy": policy})}

    def cached(self, day: date) -> dict:
        snapshot = self.snapshot(day, utc_now())
        result = {key: snapshot[key] for key in (
            "week_start", "timezone", "start", "end", "cutoff", "partial")}
        result.update(summary=None, generated_at=None, reading_minutes=0, topics=[],
                      output_id=None, stale=False,
                      can_generate=bool(snapshot["sources"]) and self.memory.settings.llm_enabled)
        with self.memory.store.connect() as db:
            row = db.execute("SELECT * FROM week_overviews WHERE week_start=?",
                             (day.isoformat(),)).fetchone()
            if not row:
                return result
            content, dependencies = json.loads(row["content"]), json.loads(row["sources"])
            try:
                self.memory.store.verify_sources(db, dependencies)
            except EvidenceChanged:
                return result
            if content["timezone"] != snapshot["timezone"]:
                return result
            previous = {source["id"] for source in dependencies}
            return result | content | {
                # Calendar progress is live; the saved cutoff and provenance remain fixed.
                "partial": snapshot["partial"], "saved_partial": content["partial"],
                "stale": row["fingerprint"] != snapshot["fingerprint"],
                "can_generate": result["can_generate"] and any(
                    source["id"] not in previous for source in snapshot["sources"])}

    async def topics(self, day: date, now: datetime, sources: list[dict]) -> list[dict]:
        """Daily topics provide navigation only; the writer receives original speech."""
        result, allowed = [], {source["id"] for source in sources}
        for offset in range(7):
            current = day + timedelta(days=offset)
            start, end = map(stamp, self.memory.day_window(current))
            if start >= stamp(now) or not any(
                       source["started_at"] < end and source["ended_at"] > start
                       for source in sources):
                continue
            recap = await self.memory.recaps.generate(current, now)
            for topic in topic_details(recap.get("summary") or "", include_source_ids=True):
                refs = [key for key in topic.pop("source_ids") if key in allowed]
                if not refs:
                    continue
                result.append(topic | {"id": f"{current.isoformat()}:{topic['id']}",
                                       "day": current.isoformat(), "source_ids": refs})
        return result

    async def compose(
        self, day: date, sources: list[dict], topics: list[dict], context: dict,
    ) -> tuple[str, dict, int]:
        links = [{"title": topic["title"], "day": topic["day"],
                  "link": "topic:" + quote(topic["id"], safe=""),
                  "source_ids": topic["source_ids"]} for topic in topics]
        purpose = (
            f"a high-level weekly recap for the Monday–Sunday week beginning {day.isoformat()}, "
            f"targeting 300–500 words, with a hard limit of {MAX_WORDS} words and five minutes "
            f"of reading. Calendar timezone: {context['timezone']}. Selected local-week "
            f"interval in UTC: [{context['start']}, {context['end']}); evidence cutoff: "
            f"{context['cutoff']}; partial week: {context['partial']}. Convert source UTC "
            "timestamps to the calendar timezone before assigning local weekdays. A partial "
            "week covers only recordings through the cutoff; do not describe the week as "
            "finished or imply that its unrecorded remainder was captured. Use less for a "
            "quiet week. Cover the WHOLE supplied recorded week "
            "from its beginning through its latest recording, including meaningful personal "
            "moments, main events, progress, decisions, changed plans and unresolved matters. "
            "Consolidate repeated subjects across days. Write a few connected concise "
            "paragraphs without a title, topic index, list of links, navigation footer or "
            "detailed task list. Keep supporting implementation details in the linked topics. "
            "Use ORIGINAL transcripts as factual evidence; daily topic titles only provide "
            "navigation and their summaries are not evidence. Preserve source dates: do not "
            "attribute a prior-week boundary recording's context to an event this week. "
            "Whole overlapping transcript chunks may extend outside the selected bounds. "
            "Do not imply unrecorded days were captured. Keep proposals and options distinct "
            "from agreed plans, work explicitly reported underway, and specific work explicitly "
            "reported completed. Keep actual progress/completion and its attribution; do not "
            "downgrade everything to proposals. A goal does not select a suggested tool, a "
            "capability is not active work, and planned testing is not testing underway. "
            "Preserve conditions, uncertainty, negation and chronological changes. Link topic "
            "mentions naturally in the prose using ONLY supplied exact Markdown targets. "
            "Topic IDs include the day; use the target matching the subject and supporting "
            "source IDs, never a similarly named unrelated topic. Never invent external links. "
            "Navigation topics: " + json.dumps(links, ensure_ascii=False)
        )
        inputs, words = sources, 0
        # Review dates in the same calendar frame as the writer. These are request
        # copies of the same instants; stored capture timestamps/text stay untouched.
        review_sources = [{**source, "started_at": datetime.fromisoformat(source["started_at"])
                           .astimezone(ZoneInfo(context["timezone"])).isoformat()}
                          for source in sources]
        for attempt in range(2):
            draft = await self.memory.inference.summarize_day_overview(inputs, purpose + (
                f" Rewrite the supplied {words}-word draft into at most 500 words. Compress "
                "supporting detail while retaining whole-week coverage, qualifications and links."
                if attempt else ""))
            draft = validate_citations(draft, sources).strip()
            if not draft or draft == NO_CONTENT:
                raise InferenceError("Weekly recap did not contain a supported overview")
            summary = linked_weekly_overview(draft, topics)
            words = len(reading_text(summary).split())
            if words <= MAX_WORDS:
                draft, accuracy = await self.memory.inference.review_summary(
                    review_sources, reading_text(summary), topics=links)
                summary = restore_links(summary, draft, topics)
            if not reading_text(summary).strip():
                raise InferenceError("Weekly recap did not contain a readable overview")
            words = len(reading_text(summary).split())
            if words <= MAX_WORDS:
                return summary, accuracy, words
            inputs = [{**sources[0], "text": draft,
                       "citation_source_ids": [source["id"] for source in sources]}]
        raise InferenceError("Weekly recap exceeded the five-minute reading limit")

    async def generate(self, day: date) -> dict:
        memory = self.memory
        async with memory.recap_lock:
            current = self.cached(day)
            if not current["can_generate"]:
                return current
            memory.settings.require_inference("llm")
            with memory.processing_activity("summarizing"):
                now = utc_now()
                snapshot = self.snapshot(day, now)
                sources = snapshot["sources"]
                topics = await self.topics(day, now, sources)
                summary, accuracy, words = await self.compose(day, sources, topics, snapshot)
                content = {key: snapshot[key] for key in (
                    "week_start", "timezone", "start", "end", "cutoff", "partial")}
                content.update(summary=summary, topics=[{key: value for key, value in topic.items()
                                                       if key != "source_ids"} for topic in topics],
                               output_id=str(uuid4()), generated_at=stamp(utc_now()),
                               language=snapshot["policy"]["recap_language"],
                               reading_minutes=max(1, math.ceil(words / 180)),
                               accuracy_review=accuracy)
                dependencies = [{key: source[key] for key in (
                    "id", "source_version", "transcript_version")} for source in sources]
                with memory.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    latest = self.snapshot(day, now, db)
                    if (snapshot["policy"] != latest["policy"] or not
                            set(snapshot["group_keys"]) <= set(latest["group_keys"])):
                        raise EvidenceChanged("Weekly recap evidence changed; retry the recap")
                    memory.store.verify_sources(db, sources)
                    previous = db.execute("SELECT content FROM week_overviews WHERE week_start=?",
                                          (day.isoformat(),)).fetchone()
                    if previous:
                        db.execute("DELETE FROM output_evidence WHERE id=?",
                                   (json.loads(previous["content"])["output_id"],))
                    evidence = memory.evidence_metadata(
                        sources, summary, "range", datetime.fromisoformat(snapshot["start"]),
                        datetime.fromisoformat(snapshot["cutoff"]), counts={}, kind="week_overview")
                    memory.store._save_evidence(db, content["output_id"], evidence, sources,
                                                summary=summary)
                    db.execute("INSERT OR REPLACE INTO week_overviews VALUES(?,?,?,?)", (
                        day.isoformat(), snapshot["fingerprint"], json.dumps(content),
                        json.dumps(dependencies)))
                return self.cached(day)
