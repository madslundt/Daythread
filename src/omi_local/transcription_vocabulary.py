"""Bounded, explicitly enabled spelling hints for compatible STT endpoints."""

import json
import unicodedata

MAX_TERMS = 100
MAX_TERM_CHARS = 80
MAX_TOTAL_CHARS = 2000


def normalize_vocabulary(value) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_TERMS:
        raise ValueError("Use a list of up to 100 transcription vocabulary terms")
    terms, seen = [], set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("Transcription vocabulary terms must be text")
        term = " ".join(unicodedata.normalize("NFC", raw).split())
        if any(unicodedata.category(char).startswith("C") for char in term):
            raise ValueError("Transcription vocabulary terms cannot contain control characters")
        if len(term) > MAX_TERM_CHARS:
            raise ValueError("Each transcription vocabulary term must be at most 80 characters")
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            terms.append(term)
    if sum(map(len, terms)) > MAX_TOTAL_CHARS:
        raise ValueError("Transcription vocabulary must be at most 2000 characters in total")
    return terms


def vocabulary_fields(mode: str, terms: list[str]) -> dict[str, str]:
    if not terms or mode == "off":
        return {}
    if mode == "prompt":
        return {"prompt": ", ".join(terms)}
    if mode == "keyterms":
        return {"keyterms": json.dumps(terms, ensure_ascii=False)}
    raise ValueError("Unsupported transcription vocabulary mode")
