"""On-demand transcript translations, separate from authoritative archive evidence."""

import asyncio
import json
import re
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, TypedDict
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


def translation_targets(memory: Memory, requested: str | None) -> tuple[str, list[str]]:
    """Choose only configured targets; an empty list disables translation."""
    languages = list(memory.settings.translation_languages)
    if requested is None:
        return (languages[0] if languages else ""), languages
    try:
        target = translation_language(requested)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if target not in languages:
        raise HTTPException(422, "Choose a translation language enabled in Settings")
    return target, languages


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


class LanguageAnalysis(TypedDict):
    dominant_language: str | None
    dominant_language_share: float
    language_shares: dict[str, float]


def _language_sample(text: str) -> str:
    """Sample equal-width strata across long recordings, including their final stratum."""
    if len(text) <= 3072:
        return text
    samples = []
    for index in range(16):
        left, right = len(text) * index // 16, len(text) * (index + 1) // 16
        start = (left + right - 192) // 2
        end = start + 192
        # Keep complete words where possible without dropping unspaced scripts.
        if start > left and text[start - 1:start + 1].isalpha():
            boundary = re.search(r"\W", text[start:start + 24])
            if boundary:
                start += boundary.end()
        if end < right and text[end - 1:end + 1].isalpha():
            boundaries = list(re.finditer(r"\W", text[end - 24:end]))
            if boundaries:
                end = end - 24 + boundaries[-1].start()
        samples.append(text[start:end])
    return "\n".join(samples)


def analyze_transcript_languages(text: str) -> LanguageAnalysis:
    """Measure language coverage locally, with context for ambiguous speech fragments.

    Short windows can confuse related languages or misread conversational endings.
    A confidently classified paragraph supplies context, while corroborated foreign
    windows still count against its language. Coverage is letter-weighted, not the
    detector's document confidence. Long recordings use equal-width samples spread
    across the entire recording; shares are estimates. Work and retained text stay
    bounded, and no transcript text is cached beyond this request.
    """
    unknown: LanguageAnalysis = {
        "dominant_language": None, "dominant_language_share": 0.0, "language_shares": {},
    }
    if not text.strip():
        return unknown
    factory = language_profiles()
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

    matched: dict[str, int] = {}
    total = 0
    for paragraph in _language_sample(text).splitlines():
        # Speaker labels are presentation metadata, not spoken text.
        paragraph = re.sub(r"^\s*(?:Speaker|Taler)\s*\d+\s*:\s*", "", paragraph, flags=re.I)
        paragraph_words = re.findall(r"[^\W\d_]+", paragraph, flags=re.UNICODE)
        normalized = " ".join(paragraph_words)
        contextual = ""
        if len(paragraph_words) >= 24 and not CJK_LETTERS.search(paragraph):
            context = classify(normalized)
            if context is None:
                return unknown
            candidate = max(context, key=context.get, default="")
            if context.get(candidate, 0) >= 0.8:
                contextual = candidate
        for utterance in re.split(r"[.!?;。！？]+", paragraph):
            utterance = re.sub(r"^\s*(?:Speaker|Taler)\s*\d+\s*:\s*", "", utterance, flags=re.I)
            # Script changes also separate languages without sentence punctuation.
            script_runs = f"({CJK_LETTERS.pattern}(?:[\\W\\d_]+{CJK_LETTERS.pattern})*)"
            for run in re.split(script_runs, utterance):
                letters = "".join(character for character in run if character.isalpha())
                total += len(letters)
                if len(letters) < 12:
                    if contextual:
                        matched[contextual] = matched.get(contextual, 0) + len(letters)
                    continue
                cjk = bool(CJK_LETTERS.fullmatch(letters))
                if cjk:
                    units, width, separator = letters, 48, ""
                else:
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
                        return unknown
                    detected = max(probabilities, key=probabilities.get, default="")
                    language = detected if probabilities.get(detected, 0) >= 0.9 else ""
                    if language != contextual and contextual and not cjk:
                        foreign = language
                        corroborated = False
                        if foreign:
                            # A clear foreign sentence must remain translatable, even
                            # when the paragraph's dominant language hides it.
                            sentence = classify(separator.join(units)) if len(letters) >= 32 else {}
                            if sentence is None:
                                return unknown
                            corroborated = sentence.get(foreign, 0) >= 0.9
                            if not corroborated:
                                index = normalized.find(passage)
                                word_index = normalized[:index].count(" ")
                                left = max(0, min(word_index - 4, len(paragraph_words) - 16))
                                context = classify(" ".join(paragraph_words[left:left + 16]))
                                if context is None:
                                    return unknown
                                corroborated = context.get(foreign, 0) >= 0.9
                        if not corroborated:
                            language = contextual
                    if language:
                        matched[language] = matched.get(language, 0) + sum(
                            len(unit) for unit in units[start:start + width]
                        )
    if not total or not matched:
        return unknown
    shares = {code: count / total for code, count in sorted(matched.items())}
    dominant = max(shares, key=shares.get)
    return {"dominant_language": dominant, "dominant_language_share": shares[dominant],
            "language_shares": shares}


