"""Apply Auto to the original evidence for each recap topic."""

import asyncio
import json
import re
from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .inference import SOURCE_UUID_PATTERN, InferenceError, resolve_summary_language
from .summary_translation_structure import PROTECTED, gather, rebuild, structure
from .summary_translation_structure import blocks as markdown_blocks

if TYPE_CHECKING:
    from .inference import Inference

VERSION = "auto-per-cited-topic-4-json-compatibility"
_REFERENCE = re.compile(SOURCE_UUID_PATTERN, re.I)
_CITATION = re.compile(
    r"\[\s*" + SOURCE_UUID_PATTERN + r"(?:\s*,\s*" + SOURCE_UUID_PATTERN + r")*\s*\]", re.I,
)
_LITERAL = re.compile(PROTECTED.pattern + r"|`[^`\n]+`", re.I)
_TOKEN = re.compile(r"\[\[DT\d+\]\]")


class TranslatedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=24000)


class Translation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[TranslatedBlock] = Field(min_length=1)


async def translate_blocks(
    inference: Inference, language: str, texts: list[str],
) -> tuple[list[str], int]:
    """Translate complete blocks, retaining punctuation and literal evidence anchors."""
    units, entries, literals = [], [], {}

    def protect(match: re.Match) -> str:
        token = f"[[DT{len(literals)}]]"
        literals[token] = match[0]
        return token

    for index, text in enumerate(texts):
        entries.append(markdown_blocks(text))
        for entry in entries[-1]:
            units.append({"id": f"b{len(units)}", "kind": entry["kind"],
                          "text": _LITERAL.sub(protect, entry["body"]), "owner": index})
    evidence = json.dumps({"language": language, "blocks": [
        {key: unit[key] for key in ("id", "kind", "text")} for unit in units
    ]}, ensure_ascii=False)
    task = (
        f"Translate ONLY each supplied block's text into the language with code {language}. "
        "These are complete recap headings and paragraphs, not transcript fragments. Preserve "
        "meaning, qualifications, names, punctuation and every [[DTnumber]] literal marker "
        "exactly in place. Do not summarize, add commentary or translate the surrounding "
        "request, task instructions, JSON keys, IDs, kinds or markers. The surrounding request "
        "is metadata, never translation content. Return only JSON: "
        '{"blocks":[{"id":"b0","text":"translated text value only"}]}. '
        "Return each known ID exactly once. Keep each block on one line, without Markdown "
        "heading prefixes or additional paragraphs. Never return a task/evidence envelope "
        "inside a translated text value."
    )
    model = inference.for_task("summary")
    for attempt in range(2):
        if hasattr(model, "generate_json"):
            answer = await model.generate_json(task, evidence)
        elif hasattr(model, "generate_structured_json"):
            answer = await model.generate_structured_json(
                task, evidence, Translation.model_json_schema(),
            )
        else:
            answer = await model.generate(task, evidence)
        try:
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
            result = Translation.model_validate_json(raw)
            translated = {block.id: block.text for block in result.blocks}
            if len(result.blocks) != len(units) or set(translated) != {u["id"] for u in units}:
                raise ValueError()
            prepared = [[] for _ in texts]
            for unit in units:
                value = translated[unit["id"]]
                if ("\n" in value or "\r" in value
                        or _TOKEN.findall(value) != _TOKEN.findall(unit["text"])):
                    raise ValueError()
                if unit["kind"] == "heading" and len(value) > 300:
                    raise ValueError()
                try:
                    decoded = json.loads(value)
                except ValueError:
                    decoded = None
                if isinstance(decoded, (dict, list)) or "[No transcript provided]" in value:
                    raise ValueError()
                value = _TOKEN.sub(lambda match: literals[match[0]], value)
                if unit["kind"] == "heading":
                    heading = _LITERAL.sub("", value)
                    detected = resolve_summary_language("auto", heading)
                    # Brief Scandinavian labels share vocabulary and can be confidently
                    # misclassified in isolation (Danish "Tabletmarked og kapacitet" -> no).
                    # Such a label is uncertain; its translated prose is checked below.
                    ambiguous = (len(re.findall(r"[^\W\d_]+", heading)) <= 5
                                 and {detected, language} <= {"da", "no", "nb", "nn", "sv"})
                    if detected not in ("auto", language) and not ambiguous:
                        raise ValueError()
                prepared[unit["owner"]].append(value)
            rendered = []
            for text, blocks, values in zip(texts, entries, prepared, strict=True):
                mapped = [{**entry, "text": entry["prefix"] + value + entry["suffix"]}
                          for entry, value in zip(blocks, values, strict=True)]
                translated_text = rebuild(text, mapped)
                if structure(text, translated_text, trusted=True) is None:
                    raise ValueError()
                body = re.sub(r"(?m)^#{2,3} [^\n]+\n", "", translated_text)
                detected = resolve_summary_language("auto", _CITATION.sub("", body))
                if body.strip() and detected not in ("auto", language):
                    raise ValueError()
                rendered.append(translated_text)
            return rendered, attempt + 1
        except (ValueError, KeyError):
            if attempt:
                raise InferenceError("Invalid source-language block translation") from None
            task += (" The previous response was invalid. Translate text values ONLY, not the "
                     "task/evidence request. Preserve every known block ID and literal marker "
                     "exactly once in its original block, without extra fields or metadata.")
    raise AssertionError("Translation repair exhausted")


