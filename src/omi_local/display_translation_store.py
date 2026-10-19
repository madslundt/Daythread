"""Versioned, disposable translations of canonical display labels, never evidence."""

import hashlib
import json

from fastapi import HTTPException

from .domain import stamp, utc_now
from .inbox_store import decode

MAX_CACHE = 1000
VERSION = "display-labels-1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS display_translations (
 content_key TEXT PRIMARY KEY, kind TEXT NOT NULL, record_id TEXT NOT NULL,
 language TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS display_translation_record ON display_translations(kind,record_id);
CREATE TABLE IF NOT EXISTS display_translation_sources (
 content_key TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(content_key,source_id)
);
CREATE INDEX IF NOT EXISTS display_translation_source ON display_translation_sources(source_id);
CREATE TRIGGER IF NOT EXISTS display_translation_cache_deleted
AFTER DELETE ON display_translations BEGIN
 DELETE FROM display_translation_sources WHERE content_key=OLD.content_key;
END;
CREATE TRIGGER IF NOT EXISTS display_translation_source_deleted BEFORE DELETE ON audio BEGIN
 DELETE FROM display_translations WHERE content_key IN (
 SELECT content_key FROM display_translation_sources WHERE source_id=OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS display_translation_source_changed
AFTER UPDATE OF text,state,source_version,transcript_version,topic_details ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.source_version IS NOT NEW.source_version
 OR OLD.transcript_version IS NOT NEW.transcript_version
 OR OLD.topic_details IS NOT NEW.topic_details BEGIN
 DELETE FROM display_translations WHERE content_key IN (
 SELECT content_key FROM display_translation_sources WHERE source_id=NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS display_translation_inbox_changed AFTER UPDATE ON inbox_items BEGIN
 DELETE FROM display_translations WHERE kind='inbox' AND record_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS display_translation_inbox_deleted AFTER DELETE ON inbox_items BEGIN
 DELETE FROM display_translations WHERE kind='inbox' AND record_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS display_translation_decision_changed AFTER UPDATE ON decisions BEGIN
 DELETE FROM display_translations WHERE kind='decision' AND record_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS display_translation_decision_deleted AFTER DELETE ON decisions BEGIN
 DELETE FROM display_translations WHERE kind='decision' AND record_id=OLD.id;
END;
"""


def missing():
    raise HTTPException(404, "Display item not found")


def changed():
    raise HTTPException(409, "Display item changed; refresh and try again")


def resolve(db, ref):
    key, kind = ref["id"], ref["kind"]
    if kind in {"inbox", "decision"}:
        table = "inbox_items" if kind == "inbox" else "decisions"
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            missing()
        if row["version"] != ref["version"]:
            changed()
        if kind == "inbox":
            title = decode(row)["title"]
            ids = {r[0] for r in db.execute(
                "SELECT DISTINCT d.source_id FROM inbox_dependencies d "
                "JOIN inbox_occurrences o ON o.job_id=d.job_id WHERE o.item_id=?", (key,),
            )}
        else:
            content = json.loads(row["content"]) if row["content"] else None
            if not content or row["evidence_condition"] == "removed":
                missing()
            title = content["choice"]["text"]
            ids = {r[0] for r in db.execute(
                "SELECT DISTINCT source_id FROM decision_dependencies WHERE decision_id=?", (key,),
            )}
        detail = {"version": row["version"]}
    else:
        row = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
        if row is None or row["state"] == "expired":
            missing()
        if (row["source_version"] != ref["source_version"]
                or row["transcript_version"] != ref["transcript_version"]):
            changed()
        details = json.loads(row["topic_details"] or "{}")
        sections = [s for s in details.get("sections", [])
                    if s.get("start_offset") == ref["start_offset"]
                    and s.get("end_offset") == ref["end_offset"]
                    and (not ref.get("chapter_id") or s.get("chapter_id") == ref["chapter_id"])]
        if (row["state"] != "done" or not 0 <= ref["start_offset"] < ref["end_offset"]
                <= len(row["text"] or "") or len(sections) != 1):
            changed()
        detail, title = sections[0], sections[0].get("title")
        ids = {key, *(r[0] for r in db.execute(
            "SELECT source_id FROM recording_topic_sources WHERE context_key=?",
            (details.get("context", ""),),
        ))}
    if not isinstance(title, str) or not title.strip() or not ids:
        missing()
    sources = []
    for source_id in sorted(ids):
        source = db.execute(
            "SELECT id,state,source_version,transcript_version FROM audio WHERE id=?", (source_id,),
        ).fetchone()
        if source is None or source["state"] == "expired":
            missing()
        sources.append(dict(source))
    return {"ref": ref, "original_title": title, "detail": detail, "sources": sources}


def snapshot(db, memory, ref, language):
    value = resolve(db, ref)
    identity = {**value, "language": language, "version": VERSION,
                "model": memory.settings.llm_route("summary").identity}
    value["content_key"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return value


class DisplayTranslationStore:
    def __init__(self, memory):
        self.memory, self.archive = memory, memory.store

    def load(self, refs, language):
        with self.archive.connect() as db:
            db.execute("BEGIN")
            values = [snapshot(db, self.memory, ref, language) for ref in refs]
            cached = {}
            for value in values:
                row = db.execute("SELECT title FROM display_translations WHERE content_key=?",
                                 (value["content_key"],)).fetchone()
                if row:
                    cached[value["content_key"]] = row["title"]
            return values, cached

    def save(self, values, translated, language):
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            setting = db.execute(
                "SELECT value FROM runtime_settings WHERE key='translation_languages'"
            ).fetchone()
            configured = self.memory.settings.translation_languages
            languages = json.loads(setting[0]) if setting else configured
            if language not in languages or language not in configured:
                raise HTTPException(
                    409, "Translation languages changed; choose a language and try again",
                )
            for value in values:
                if snapshot(db, self.memory, value["ref"], language) != value:
                    changed()
            for value in values:
                key = value["content_key"]
                if key not in translated:
                    continue
                db.execute("INSERT OR REPLACE INTO display_translations VALUES(?,?,?,?,?,?)", (
                    key, value["ref"]["kind"], value["ref"]["id"], language,
                    translated[key], stamp(utc_now()),
                ))
                db.executemany("INSERT OR IGNORE INTO display_translation_sources VALUES(?,?)",
                               [(key, source["id"]) for source in value["sources"]])
            db.execute(
                "DELETE FROM display_translations WHERE content_key NOT IN "
                "(SELECT content_key FROM display_translations "
                "ORDER BY created_at DESC,content_key LIMIT ?)", (MAX_CACHE,),
            )
