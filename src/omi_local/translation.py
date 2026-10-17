"""On-demand transcript translations, separate from authoritative archive evidence."""

import asyncio
import json
import re
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from langdetect import DetectorFactory, LangDetectException
from langdetect.detector_factory import PROFILES_DIRECTORY
from starlette.concurrency import run_in_threadpool

from .domain import stamp, utc_now
from .inference import InferenceError
from .settings import translation_language

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
CREATE TABLE IF NOT EXISTS transcript_passage_translations (
 audio_id TEXT NOT NULL, language TEXT NOT NULL, transcript_version TEXT NOT NULL,
 start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
 text TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(audio_id,language,start_offset,end_offset)
);
CREATE TRIGGER IF NOT EXISTS passage_translation_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM transcript_passage_translations WHERE audio_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS passage_translation_source_update
AFTER UPDATE OF text,state,transcript_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version BEGIN
 DELETE FROM transcript_passage_translations WHERE audio_id=NEW.id;
END;
"""


def translation_passage(
    source: dict, start: int | None, end: int | None, version: str | None
) -> str:
    """Offsets refer to Unicode codepoints in one immutable transcript version."""
    text = source["text"] or ""
    if start is None and end is None:
        return text
    if start is None or end is None or not version:
        raise HTTPException(422, "A valid passage and transcript version are required")
    if version != source["transcript_version"]:
        raise HTTPException(409, "Transcript changed; reopen the passage and try again")
    if not 0 <= start < end <= len(text):
        raise HTTPException(422, "A valid passage and transcript version are required")
    return text[start:end]


class TranslationStore:
    def transcript_translation(
        self, audio_id: str, language: str, span: tuple[int, int] | None = None
    ) -> dict | None:
        table = "transcript_passage_translations" if span else "transcript_translations"
        with self.connect() as db:
            row = db.execute(
                "SELECT t.language,t.transcript_version,t.text,t.created_at "
                f"FROM {table} t JOIN audio a ON a.id=t.audio_id "
                "WHERE t.audio_id=? AND t.language=? AND a.state='done' "
                "AND t.transcript_version=a.transcript_version AND length(trim(a.text))>0"
                + (" AND t.start_offset=? AND t.end_offset=?" if span else ""),
                (audio_id, language.lower(), *(span or ())),
            ).fetchone()
            return dict(row) if row else None

    def save_translation(
        self, source: dict, language: str, text: str, fallback_languages: list[str],
        span: tuple[int, int] | None = None,
    ) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM audio WHERE id=?", (source["id"],)).fetchone()
            setting = db.execute(
                "SELECT value FROM runtime_settings WHERE key='translation_languages'"
            ).fetchone()
            # Settings writes and evidence edits share this SQLite writer lock.
            active_languages = json.loads(setting["value"]) if setting else fallback_languages
            if (
                not current or current["state"] != "done"
                or current["transcript_version"] != source["transcript_version"]
                or current["text"] != source["text"]
            ):
                raise HTTPException(409, "Transcript changed; try again")
            if language.lower() not in {value.lower() for value in active_languages}:
                raise HTTPException(
                    409, "Translation languages changed; choose a language and try again"
                )
            result = {
                "language": language,
                "transcript_version": source["transcript_version"],
                "text": text,
                "created_at": stamp(utc_now()),
            }
            if span:
                translation_passage(source, *span, source["transcript_version"])
            table = "transcript_passage_translations" if span else "transcript_translations"
            placeholders = "?,?,?,?,?,?,?" if span else "?,?,?,?,?"
            db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES({placeholders})",
                (source["id"], language.lower(), result["transcript_version"], *(span or ()),
                 text, result["created_at"]),
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
    """Measure language coverage locally, with context for ambiguous speech fragments.

    Short windows can confuse related languages or misread conversational endings.
    A confidently classified paragraph supplies context, while corroborated foreign
    windows still count against its language. Coverage is letter-weighted, not the
    detector's document confidence. Classification and text remain request-local.
    """
    target = language.lower().split("-")[0]
    if target == "auto" or not text.strip():
        return False
    factory = language_profiles()
    if target not in {code.split("-")[0] for code in factory.get_lang_list()}:
        return False
    checked: dict[str, dict[str, float]] = {}

    def classify(passage: str, cjk: bool = False) -> dict[str, float] | None:
        if passage in checked:
            return checked[passage]
        # Bound all work, including paragraph and corroboration checks.
        if len(checked) >= 256:
            return None
        detector = factory.create()
        if cjk:
            detector.set_prior_map({"ja": 1, "zh-cn": 1, "zh-tw": 1})
        detector.append(passage)
        probabilities: dict[str, float] = {}
        try:
            for item in detector.get_probabilities():
                code = item.lang.split("-")[0]
                probabilities[code] = probabilities.get(code, 0) + item.prob
        except LangDetectException:
            pass
        checked[passage] = probabilities
        return probabilities

    matched, total = 0, 0
    for paragraph in text.splitlines():
        # Speaker labels are presentation metadata, not spoken text.
        paragraph = re.sub(r"^\s*(?:Speaker|Taler)\s*\d+\s*:\s*", "", paragraph, flags=re.I)
        paragraph_words = re.findall(r"[^\W\d_]+", paragraph, flags=re.UNICODE)
        normalized = " ".join(paragraph_words)
        contextual = False
        if len(paragraph_words) >= 24 and not CJK_LETTERS.search(paragraph):
            context = classify(normalized)
            if context is None:
                return False
            contextual = context.get(target, 0) >= 0.8
        for utterance in re.split(r"[.!?;。！？]+", paragraph):
            utterance = re.sub(r"^\s*(?:Speaker|Taler)\s*\d+\s*:\s*", "", utterance, flags=re.I)
            # Script changes also separate languages without sentence punctuation.
            script_runs = f"({CJK_LETTERS.pattern}(?:[\\W\\d_]+{CJK_LETTERS.pattern})*)"
            for run in re.split(script_runs, utterance):
                letters = "".join(character for character in run if character.isalpha())
                total += len(letters)
                if len(letters) < 12:
                    if contextual:
                        matched += len(letters)
                    continue
                cjk = bool(CJK_LETTERS.fullmatch(letters))
                if cjk:
                    if target not in {"zh", "ja"}:
                        continue
                    units, width, separator = letters, 48, ""
                else:
                    if target in {"zh", "ja"}:
                        continue
                    units = re.findall(r"[^\W\d_]+", run, flags=re.UNICODE)
                    if len(units) < 3 or all(word.istitle() or word.isupper() for word in units):
                        continue
                    width, separator = 8, " "
                for start in range(0, len(units), width):
                    # Give the tail context, but count each letter only once.
                    context_start = min(start, max(0, len(units) - width))
                    passage = separator.join(units[context_start:context_start + width])
                    probabilities = classify(passage, cjk)
                    if probabilities is None:
                        return False
                    same_language = probabilities.get(target, 0) >= 0.9
                    if not same_language and contextual:
                        foreign = max(probabilities, key=probabilities.get, default="")
                        corroborated = False
                        if foreign != target and probabilities.get(foreign, 0) >= 0.9:
                            # A clear foreign sentence must remain translatable, even
                            # when the paragraph's dominant language hides it.
                            sentence = classify(separator.join(units)) if len(letters) >= 32 else {}
                            if sentence is None:
                                return False
                            corroborated = sentence.get(foreign, 0) >= 0.9
                            if not corroborated:
                                index = normalized.find(passage)
                                word_index = normalized[:index].count(" ")
                                left = max(0, min(word_index - 4, len(paragraph_words) - 16))
                                context = classify(" ".join(paragraph_words[left:left + 16]))
                                if context is None:
                                    return False
                                corroborated = context.get(foreign, 0) >= 0.9
                        same_language = not corroborated
                    if same_language:
                        matched += sum(len(unit) for unit in units[start:start + width])
    return total > 0 and matched * 100 >= total * 95


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

    def read(
        memory: Memory, audio_id: str, start: int | None, end: int | None, version: str | None,
        requested_language: str | None,
    ) -> tuple[dict, dict]:
        source = memory.store.get_audio(audio_id)
        if source is None:
            raise HTTPException(404, "Transcript not found")
        passage = translation_passage(source, start, end, version)
        languages = list(memory.settings.translation_languages)
        language = languages[0] if languages else ""
        if requested_language is not None:
            try:
                language = translation_language(requested_language)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if language not in languages:
                raise HTTPException(422, "Choose a translation language enabled in Settings")
        already_in_target = bool(language) and source["state"] == "done" and transcript_in_language(
            passage, language
        )
        redundant = [
            code for code in languages if source["state"] == "done"
            and (already_in_target if code == language else transcript_in_language(passage, code))
        ]
        return {
            "language": language,
            "available_languages": languages,
            "redundant_languages": redundant,
            "already_in_target_language": already_in_target,
            "translation": (
                None if already_in_target or not language
                else memory.store.transcript_translation(
                    audio_id, language, (start, end) if start is not None else None
                )
            ),
        }, source

    @router.get("/{audio_id}/translation")
    def translation(
        audio_id: UUID, start_offset: int | None = Query(None, ge=0),
        end_offset: int | None = Query(None, ge=1), transcript_version: str | None = None,
        language: str | None = None,
    ) -> dict:
        return read(
            get_memory(), str(audio_id), start_offset, end_offset, transcript_version, language
        )[0]

    @router.post("/{audio_id}/translation")
    async def translate(
        audio_id: UUID, start_offset: int | None = Query(None, ge=0),
        end_offset: int | None = Query(None, ge=1), transcript_version: str | None = None,
        language: str | None = None,
    ) -> dict:
        memory, key = get_memory(), str(audio_id)
        # Coalesce repeat clicks by reading the cache again after acquiring the lock.
        async with memory.translation_lock:
            result, source = await run_in_threadpool(
                read, memory, key, start_offset, end_offset, transcript_version, language
            )
            language = result["language"]
            if not language:
                raise HTTPException(
                    409, "Add a translation language in Settings before translating"
                )
            if language not in memory.settings.translation_languages:
                raise HTTPException(
                    409, "Translation languages changed; choose a language and try again"
                )
            result["available_languages"] = list(memory.settings.translation_languages)
            current = memory.store.get_audio(key)
            if not current or any(
                current[field] != source[field] for field in ("state", "transcript_version", "text")
            ):
                raise HTTPException(409, "Transcript changed; try again")
            if result["already_in_target_language"]:
                return result
            if result["translation"]:
                return result
            if source["state"] != "done" or not (source["text"] or "").strip():
                raise HTTPException(409, "A completed, nonempty transcript is required")
            try:
                async with asyncio.timeout(memory.settings.inference_timeout_seconds):
                    parts = []
                    for chunk in translation_chunks(translation_passage(
                        source, start_offset, end_offset, transcript_version
                    )):
                        translated = await memory.inference.translate(chunk, language)
                        if not isinstance(translated, str) or not translated.strip():
                            raise InferenceError("Empty translation")
                        parts.append(translated.strip())
                    text = "\n".join(parts)
            except (InferenceError, httpx.HTTPError, TimeoutError) as exc:
                raise HTTPException(
                    502, "Translation failed. The original transcript is available."
                ) from exc
            if language not in memory.settings.translation_languages:
                raise HTTPException(
                    409, "Translation languages changed; choose a language and try again"
                )
            result["translation"] = memory.store.save_translation(
                source, language, text, memory.settings.translation_languages,
                (start_offset, end_offset) if start_offset is not None else None,
            )
            result["available_languages"] = list(memory.settings.translation_languages)
            return result

    return router
