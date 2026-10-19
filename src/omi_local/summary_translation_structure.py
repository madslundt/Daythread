"""Display-only translations retain canonical Markdown layout and evidence identity."""

import asyncio
import hashlib
import re

from .inference import InferenceError
from .translation import translation_chunks

VERSION = 1
UUID = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
PROTECTED = re.compile(
    rf"!?\[[^\]\n]*\]\([^\)\n]+\)|\[\s*(?:source:)?{UUID}(?:\s*,\s*{UUID})*\s*\]"
    rf"|https?://[^\s<>]+|\b{UUID}\b", re.I,
)
HEADING = re.compile(r"^(\s*#{1,6}\s+)(.*)$")
ITEM = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
BOLD = re.compile(r"^\*\*([^*]+)\*\*(\s*(?:[—–:-]\s*)?)(.*)$")
EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
INLINE = re.compile(PROTECTED.pattern + r"|`[^`\n]+`|" + EMPHASIS.pattern, re.I)


def references(text):
    result = []
    for value in PROTECTED.findall(text):
        link = re.fullmatch(r"(!?)\[[^\]\n]*\](\([^\)\n]+\))", value)
        result.append(link[1] + link[2] if link else value)
    return result


def blocks(text):
    result, offset = [], 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        if raw.strip():
            heading, item = HEADING.match(raw), ITEM.match(raw)
            bold_heading = re.fullmatch(r"(\s*\*\*)(.+?)(\*\*:?[ \t]*)", raw)
            if heading:
                kind, prefix, body, suffix = "heading", heading[1], heading[2], ""
            elif bold_heading:
                kind, prefix, body, suffix = "heading", *bold_heading.groups()
            elif item:
                kind, prefix, body, suffix = "item", item[1], item[2], ""
            else:
                prefix = re.match(r"[ \t]*(?:>[ \t]*)*", raw)[0]
                kind, body, suffix = "text", raw[len(prefix):], ""
            result.append({"index": len(result), "kind": kind, "start_offset": offset,
                           "end_offset": offset + len(raw), "original_text": raw,
                           "prefix": prefix, "body": body, "suffix": suffix})
        offset += len(line)
    return result


def rebuild(original, entries):
    result, offset = [], 0
    for entry in entries:
        result.extend((original[offset:entry["start_offset"]], entry["text"]))
        offset = entry["end_offset"]
    result.append(original[offset:])
    return "".join(result)


def structure(original, translated, *, trusted=False):
    """Legacy text is projectable only when its layout and evidence mapping are unambiguous."""
    source, target = blocks(original), blocks(translated)
    if not source or len(source) != len(target):
        return None
    citations = []
    for left, right in zip(source, target, strict=True):
        if (left["kind"], left["prefix"], left["suffix"]) != (
            right["kind"], right["prefix"], right["suffix"],
        ):
            return None
        if bool(BOLD.match(left["body"])) != bool(BOLD.match(right["body"])):
            return None
        before, after = references(left["body"]), references(right["body"])
        if before != after:
            return None
        if [m[1] for m in EMPHASIS.finditer(left["body"])] != [
            m[1] for m in EMPHASIS.finditer(right["body"])
        ]:
            return None
        if re.findall(r"`[^`\n]+`", left["body"]) != re.findall(r"`[^`\n]+`", right["body"]):
            return None
        if left["kind"] != "heading":
            citations.append(tuple(before))
    if not trusted and len(source) > 1:
        # Same-shaped uncited/repeated-anchor prose cannot prove that a legacy whole-text
        # translation retained topic order. New translations are mapped at inference time.
        if (not citations or any(not refs for refs in citations)
                or len(set(citations)) != len(citations)):
            return None
    entries = [{key: left[key] for key in (
        "index", "kind", "start_offset", "end_offset", "original_text",
    )} | {"text": right["original_text"]} for left, right in zip(source, target, strict=True)]
    return {"version": VERSION, "summary_version": hashlib.sha256(original.encode()).hexdigest(),
            "blocks": entries}


def projection(original, translated, saved=None):
    mapped = structure(original, translated, trusted=saved is not None)
    if saved is not None and mapped != saved:
        return None
    return mapped


def clean_unit(value, heading):
    if not isinstance(value, str) or not value.strip():
        raise InferenceError("Empty translation")
    value = value.strip()
    if heading:
        value = re.sub(r"^#{1,6}\s+", "", value)
        if value.startswith("**") and value.endswith("**"):
            value = value[2:-2].strip()
    if ("\n\n" in value or "\r" in value or INLINE.search(value)
            or any(HEADING.match(line) or ITEM.match(line) for line in value.splitlines())
            or BOLD.match(value) or re.fullmatch(r"\*\*.*\*\*:?:?", value)):
        raise InferenceError("Translation changed summary structure")
    return " ".join(value.split())


async def gather(*coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def translate_structure(inference, original, language):
    # A single overall timeout is owned by the caller. Three bounded calls can run together;
    # indexed gather preserves source order regardless of provider completion order.
    semaphore = asyncio.Semaphore(3)

    async def unit(text, heading=False):
        async def chunk(value):
            async with semaphore:
                translated = await inference.translate(value, language)
            return clean_unit(translated, heading)

        async def prose(value):
            if not value.strip():
                return value
            leading, trailing = re.match(r"\s*", value)[0], re.search(r"\s*$", value)[0]
            parts = await gather(*(
                chunk(part) for part in translation_chunks(value.strip())
            ))
            return leading + " ".join(parts) + trailing

        async def literal(value):
            emphasis = EMPHASIS.fullmatch(value)
            link = re.fullmatch(r"(!?)\[([^\]\n]*)\](\([^\)\n]+\))", value)
            if emphasis:
                return emphasis[1] + await unit(emphasis[2]) + emphasis[1]
            if link:
                return link[1] + "[" + await unit(link[2]) + "]" + link[3]
            return value

        pending, offset = [], 0
        for match in INLINE.finditer(text):
            pending.extend((prose(text[offset:match.start()]), literal(match[0])))
            offset = match.end()
        pending.append(prose(text[offset:]))
        return "".join(await gather(*pending))

    async def block(entry):
        labelled = BOLD.match(entry["body"])
        if labelled:
            title, body = await gather(unit(labelled[1], True), unit(labelled[3]))
            text = "**" + title + "**" + labelled[2] + body
        else:
            text = await unit(entry["body"], entry["kind"] == "heading")
        return {**entry, "text": entry["prefix"] + text + entry["suffix"]}

    entries = await gather(*(block(entry) for entry in blocks(original)))
    translated = rebuild(original, entries)
    mapped = structure(original, translated, trusted=True)
    if mapped is None:
        raise InferenceError("Translation changed summary structure")
    return translated, mapped
