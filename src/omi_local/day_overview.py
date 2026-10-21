"""An explicitly requested, saved overview above the day's detailed topics."""

import json
import math
import re
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote
from uuid import uuid4

from .domain import EvidenceChanged, stamp, utc_now
from .inference import InferenceError, validate_citations
from .recaps import digest
from .summary_filter import NO_CONTENT, filter_summary_sources

if TYPE_CHECKING:
    from .service import Memory

VERSION = "day-overview-9-inline-navigation"
MAX_WORDS = 900
SCHEMA = """
CREATE TABLE IF NOT EXISTS day_overviews (
 day TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS day_overview_output_delete AFTER DELETE ON day_overviews BEGIN
 DELETE FROM output_evidence WHERE id=json_extract(OLD.content,'$.output_id');
END;
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


def topic_details(markdown: str, *, include_source_ids: bool = False) -> list[dict]:
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
    return [{**topic, "summary": without_citations(topic["summary"]),
             **({"source_ids": list(dict.fromkeys(
                 key.lower() for citation in _CITATION.finditer(topic["summary"])
                 for key in re.findall(r"[0-9a-f-]{36}", citation[0], re.I)
             ))} if include_source_ids else {})}
            for topic in topics if topic["summary"].strip()]


def linked_overview(
    text: str, topics: list[dict], *, link_plain: bool = True,
    plain_topics: list[dict] | None = None,
) -> str:
    allowed = {topic["id"] for topic in topics}

    def link(match):
        label, url = match[1], match[2]
        if url.startswith("topic:") and unquote(url[6:]) in allowed:
            return f"[{label}](topic:{quote(unquote(url[6:]), safe='')})"
        # Model-created external links or unknown topics must never become navigation.
        return label

    text = _LINK.sub(link, without_citations(without_topic_index(text, topics)))
    if not link_plain:
        return text.strip()
    selected = ({topic["id"] for topic in plain_topics} if plain_topics is not None else allowed)
    candidates = [topic for topic in topics if topic["id"] in selected]
    # A plain mention is also navigable if the model omitted its requested link.
    pieces = re.split(r"(\[[^\]\n]+\]\(topic:[^\s)]+\))", text)
    for index in range(0, len(pieces), 2):
        for topic in sorted(candidates, key=lambda item: len(item["title"]), reverse=True):
            pattern = r"(?<!\w)" + re.escape(topic["title"]) + r"(?!\w)"
            # Avoid linking inside a link inserted for an overlapping longer title.
            segments = re.split(r"(\[[^\]\n]+\]\(topic:[^\s)]+\))", pieces[index])
            for offset in range(0, len(segments), 2):
                segments[offset] = re.sub(pattern, lambda match, topic=topic:
                    f"[{match[0]}](topic:{quote(topic['id'], safe='')})", segments[offset],
                    flags=re.IGNORECASE)
            pieces[index] = "".join(segments)
    return "".join(pieces).strip()


def without_topic_index(text: str, topics: list[dict]) -> str:
    """Remove the old generated navigation footer while preserving linked prose."""
    paragraphs = text.rstrip().rsplit("\n\n", 1)
    if len(paragraphs) != 2:
        return text
    allowed = {topic["id"] for topic in topics}
    entries = paragraphs[-1].split(" · ")
    matches = [_LINK.fullmatch(entry.strip()) for entry in entries]
    if all(match and match[2].startswith("topic:")
           and unquote(match[2][6:]) in allowed for match in matches):
        return paragraphs[0].rstrip()
    return text


class DayOverview:
    def __init__(self, memory: Memory):
        self.memory = memory

    def snapshot(self, day: date, now, db=None):
        recaps = self.memory.recaps
        groups = recaps.for_day(recaps.groups(now, db), day)
        key = digest({"day": day.isoformat(), "evidence": recaps.day_key(groups),
                      "overview": VERSION, "recap_language": self.memory.settings.recap_language})
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
                  "reading_minutes": 0, "output_id": None}
        with memory.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
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
            # Only newly eligible recordings offer another generation. Preferences,
            # model upgrades and pending chunks do not create anything new to recap.
            previous = {source["id"] for source in dependencies}
            can_generate = can_generate and any(source["id"] not in previous for source in useful)
            summary = without_topic_index(content["summary"], content["topics"])
            output_id = content.get("output_id")
            registered = db.execute(
                "SELECT text FROM summary_outputs JOIN output_evidence "
                "ON output_evidence.id=summary_outputs.id WHERE summary_outputs.id=?",
                (output_id,)).fetchone() if output_id else None
            if not registered or registered["text"] != summary:
                if output_id:
                    db.execute("DELETE FROM output_evidence WHERE id=?", (output_id,))
                originals = [dict(db.execute("SELECT * FROM audio WHERE id=?", (source["id"],))
                                  .fetchone()) for source in dependencies]
                content.update(summary=summary, output_id=str(uuid4()),
                               reading_minutes=max(1, math.ceil(len(reading_text(summary).split())
                                                                / 180)))
                self.save_output(db, content, originals)
                db.execute("UPDATE day_overviews SET content=? WHERE day=?",
                           (json.dumps(content), day.isoformat()))
        return result | content | {"stale": row["fingerprint"] != key,
                                   "can_generate": can_generate}

    def save_output(self, db, content: dict, sources: list[dict]):
        evidence = self.memory.evidence_metadata(
            sources, content["summary"], "day", None, None, counts={}, kind="day_overview")
        self.memory.store._save_evidence(
            db, content["output_id"], evidence, sources, summary=content["summary"])

    async def generate(self, day: date) -> dict:
        memory = self.memory
        # Share topic generation's lock so concurrent clicks reuse a completed snapshot.
        async with memory.recap_lock:
            current = self.cached(day)
            if current["summary"] and not current["stale"] or not current["can_generate"]:
                return current
            memory.settings.require_inference("llm")
            with memory.processing_activity("summarizing"):
                now = utc_now()
                groups, key = self.snapshot(day, now)
                language = memory.settings.recap_language
                recap = await memory.recaps.generate(day, now)
                if not recap.get("summary"):
                    return self.cached(day)
                sources = recap["sources"]
                topics = topic_details(recap["summary"])
                links = [{"title": topic["title"],
                          "link": "topic:" + quote(topic["id"], safe=""),
                          "source_ids": topic["source_ids"]}
                         for topic in topic_details(recap["summary"], include_source_ids=True)]
                purpose = (
                    f"a high-level day recap for {day.isoformat()}, targeting 300–500 words "
                    f"with a hard limit of {MAX_WORDS} words, readable within five minutes. Use "
                    "less for a quiet day. Cover what happened across the WHOLE recorded day, "
                    "including meaningful personal moments, progress, changed plans, decisions "
                    "and unresolved matters. Consolidate repetitions. Keep details in the "
                    "individual topics; do not reproduce their full summaries or a task list. "
                    "Give each substantive topic only a short sentence or two about its main "
                    "event, outcome or unresolved issue. Leave implementation steps, technical "
                    "specifications, inventories, minor names and supporting numbers in the "
                    "linked topics. Do not enumerate conversion logic, version numbers, "
                    "counts or test mechanics. Cover the beginning and end of the recorded day. "
                    "Write a few concise connected paragraphs without a title. Preserve "
                    "uncertainty and do not imply unrecorded parts of the day were captured. "
                    "Use the original recordings as the factual evidence. Topic titles only "
                    "provide navigation; earlier topic summaries may overstate progress. "
                    "Keep proposals and options distinct from agreed plans, work explicitly "
                    "reported underway, and work explicitly reported completed. Preserve "
                    "genuine progress and completion with their specific scope and attribution. "
                    "Do not turn planned testing into testing underway, capabilities into "
                    "active work, hopes into forecasts, historical aspirations into current "
                    "future plans, or an agreed goal into selection of an optional tool. "
                    "Before returning, check each action's object, modality and temporal frame "
                    "against the original wording. Omit uncertain minor details rather than "
                    "inferring progress. "
                    "Whenever referring to a supplied topic, link its name using the exact "
                    "Markdown target below, for example [Topic](topic:Topic). Never invent "
                    "topic links or external URLs. "
                    "Do not append a topic index, a list of links, or a navigation footer. "
                    "Match links to the specific subject and its supporting source IDs, "
                    "not merely similar words in a title. Do not "
                    "attach a fact to a different topic's link. Topics and their source IDs "
                    "provide navigation, not new factual evidence: "
                    + json.dumps(links, ensure_ascii=False)
                )
                inputs = sources
                words = 0
                for attempt in range(2):
                    draft = await memory.inference.summarize_day_overview(inputs, purpose + (
                        f" Rewrite the supplied draft, which had {words} words, into at most "
                        "500 words. This is a compression pass: do not add details or repeat "
                        "the original topic summaries. Keep the main events across all "
                        "recordings, qualifications, and topic links; remove supporting detail."
                        if attempt else ""))
                    draft = validate_citations(draft, sources).strip()
                    if draft == NO_CONTENT or not draft:
                        raise InferenceError("Day recap did not contain a supported overview")
                    summary = linked_overview(draft, topics)
                    words = len(reading_text(summary).split())
                    if words <= MAX_WORDS:
                        # Audit prose, not encoded navigation or citation markup. Restore
                        # canonical targets including labels in a different recap language.
                        navigation = topics + [
                            {"id": unquote(match[2][6:]), "title": match[1]}
                            for match in _LINK.finditer(summary)
                        ]
                        draft, accuracy = await memory.inference.review_summary(
                            sources, reading_text(draft), topics=links)
                        summary = linked_overview(draft, navigation)
                    if not reading_text(summary).strip():
                        raise InferenceError("Day recap did not contain a readable overview")
                    words = len(reading_text(summary).split())
                    if words <= MAX_WORDS:
                        break
                    inputs = [{**sources[0], "text": draft,
                               "citation_source_ids": [source["id"] for source in sources]}]
                else:
                    raise InferenceError("Day recap exceeded the five-minute reading limit")
                content = {"day": day.isoformat(), "summary": summary, "topics": topics,
                           "output_id": str(uuid4()), "language": language,
                           "timezone": memory.settings.timezone, "generated_at": stamp(utc_now()),
                           "reading_minutes": max(1, math.ceil(words / 180)),
                           "accuracy_review": accuracy}
                dependencies = [{name: source[name] for name in (
                    "id", "source_version", "transcript_version") if name in source}
                                for source in sources]
                with memory.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    memory.recaps.guard(day, now, groups)(db)
                    if memory.settings.recap_language != language:
                        raise EvidenceChanged("Recap language changed; retry the recap")
                    memory.store.verify_sources(db, sources)
                    previous = db.execute("SELECT content FROM day_overviews WHERE day=?",
                                          (day.isoformat(),)).fetchone()
                    if previous:
                        db.execute("DELETE FROM output_evidence WHERE id=?",
                                   (json.loads(previous["content"]).get("output_id"),))
                    self.save_output(db, content, sources)
                    db.execute("INSERT OR REPLACE INTO day_overviews VALUES(?,?,?,?)", (
                        day.isoformat(), key, json.dumps(content), json.dumps(dependencies)))
                return self.cached(day)