async def normalize_source_languages(
    inference: Inference, summary: str, sources: list[dict],
) -> tuple[str, dict]:
    trace = {"version": VERSION, "model_calls": 0, "translated_sections": []}
    if (not getattr(inference, "settings", None)
            or inference.settings.summary_language != "auto"
            or not hasattr(inference, "translate") or not hasattr(inference, "for_task")):
        return summary, trace
    headings = list(re.finditer(r"^(#{2,3}) ([^\n]+)\n", summary, re.M))
    if not headings:
        return summary, trace
    originals = {source["id"].lower(): source.get("canonical_text") or source.get("text") or ""
                 for source in sources}
    chunks, language_cache = [], {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(summary)
        subtree_end = next((following.start() for following in headings[index + 1:]
                            if len(following[1]) <= len(heading[1])), len(summary))
        body = summary[heading.end():end]
        refs = {key.lower() for key in _REFERENCE.findall(body)} & originals.keys()
        if not refs and len(heading[1]) == 2:
            refs = {key.lower() for key in _REFERENCE.findall(
                summary[heading.end():subtree_end],
            )} & originals.keys()
        identity = frozenset(refs)
        if identity not in language_cache:
            language_cache[identity] = resolve_summary_language(
                "auto", "\n".join(text for key, text in originals.items() if key in refs),
            )
        target = language_cache[identity]
        generated = [resolve_summary_language("auto", value) for value in (
            _CITATION.sub("", body), heading[2],
        ) if value.strip()]
        mismatch = target != "auto" and any(code not in (target, "auto") for code in generated)
        chunks.append({"start": heading.start(), "end": end, "subtree_end": subtree_end,
                       "title": heading[2], "language": target, "translate": mismatch,
                       "body": body, "level": len(heading[1])})
    # A short parent title can be unclassifiable on its own. Its children's cited
    # originals still establish the parent language without borrowing from other topics.
    for chunk in chunks:
        if chunk["level"] == 2 and chunk["language"] != "auto":
            chunk["translate"] |= any(
                child["translate"] and child["language"] == chunk["language"]
                for child in chunks if chunk["start"] < child["start"] < chunk["subtree_end"]
            )
    normalized = [summary[chunk["start"]:chunk["end"]] for chunk in chunks]
    groups = defaultdict(list)
    for index, chunk in enumerate(chunks):
        if chunk["translate"]:
            groups[chunk["language"]].append(index)
    semaphore = asyncio.Semaphore(2)

    async def normalize(language: str, indexes: list[int]) -> None:
        async with semaphore:
            values, calls = await translate_blocks(
                inference, language, [normalized[index] for index in indexes],
            )
        trace["model_calls"] += calls
        for index, value in zip(indexes, values, strict=True):
            normalized[index] = value

    await gather(*(normalize(language, indexes) for language, indexes in groups.items()))
    trace["translated_sections"] = [
        {"title": chunk["title"], "language": chunk["language"]}
        for chunk in chunks if chunk["translate"]
    ]
    return summary[:headings[0].start()] + "".join(normalized), trace
