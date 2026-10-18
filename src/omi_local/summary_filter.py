"""Deterministic summary request copies; archived transcripts stay authoritative."""

import re

VERSION = "hesitations-en-da-v1"
NO_CONTENT = "[[NO_RECAP_CONTENT]]"

# Only unmistakable hesitation sounds. Ordinary words (er, like, well, altså),
# reactions (hmm, oh, ah), repetitions and short answers can carry meaning.
# Protect quoted literals, code, links and email addresses before matching sounds.
_TOKENS = re.compile(
    r'(?P<literal>```[\s\S]*?(?:```|\Z)|`[^`\n]*(?:`|$)'
    r'|"(?:\\.|[^"\\])*(?:"|\Z)|“[^”]*(?:”|\Z)|‘[^’]*(?:’|\Z)'
    r"|(?<!\w)'[^'\n]*'(?!\w)"
    r'|https?://\S+|www\.\S+|(?<!\S)[^\s@]+@[^\s@]+)'
    r'|(?P<filler>(?<![\w@./\x27’+\-])(?:u+m+|u+h+m*|e+h+m+|ø+h+m*)'
    r'[,;:.…]*(?=$|\s|[!?)}\]]))'
    # Whole annotation lines only; literal matching above also protects quoted annotations.
    r'|(?P<annotation>^[ \t]*[\[(](?:silence|noise|background noise|music|inaudible|'
    r'unintelligible|stilhed|støj|baggrundsstøj|musik|uhørligt)[\])][ \t]*(?:\r?\n|$))',
    re.IGNORECASE | re.MULTILINE,
)


def clean_summary_text(text: str) -> tuple[str, int]:
    """Remove explicit non-speech lines and unquoted English/Danish hesitations.

    This is lexical cleanup, not semantic compression. Never guess which normal
    words, sentence fragments, speaker turns or repeated facts are unimportant.
    Quotes retain their exact wording for fidelity to the original recording.
    """
    removed, cursor = 0, 0
    pieces, plain = [], []
    changed = False

    def flush() -> None:
        # Only normalize outside literals, preserving their exact whitespace too.
        value = re.sub(r'[^\S\n]+', ' ', "".join(plain))
        value = re.sub(r' *\n *', '\n', value)
        pieces.append(re.sub(r' +([,.;:!?])', r'\1', value))
        plain.clear()

    for match in _TOKENS.finditer(text):
        plain.append(text[cursor:match.start()])
        if match.group("literal") is not None:
            flush()
            pieces.append(match.group())
        else:
            sound = match.group("filler")
            word = sound.rstrip(",;:.…") if sound else ""
            # Short uppercase tokens may be initials/acronyms; ASCII um after a
            # quantity may mean micrometres. Keep ambiguous uses conservatively.
            ambiguous = word and (
                (word.isupper() and len(word) <= 3)
                or (word.lower() == "um" and re.search(
                    r'\d\s*$', text[max(0, match.start() - 64):match.start()],
                ))
            )
            if ambiguous:
                plain.append(match.group())
            else:
                changed = True
                removed += sound is not None
        cursor = match.end()
    if not changed:
        return text, 0
    plain.append(text[cursor:])
    flush()
    cleaned = "".join(pieces).strip()
    if not cleaned.strip(" \t\r\n,;:.…!?()[]{}-–—"):
        cleaned = ""
    return cleaned, removed


def filter_summary_sources(sources: list[dict]) -> tuple[list[dict], dict]:
    """Copy sources for the prompt and expose counts without storing transcript text."""
    retained = []
    original_chars = retained_chars = removed_fillers = 0
    for source in sources:
        original = source["text"] or ""
        text, removed = clean_summary_text(original)
        original_chars += len(original)
        removed_fillers += removed
        if text.strip():
            retained_chars += len(text)
            retained.append({**source, "text": text})
    return retained, {
        "version": VERSION, "original_characters": original_chars,
        "retained_characters": retained_chars, "removed_fillers": removed_fillers,
        "omitted_sources": len(sources) - len(retained),
    }
