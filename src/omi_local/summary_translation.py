"""On-demand translations of saved summaries, separate from generated evidence."""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from .completeness import revision
from .domain import stamp, utc_now
from .inference import InferenceError
from .translation import (
    analyze_transcript_languages,
    recommended_translation_language,
    translation_chunks,
    translation_targets,
)

if TYPE_CHECKING:
    from .service import Memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS summary_outputs (
 id TEXT PRIMARY KEY, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS summary_output_sources (
 output_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(output_id,source_id)
);
CREATE INDEX IF NOT EXISTS summary_output_source ON summary_output_sources(source_id,output_id);
CREATE TABLE IF NOT EXISTS summary_translations (
 output_id TEXT NOT NULL, language TEXT NOT NULL, summary_version TEXT NOT NULL,
 text TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(output_id,language)
);
CREATE TRIGGER IF NOT EXISTS summary_translation_output_delete
AFTER DELETE ON output_evidence BEGIN
 DELETE FROM summary_outputs WHERE id=OLD.id;
 DELETE FROM summary_output_sources WHERE output_id=OLD.id;
 DELETE FROM summary_translations WHERE output_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS summary_translation_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM summary_translations WHERE output_id IN (
 SELECT output_id FROM summary_output_sources WHERE source_id=OLD.id);
 DELETE FROM summary_outputs WHERE id IN (
 SELECT output_id FROM summary_output_sources WHERE source_id=OLD.id);
 DELETE FROM summary_output_sources WHERE output_id IN (
 SELECT output_id FROM summary_output_sources WHERE source_id=OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS summary_translation_source_change
AFTER UPDATE OF text,state,transcript_version,source_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version
 OR OLD.source_version IS NOT NEW.source_version BEGIN
 DELETE FROM summary_translations WHERE output_id IN (
 SELECT output_id FROM summary_output_sources WHERE source_id=NEW.id);
 DELETE FROM summary_outputs WHERE id IN (
 SELECT output_id FROM summary_output_sources WHERE source_id=NEW.id);
 DELETE FROM summary_output_sources WHERE output_id IN (
 SELECT output_id FROM summary_output_sources WHERE source_id=NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS summary_translation_recap_delete
AFTER DELETE ON diary_versions BEGIN
 DELETE FROM summary_translations WHERE output_id=OLD.id;
 DELETE FROM summary_outputs WHERE id=OLD.id;
 DELETE FROM summary_output_sources WHERE output_id=OLD.id;
END;
"""


class SummaryTranslationStore:
    def _summary_source(self, db, output_id: str) -> dict:
        evidence = db.execute(
            "SELECT payload FROM output_evidence WHERE id=?", (output_id,)
        ).fetchone()
        row = db.execute("SELECT text FROM summary_outputs WHERE id=?", (output_id,)).fetchone()
        # Existing saved recaps predate the summary text projection.
        if row is None:
            saved = db.execute(
                "SELECT content FROM diary_versions WHERE id=?", (output_id,)
            ).fetchone()
            text = json.loads(saved["content"]).get("summary") if saved else None
            if text is None and db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='generation_jobs'"
            ).fetchone():
                job = db.execute(
                    "SELECT result FROM generation_jobs WHERE state='completed' "
                    "AND json_extract(result,'$.output_id')=? "
                    "AND kind IN ('rewind','meeting_recap') LIMIT 1", (output_id,),
                ).fetchone()
                text = json.loads(job["result"]).get("summary") if job else None
        else:
            text = row["text"]
        if not evidence or not isinstance(text, str) or not text.strip():
            raise HTTPException(404, "Summary not found")
        sources = json.loads(evidence["payload"])["selected"]
        for source in sources:
            current = db.execute("SELECT * FROM audio WHERE id=?", (source["id"],)).fetchone()
            if (
                not current or current["state"] != "done"
                or revision(dict(current)) != source["revision"]
                or any(source.get(field) is not None and source[field] != current[field]
                       for field in ("source_version", "transcript_version"))
            ):
                raise HTTPException(409, "Summary evidence changed; generate it again")
        return {"id": output_id, "text": text, "sources": sources,
                "summary_version": hashlib.sha256(text.encode()).hexdigest()}

    def summary_translation_source(self, output_id: str) -> dict:
        with self.connect() as db:
            return self._summary_source(db, output_id)

    def summary_translation(self, source: dict, language: str) -> dict | None:
        with self.connect() as db:
            current = self._summary_source(db, source["id"])
            if current != source:
                raise HTTPException(409, "Summary changed; try again")
            row = db.execute(
                "SELECT language,summary_version,text,created_at FROM summary_translations "
                "WHERE output_id=? AND language=? AND summary_version=?",
                (source["id"], language.lower(), source["summary_version"]),
            ).fetchone()
            return dict(row) if row else None

    def save_summary_translation(
        self, source: dict, language: str, text: str, fallback_languages: list[str],
    ) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._summary_source(db, source["id"])
            if current != source:
                raise HTTPException(409, "Summary changed; try again")
            setting = db.execute(
                "SELECT value FROM runtime_settings WHERE key='translation_languages'"
            ).fetchone()
            languages = json.loads(setting["value"]) if setting else fallback_languages
            if language.lower() not in {value.lower() for value in languages}:
                raise HTTPException(
                    409, "Translation languages changed; choose a language and try again"
                )
            # Register legacy outputs for the same dependency cleanup as new summaries.
            db.executemany("INSERT OR IGNORE INTO summary_output_sources VALUES(?,?)",
                           [(source["id"], item["id"]) for item in source["sources"]])
            result = {"language": language, "summary_version": source["summary_version"],
                      "text": text, "created_at": stamp(utc_now())}
            db.execute("INSERT OR REPLACE INTO summary_translations VALUES(?,?,?,?,?)",
                       (source["id"], language.lower(), source["summary_version"], text,
                        result["created_at"]))
            return result


def read_summary_translation(
    memory: Memory, output_id: str, requested_language: str | None,
) -> tuple[dict, dict]:
    source = memory.store.summary_translation_source(output_id)
    language, languages = translation_targets(memory, requested_language)
    # Citation IDs and Markdown are presentation metadata, not summary language.
    reading_text = re.sub(
        r"\[[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\]", "", source["text"], flags=re.I
    )
    reading_text = " ".join(re.sub(r"(?m)^#{1,6}\s+", "", reading_text).split())
    analysis = analyze_transcript_languages(reading_text)
    recommended = recommended_translation_language(analysis, languages)
    if requested_language is None and recommended:
        language, languages = translation_targets(memory, recommended)
    redundant = [code for code in languages
                 if analysis["language_shares"].get(code.split("-")[0], 0) >= 0.9]
    already = language in redundant
    return {
        "language": language, "summary_version": source["summary_version"],
        "language_analysis": analysis, "recommended_language": recommended,
        "available_languages": languages, "redundant_languages": redundant,
        "already_in_target_language": already,
        "translation": None if already or not language else memory.store.summary_translation(
            source, language
        ),
    }, source


async def generate_summary_translation(
    memory: Memory, output_id: UUID, language: str | None = None,
) -> dict:
    async with memory.translation_lock:
        result, source = await run_in_threadpool(
            read_summary_translation, memory, str(output_id), language
        )
        language = result["language"]
        if not language:
            raise HTTPException(409, "Add a translation language in Settings before translating")
        if language not in memory.settings.translation_languages:
            raise HTTPException(
                409, "Translation languages changed; choose a language and try again"
            )
        result["available_languages"] = list(memory.settings.translation_languages)
        if memory.store.summary_translation_source(str(output_id)) != source:
            raise HTTPException(409, "Summary changed; try again")
        if result["already_in_target_language"] or result["translation"]:
            return result
        try:
            async with asyncio.timeout(memory.settings.inference_timeout_seconds):
                parts = []
                for chunk in translation_chunks(source["text"]):
                    translated = await memory.inference.translate(chunk, language)
                    if not isinstance(translated, str) or not translated.strip():
                        raise InferenceError("Empty translation")
                    parts.append(translated.strip())
                text = "\n".join(parts)
        except (InferenceError, httpx.HTTPError, TimeoutError) as exc:
            raise HTTPException(
                502, "Translation failed. The original summary is available."
            ) from exc
        result["translation"] = memory.store.save_summary_translation(
            source, language, text, memory.settings.translation_languages,
        )
        result["available_languages"] = list(memory.settings.translation_languages)
        return result


def summary_translation_routes(get_memory: Callable[[], Memory]) -> APIRouter:
    router = APIRouter(prefix="/v1/outputs")

    @router.get("/{output_id}/translation")
    def translation(output_id: UUID, language: str | None = None) -> dict:
        return read_summary_translation(get_memory(), str(output_id), language)[0]

    @router.post("/{output_id}/translation")
    async def translate(output_id: UUID, language: str | None = None) -> dict:
        return await generate_summary_translation(get_memory(), output_id, language)

    return router
