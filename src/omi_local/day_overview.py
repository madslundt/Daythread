"""An explicitly requested, saved overview above the day's detailed topics."""

import json
import math
import re
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from .domain import EvidenceChanged, stamp, utc_now
from .inference import InferenceError, validate_citations
from .recaps import digest
from .summary_filter import NO_CONTENT, filter_summary_sources

if TYPE_CHECKING:
    from .service import Memory

VERSION = "day-overview-1"
MAX_WORDS = 900
SCHEMA = """
CREATE TABLE IF NOT EXISTS day_overviews (
 day TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS day_overview_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM day_overviews WHERE EXISTS (
  SELECT 1 FROM json_each(day_overviews.sources) WHERE json_extract(value,'$.id')=OLD.id
 );
END;
CREATE TRIGGER IF NOT EXISTS day_overview_source_change
AFTER UPDATE OF text,state,source_version,transcript_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.source_version IS NOT NEW.source_version
 OR OLD.transcript_version IS NOT NEW.transcript_version BEGIN
 DELETE FROM day_overviews WHERE EXISTS (
  SELECT 1 FROM json_each(day_overviews.sources) WHERE json_extract(value,'$.id')=OLD.id
 );
END;
"""

_CITATION = re.compile(r"\[\s*[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
                       r"(?:\s*,\s*[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})*\s*\]",
                       re.I)
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^\s)]+)\)")


def reading_text(text: str) -> str:
    return _LINK.sub(lambda match: match[1], _CITATION.sub("", text))


def without_citations(text: str) -> str:
    text = re.sub(r" +", " ", _CITATION.sub("", text))
    return re.sub(r" +([.,;!?])", r"\1", text).strip()


def topic_details(markdown: str) -> list[dict]:
    """Retain canonical titles and their saved detail for navigation after regrouping."""
    topics = []
    current = None
    for line in markdown.splitlines():
        heading = re.match(r"^#{1,4}\s+(.+)$", line.strip())
        legacy = re.match(r"^[-*+]\s+\*\*([^*]+)\*\*\s*(?:[—–:-]\s*)?(.*)$",
                          line.strip())
        if heading or legacy:
            title = (heading[1] if heading else legacy[1]).replace("**", "").strip()
            current = next((topic for topic in topics if topic["title"] == title), None)
            if current is None:
                current = {"id": title, "title": title, "summary": ""}
                topics.append(current)
            if legacy and legacy[2]:
                current["summary"] += legacy[2] + "\n"
        elif current is not None:
            current["summary"] += line + "\n"
    return [{**topic, "summary": without_citations(topic["summary"])}
            for topic in topics if topic["summary"].strip()]


def linked_overview(text: str, topics: list[dict]) -> str:
    allowed = {topic["id"] for topic in topics}

    def link(match):
        label, url = match[1], match[2]
        if url.startswith("topic:") and unquote(url[6:]) in allowed:
            return f"[{label}](topic:{quote(unquote(url[6:]), safe='')})"
        # Model-created external links or unknown topics must never become navigation.
        return label

    text = _LINK.sub(link, without_citations(text))
    # A plain mention is also navigable if the model omitted its requested link.
    pieces = re.split(r"(\[[^\]\n]+\]\(topic:[^\s)]+\))", text)
    for index in range(0, len(pieces), 2):
        for topic in sorted(topics, key=lambda item: len(item["title"]), reverse=True):
            pattern = r"(?<!\w)" + re.escape(topic["title"]) + r"(?!\w)"
            # Avoid linking inside a link inserted for an overlapping longer title.
            segments = re.split(r"(\[[^\]\n]+\]\(topic:[^\s)]+\))", pieces[index])
            for offset in range(0, len(segments), 2):
                segments[offset] = re.sub(pattern, lambda match, topic=topic:
                    f"[{match[0]}](topic:{quote(topic['id'], safe='')})", segments[offset])
            pieces[index] = "".join(segments)
    return "".join(pieces)