def transcript_in_language(text: str, language: str) -> bool:
    """A language covering at least 90% of spoken letters needs no translation."""
    return analyze_transcript_languages(text)["language_shares"].get(
        language.lower().split("-")[0], 0
    ) >= 0.9


def recommended_translation_language(
    analysis: LanguageAnalysis, languages: list[str],
) -> str | None:
    """Offer the dominant enabled language only for observed mixed-language speech."""
    if (
        analysis["dominant_language_share"] >= 0.9
        or len(analysis["language_shares"]) < 2
        or sum(analysis["language_shares"].values()) < 0.8
    ):
        return None
    return next((code for code in languages
                 if code.split("-")[0] == analysis["dominant_language"]), None)


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


def read_translation(
    memory: Memory, audio_id: str, start: int | None, end: int | None, version: str | None,
    requested_language: str | None,
) -> tuple[dict, dict]:
    source = memory.store.get_audio(audio_id)
    if source is None:
        raise HTTPException(404, "Transcript not found")
    passage = translation_passage(source, start, end, version)
    language, languages = translation_targets(memory, requested_language)
    analysis = analyze_transcript_languages(passage if source["state"] == "done" else "")
    recommended = recommended_translation_language(analysis, languages)
    if requested_language is None and recommended:
        language = recommended
    redundant = [
        code for code in languages if source["state"] == "done"
        and analysis["language_shares"].get(code.split("-")[0], 0) >= 0.9
    ]
    already_in_target = language in redundant
    return {
        "language": language,
        "language_analysis": analysis,
        "recommended_language": recommended,
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


async def generate_translation(
    memory: Memory, audio_id: UUID, start_offset: int | None = None,
    end_offset: int | None = None, transcript_version: str | None = None,
    language: str | None = None,
) -> dict:
    key = str(audio_id)
    # Coalesce repeat clicks by reading the cache again after acquiring the lock.
    async with memory.translation_lock:
        result, source = await run_in_threadpool(
            read_translation, memory, key, start_offset, end_offset, transcript_version, language
        )
        language = result["language"]
        if not language:
            raise HTTPException(409, "Add a translation language in Settings before translating")
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
        memory.settings.require_inference("llm")
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


def translation_routes(get_memory: Callable[[], Memory]) -> APIRouter:
    router = APIRouter(prefix="/v1/transcripts")

    @router.get("/{audio_id}/translation")
    def translation(
        audio_id: UUID, start_offset: int | None = Query(None, ge=0),
        end_offset: int | None = Query(None, ge=1), transcript_version: str | None = None,
        language: str | None = None,
    ) -> dict:
        return read_translation(
            get_memory(), str(audio_id), start_offset, end_offset, transcript_version, language
        )[0]

    @router.post("/{audio_id}/translation")
    async def translate(
        audio_id: UUID, start_offset: int | None = Query(None, ge=0),
        end_offset: int | None = Query(None, ge=1), transcript_version: str | None = None,
        language: str | None = None,
    ) -> dict:
        return await generate_translation(
            get_memory(), audio_id, start_offset, end_offset, transcript_version, language
        )

    return router
