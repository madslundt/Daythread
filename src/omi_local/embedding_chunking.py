"""Deterministic source spans, with offline token counting and no model downloads."""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

SENTENCE_END = re.compile(r"[.!?。！？](?:[\"'”’)]*)(?:\s+|$)|\n+")


@lru_cache(maxsize=8)
def _local_tokenizer(path: str, revision: str, modified: int, size: int):
    # Only an explicitly supplied local file is opened. No Hub/network fallback exists.
    from tokenizers import Tokenizer

    data = Path(path).read_bytes()
    try:
        tokenizer = Tokenizer.from_str(data.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid local embedding tokenizer") from exc
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer, hashlib.sha256(data).hexdigest()


def tokenizer_for(manifest: dict):
    name = manifest.get("tokenizer", "utf8")
    if name == "utf8":
        return None, "utf8-byte-bound-v1"
    path = Path(name)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Embedding tokenizer must be an existing absolute local file")
    stat = path.stat()
    return _local_tokenizer(
        str(path), manifest.get("tokenizer_revision", ""), stat.st_mtime_ns, stat.st_size
    )


def token_count(text: str, manifest: dict) -> int:
    tokenizer, _ = tokenizer_for(manifest)
    if tokenizer is None:
        # A provable upper bound for byte-level BPE models (including Qwen3), rather
        # than an English characters-per-token heuristic. Other models need their tokenizer.
        return len(text.encode("utf-8"))
    try:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)
    except Exception as exc:
        raise ValueError("Embedding tokenization failed") from exc


def input_fits(text: str, manifest: dict) -> bool:
    return (
        len(text) <= manifest.get("max_input_chars", 4000)
        and token_count(text, manifest) + manifest.get("special_tokens", 8)
        <= manifest.get("max_input_tokens", 8192)
    )


def topic_regions(text: str, topics: list[dict] | None) -> list[tuple[int, int, int | None]]:
    """Only ordered, in-range offsets are used; uncovered gaps stay searchable."""
    if not isinstance(topics, list):
        return [(0, len(text), None)]
    result, cursor = [], 0
    for index, topic in enumerate(topics):
        if not isinstance(topic, dict):
            return [(0, len(text), None)]
        start, end = topic.get("start_offset"), topic.get("end_offset")
        if type(start) is not int or type(end) is not int or not cursor <= start < end <= len(text):
            return [(0, len(text), None)]
        if start > cursor:
            result.append((cursor, start, None))
        result.append((start, end, index))
        cursor = end
    if cursor < len(text):
        result.append((cursor, len(text), None))
    return result


def _fitting_end(text: str, start: int, end: int, fits) -> int:
    low, high = start, end
    while low < high:
        middle = (low + high + 1) // 2
        if fits(text[start:middle]):
            low = middle
        else:
            high = middle - 1
    # Token counts need not be monotonic; the selected span itself must fit.
    while low > start and not fits(text[start:low]):
        low -= 1
    return low


def chunk_passages(text: str, manifest: dict, topics: list[dict] | None = None) -> list[dict]:
    strategy = manifest.get("chunking_strategy", "characters-overlap-v1")
    if strategy == "characters-overlap-v1":
        from .semantic_store import passages

        return [dict(start_offset=start, end_offset=end, topic_id=None)
                for start, end in passages(text, manifest.get("passage_chars", 1000))]
    prefix = manifest.get("document_prefix", "")
    maximum = manifest.get("max_tokens", 512)
    target = min(manifest.get("target_tokens", 320), maximum)
    overlap = manifest.get("overlap_tokens", 48)
    if not 0 <= overlap < target <= maximum:
        raise ValueError("Invalid embedding chunk token bounds")

    def fits(value: str) -> bool:
        return token_count(value, manifest) <= maximum and input_fits(prefix + value, manifest)

    regions = topic_regions(text, topics if strategy == "topic-token-v1" else None)
    result = []
    for first, last, topic_id in regions:
        start = first
        while start < last:
            hard_end = _fitting_end(
                text, start, min(last, start + manifest.get("max_input_chars", 4000)), fits
            )
            if hard_end <= start:
                raise ValueError("Embedding prefix or single character exceeds token budget")
            if hard_end == last:
                end = last
            else:
                preferred = _fitting_end(
                    text, start, hard_end,
                    lambda value: token_count(value, manifest) <= target and fits(value),
                )
                boundaries = [start + match.end()
                              for match in SENTENCE_END.finditer(text[start:hard_end])]
                coherent = [point for point in boundaries
                            if token_count(text[start:point], manifest) >= target // 2
                            and fits(text[start:point])]
                end = min(coherent, key=lambda point: abs(
                    token_count(text[start:point], manifest) - target
                )) if coherent else max(start + 1, preferred)
            if text[start:end].strip():
                result.append(dict(start_offset=start, end_offset=end, topic_id=topic_id))
            if end == last:
                break
            if overlap:
                # Find a bounded suffix without decoding/re-encoding source offsets.
                low, high = start + 1, end
                while low < high:
                    middle = (low + high) // 2
                    if token_count(text[middle:end], manifest) <= overlap:
                        high = middle
                    else:
                        low = middle + 1
                start = low
            else:
                start = end
    return result
