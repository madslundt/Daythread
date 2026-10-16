"""On-demand transcript translations, separate from authoritative archive evidence."""

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException

from .domain import stamp, utc_now
from .inference import InferenceError

if TYPE_CHECKING:
    from .service import Memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript_translations (
 audio_id TEXT NOT NULL, language TEXT NOT NULL, transcript_version TEXT NOT NULL,
 text TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(audio_id,language)
);
CREATE TRIGGER IF NOT EXISTS translation_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM transcript_translations WHERE audio_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS translation_source_update
AFTER UPDATE OF text,state,transcript_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version BEGIN
 DELETE FROM transcript_translations WHERE audio_id=NEW.id;
END;
"""


class TranslationStore:
    def transcript_translation(self, audio_id: str, language: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT t.language,t.transcript_version,t.text,t.created_at "
                "FROM transcript_translations t JOIN audio a ON a.id=t.audio_id "
                "WHERE t.audio_id=? AND t.language=? AND a.state='done' "
                "AND t.transcript_version=a.transcript_version AND length(trim(a.text))>0",
                (audio_id, language.lower()),
            ).fetchone()
            return dict(row) if row else None

    def save_translation(
        self, source: dict, language: str, text: str, fallback_language: str
    ) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM audio WHERE id=?", (source["id"],)).fetchone()
            setting = db.execute(
                "SELECT value FROM runtime_settings WHERE key='summary_language'"
            ).fetchone()
            # Settings writes and evidence edits share this SQLite writer lock.
            active_language = json.loads(setting["value"]) if setting else fallback_language
            if (
                not current or current["state"] != "done"
                or current["transcript_version"] != source["transcript_version"]
                or current["text"] != source["text"]
                or active_language.lower() != language.lower()
            ):
                raise HTTPException(409, "Transcript or summary language changed; try again")
            result = {
                "language": language,
                "transcript_version": source["transcript_version"],
                "text": text,
                "created_at": stamp(utc_now()),
            }
            db.execute(
                "INSERT OR REPLACE INTO transcript_translations VALUES(?,?,?,?,?)",
                (source["id"], language.lower(), result["transcript_version"], text,
                 result["created_at"]),
            )
            return result


def translation_chunks(text: str, size: int = 4000) -> list[str]:
    """Retain every character; prefer utterance/word boundaries within bounded calls."""
    chunks = []
    while text:
        end = min(len(text), size)
        if end < len(text):
            boundary = max(text.rfind(mark, size // 2, end) for mark in ("\n", ". ", " "))
            if boundary >= 0:
                end = boundary + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks


def translation_routes(get_memory: Callable[[], Memory]) -> APIRouter:
    router = APIRouter(prefix="/v1/transcripts")

    def read(memory: Memory, audio_id: str) -> dict:
        if memory.store.get_audio(audio_id) is None:
            raise HTTPException(404, "Transcript not found")
        language = memory.settings.summary_language
        return {
            "language": language,
            "translation": memory.store.transcript_translation(audio_id, language),
        }

    @router.get("/{audio_id}/translation")
    def translation(audio_id: UUID) -> dict:
        return read(get_memory(), str(audio_id))

    @router.post("/{audio_id}/translation")
    async def translate(audio_id: UUID) -> dict:
        memory, key = get_memory(), str(audio_id)
        # Coalesce repeat clicks by reading the cache again after acquiring the lock.
        async with memory.translation_lock:
            result = read(memory, key)
            language = result["language"]
            if language.lower() == "auto":
                raise HTTPException(409, "Choose a summary language in Settings before translating")
            if result["translation"]:
                return result
            source = memory.store.get_audio(key)
            if not source or source["state"] != "done" or not (source["text"] or "").strip():
                raise HTTPException(409, "A completed, nonempty transcript is required")
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
                    502, "Translation failed. The original transcript is available."
                ) from exc
            if memory.settings.summary_language.lower() != language.lower():
                raise HTTPException(409, "Summary language changed; try again")
            result["translation"] = memory.store.save_translation(
                source, language, text, memory.settings.summary_language
            )
            return result

    return router