class DayOverview:
    def __init__(self, memory: Memory):
        self.memory = memory

    def snapshot(self, day: date, now, db=None):
        recaps = self.memory.recaps
        groups = recaps.for_day(recaps.groups(now, db), day)
        key = digest({"day": day.isoformat(), "evidence": recaps.day_key(groups),
                      "overview": VERSION})
        return groups, key

    def cached(self, day: date) -> dict:
        memory, now = self.memory, utc_now()
        groups, key = self.snapshot(day, now)
        recap = memory.cached_recap(day)
        sources = [source for group in groups if group.ready
                   for source in memory.recaps.sources(group)]
        useful, _ = filter_summary_sources(sources)
        can_generate = bool(useful) and memory.settings.llm_enabled
        if recap.get("no_recap_content") and not recap.get("stale"):
            can_generate = False
        result = {"day": day.isoformat(), "summary": None, "generated_at": None,
                  "stale": False, "can_generate": can_generate, "topics": [],
                  "reading_minutes": 0}
        with memory.store.connect() as db:
            row = db.execute("SELECT * FROM day_overviews WHERE day=?",
                             (day.isoformat(),)).fetchone()
            if not row:
                return result
            content = json.loads(row["content"])
            dependencies = json.loads(row["sources"])
            try:
                memory.store.verify_sources(db, dependencies)
            except EvidenceChanged:
                return result
        if content["timezone"] != memory.settings.timezone:
            return result
        pending = {source["id"] for group in groups if not group.ready for source in group.sources}
        if any(source["id"] in pending for source in dependencies):
            return result
        return result | content | {"stale": row["fingerprint"] != key,
                                   "can_generate": can_generate}

    async def generate(self, day: date) -> dict:
        memory = self.memory
        memory.settings.require_inference("llm")
        # Share topic generation's lock so concurrent clicks reuse a completed snapshot.
        async with memory.recap_lock:
            current = self.cached(day)
            if current["summary"] and not current["stale"] or not current["can_generate"]:
                return current
            with memory.processing_activity("summarizing"):
                now = utc_now()
                groups, key = self.snapshot(day, now)
                recap = await memory.recaps.generate(day, now)
                if not recap.get("summary"):
                    return self.cached(day)
                sources = recap["sources"]
                topics = topic_details(recap["summary"])
                links = [{"title": topic["title"],
                          "link": "topic:" + quote(topic["id"], safe="")} for topic in topics]
                purpose = (
                    f"a high-level day recap for {day.isoformat()}, at most {MAX_WORDS} words "
                    "and readable within five minutes. Usually 200–500 words is enough; use "
                    "less for a quiet day. Cover what happened across the WHOLE recorded day, "
                    "including meaningful personal moments, progress, changed plans, decisions "
                    "and unresolved matters. Consolidate repetitions. Keep details in the "
                    "individual topics; do not reproduce their full summaries or a task list. "
                    "Write a few concise connected paragraphs without a title. Preserve "
                    "uncertainty and do not imply unrecorded parts of the day were captured. "
                    "Whenever referring to a supplied topic, link its name using the exact "
                    "Markdown target below, for example [Topic](topic:Topic). Never invent "
                    "topic links or external URLs. Topics are navigation, not new evidence: "
                    + json.dumps(links, ensure_ascii=False)
                )
                inputs = [{**sources[0], "text": recap["summary"],
                           "citation_source_ids": [source["id"] for source in sources]}]
                for attempt in range(2):
                    draft = await memory.inference.summarize(inputs, purpose + (
                        " The previous draft was too long. Keep this version below 700 words."
                        if attempt else ""))
                    draft = validate_citations(draft, sources).strip()
                    if draft == NO_CONTENT or not draft:
                        raise InferenceError("Day recap did not contain a supported overview")
                    summary = linked_overview(draft, topics)
                    if not reading_text(summary).strip():
                        raise InferenceError("Day recap did not contain a readable overview")
                    words = len(reading_text(summary).split())
                    if words <= MAX_WORDS:
                        break
                else:
                    raise InferenceError("Day recap exceeded the five-minute reading limit")
                content = {"day": day.isoformat(), "summary": summary, "topics": topics,
                           "timezone": memory.settings.timezone, "generated_at": stamp(utc_now()),
                           "reading_minutes": max(1, math.ceil(words / 180))}
                dependencies = [{name: source[name] for name in (
                    "id", "source_version", "transcript_version") if name in source}
                                for source in sources]
                with memory.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    memory.recaps.guard(day, now, groups)(db)
                    memory.store.verify_sources(db, sources)
                    db.execute("INSERT OR REPLACE INTO day_overviews VALUES(?,?,?,?)", (
                        day.isoformat(), key, json.dumps(content), json.dumps(dependencies)))
                return self.cached(day)
