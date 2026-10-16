"""On-demand transcript translations, separate from authoritative archive evidence."""

import asyncio
import json
import re
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from langdetect import DetectorFactory, LangDetectException
from langdetect.detector_factory import PROFILES_DIRECTORY
from starlette.concurrency import run_in_threadpool

from .domain import stamp, utc_now
from .inference import InferenceError

if TYPE_CHECKING:
    from .service import Memory

CJK_LETTERS = re.compile(
    r"[\u3005\u3006\u303b\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff66-\uff9f\U00020000-\U0002fa1f]+"
)

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


@cache
def language_profiles() -> DetectorFactory:
    """Load bundled offline profiles once; detectors and transcript text stay request-local."""
    factory = DetectorFactory()
    factory.seed = 0
    factory.load_profile(PROFILES_DIRECTORY)
    return factory


def transcript_in_language(text: str, language: str) -> bool:
    """Suppress translation only when every checked passage confidently matches the target.

    Whole-document detection hides minority languages behind the dominant language. Check
    utterances and overlapping word/character windows, including long sentences' tails.
    Short/ambiguous passages and texts exceeding the local work budget deliberately keep
    translation available. No text is cached.
    """
    target = language.lower().split("-")[0]
    if target == "auto" or not text.strip():
        return False
    factory = language_profiles()
    if target not in {code.split("-")[0] for code in factory.get_lang_list()}:
        return False
    checked = set()
    for utterance in re.split(r"[.!?;\n。！？]+", text):
        # Speaker labels are presentation metadata, not words spoken in the conversation.
        utterance = re.sub(r"^\s*(?:Speaker|Taler)\s*\d+\s*:\s*", "", utterance, flags=re.I)
        letters = "".join(character for character in utterance if character.isalpha())
        if not letters:
            continue
        if len(letters) < 12:
            return False
        cjk = target in {"zh", "ja"}
        if cjk:
            # These scripts do not require word separators. Keep punctuation as model
            # context, and reject Latin/other scripts even when they are a small minority.
            if not CJK_LETTERS.fullmatch(letters):
                return False
            units, width, stride, separator = utterance.strip(), 48, 24, ""
        else:
            if CJK_LETTERS.search(letters):
                return False
            units = re.findall(r"[^\W\d_]+", utterance, flags=re.UNICODE)
            if len(units) < 3 or all(word.istitle() or word.isupper() for word in units):
                return False
            width, stride, separator = 8, 4, " "
        starts = set(range(0, max(1, len(units) - width + 1), stride)) | {
            max(0, len(units) - width)
        }
        for start in sorted(starts):
            passage = separator.join(units[start:start + width])
            if passage in checked:
                continue
            # Bound CPU work on archive reads; unexamined text must never be assumed to
            # share the dominant language. Repeated utterances need only one check.
            if len(checked) >= 256:
                return False
            detector = factory.create()
            if cjk:
                # Only Chinese/Japanese profiles fit exclusively Han/kana input. This
                # avoids Korean false positives on traditional Chinese with no Hangul.
                detector.set_prior_map({"ja": 1, "zh-cn": 1, "zh-tw": 1})
            detector.append(passage)
            try:
                matches = detector.get_probabilities()
            except LangDetectException:
                return False
            confidence = sum(item.prob for item in matches if item.lang.split("-")[0] == target)
            if confidence < 0.9:
                return False
            checked.add(passage)
    return bool(checked)


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

    def read(memory: Memory, audio_id: str) -> tuple[dict, dict]:
        source = memory.store.get_audio(audio_id)
        if source is None:
            raise HTTPException(404, "Transcript not found")
        language = memory.settings.summary_language
        already_in_target = source["state"] == "done" and transcript_in_language(
            source["text"] or "", language
        )
        return {
            "language": language,
            "already_in_target_language": already_in_target,
            "translation": (
                None if already_in_target
                else memory.store.transcript_translation(audio_id, language)
            ),
        }, source

    @router.get("/{audio_id}/translation")
    def translation(audio_id: UUID) -> dict:
        return read(get_memory(), str(audio_id))[0]

    @router.post("/{audio_id}/translation")
    async def translate(audio_id: UUID) -> dict:
        memory, key = get_memory(), str(audio_id)
        # Coalesce repeat clicks by reading the cache again after acquiring the lock.
        async with memory.translation_lock:
            result, source = await run_in_threadpool(read, memory, key)
            language = result["language"]
            if memory.settings.summary_language.lower() != language.lower():
                raise HTTPException(409, "Summary language changed; try again")
            current = memory.store.get_audio(key)
            if not current or any(
                current[field] != source[field] for field in ("state", "transcript_version", "text")
            ):
                raise HTTPException(409, "Transcript changed; try again")
            if language.lower() == "auto":
                raise HTTPException(409, "Choose a summary language in Settings before translating")
            if result["already_in_target_language"]:
                raise HTTPException(409, "Transcript is already in the summary language")
            if result["translation"]:
                return result
            if source["state"] != "done" or not (source["text"] or "").strip():
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
