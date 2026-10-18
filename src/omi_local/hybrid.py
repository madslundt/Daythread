"""Scoped candidate fusion and bounded excerpts, independent of answer generation."""

import asyncio
import re
import unicodedata
from datetime import datetime
from typing import Literal

from .embeddings import EmbeddingError, Embeddings
from .settings import Settings
from .store import Store


def normalized_words(text: str) -> set[str]:
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    return set(re.findall(r"\w+", normalized))


def relevant_excerpts(row: dict, query: str, semantic_match: dict | None) -> list[dict]:
    """Prefer dense query coverage, retaining distant non-overlapping supporting excerpts."""
    candidates = [
        excerpt(row["text"], query, max(0, offset - 200), min(len(row["text"]), offset + 800))
        for offset in row.get(
            "lexical_offsets", [row["lexical_offset"]] if "lexical_offset" in row else []
        )
    ]
    if semantic_match:
        candidates.append(semantic_match)
    if not candidates:
        return [excerpt(row["text"] or "", query)]
    words = normalized_words(query)
    candidates.sort(
        key=lambda match: (-len(normalized_words(match["text"]) & words), match["start_offset"])
    )
    selected, covered = [], set()
    for match in candidates:
        terms = normalized_words(match["text"]) & words
        if any(
            match["start_offset"] < previous["end_offset"]
            and match["end_offset"] > previous["start_offset"]
            for previous in selected
        ):
            continue
        if selected and not terms - covered and match is not semantic_match:
            continue
        selected.append(match)
        covered |= terms
        if len(selected) == 3:
            break
    return selected


def excerpt(text: str, query: str, start: int | None = None, end: int | None = None) -> dict:
    if start is None:
        words = re.findall(r"\w+", query)
        match = (
            re.search(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", text, re.I)
            if words
            else None
        )
        start = max(0, match.start() - 200) if match else 0
        end = min(len(text), start + 1000)
    start = max(0, start - 150)
    end = min(len(text), end + 150)
    return {
        "text": text[start:end],
        "start_offset": start,
        "end_offset": end,
        "timing": "chunk",  # No false timestamp precision for corrected/imported transcripts.
    }


def bound_sources(
    sources: list[dict],
    query: str,
    limit: int,
    *,
    full_transcripts: bool = False,
) -> tuple[list[dict], bool]:
    selected, remaining, extras = [], 12000, []
    truncated = len(sources) > limit
    for source in sources[:limit]:
        if full_transcripts:
            selected.append(source)
            continue
        match = source.get("matches", [excerpt(source["text"] or "", query)])[0]
        extra = source.get("matches", [])[1:]
        if remaining <= 0:
            truncated = True
            break
        match = dict(match)
        if len(match["text"]) > remaining:
            match["text"] = match["text"][:remaining]
            match["end_offset"] = match["start_offset"] + len(match["text"])
            truncated = True
        source["matches"] = [match]
        extras.append((source, extra))
        remaining -= len(match["text"])
        truncated |= len(match["text"]) < len(source["text"] or "")
        selected.append(source)
    # Give ranked recordings their first excerpt before spending spare budget on
    # additional passages; distant matches never displace another recording's first hit.
    for source, matches in extras:
        for match in matches:
            if remaining <= 0:
                truncated = True
                break
            bounded = dict(match)
            bounded["text"] = bounded["text"][:remaining]
            bounded["end_offset"] = bounded["start_offset"] + len(bounded["text"])
            source["matches"].append(bounded)
            remaining -= len(bounded["text"])
    ids = {source["id"] for source in selected}
    # A derived diary must not smuggle omitted/out-of-scope dependencies into model evidence.
    for source in selected:
        if "diary_summaries" in source:
            source["diary_summaries"] = [
                diary
                for diary in source["diary_summaries"]
                if set(diary.get("source_ids", [])) <= ids
            ]
    return selected, truncated


class HybridRetriever:
    def __init__(self, store: Store, settings: Settings, embeddings: Embeddings | None = None):
        self.store, self.settings, self.embeddings = store, settings, embeddings
        self.set_id = (
            store.configure_embeddings(settings.embedding_manifest)
            if settings.background_processing_enabled and settings.embedding_enabled else None
        )

    def status(self, start: datetime | None = None, end: datetime | None = None) -> dict:
        if not self.set_id:
            return {"state": "disabled", "set_id": None, "total": 0, "indexed": 0}
        coverage = self.store.embedding_coverage(self.set_id, start, end)
        health = self.embeddings.health if self.embeddings else {"state": "unavailable"}
        state = health["state"]
        if state == "ready" and coverage["indexed"] < coverage["total"]:
            state = "partial"
        return coverage | {"state": state, "set_id": self.set_id}

    async def retrieve(
        self,
        question: str,
        query: str,
        limit: int,
        start: datetime | None,
        end: datetime | None,
        mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
    ) -> tuple[list[dict], dict]:
        details = {"mode": "keyword", "fallback_reason": None}

        async def lexical_candidates():
            return (
                await asyncio.to_thread(self.store.search, query, 50, start, end)
                if mode != "semantic" or not self.set_id
                else []
            )

        async def semantic_candidates():
            if not self.set_id or mode == "keyword":
                return []
            try:
                if self.embeddings is None:
                    raise EmbeddingError("No adapter")
                vector = (await self.embeddings.embed([question], query=True))[0]
                semantic = await asyncio.to_thread(
                    self.store.semantic_candidates,
                    self.set_id,
                    vector,
                    self.settings.embedding_min_score,
                    50,
                    start,
                    end,
                )
                details["mode"] = "semantic" if mode == "semantic" else "hybrid"
                return semantic
            except EmbeddingError:
                details["fallback_reason"] = "embedding_unavailable"
                return []

        async with asyncio.TaskGroup() as group:
            lexical_task = group.create_task(lexical_candidates())
            semantic_task = group.create_task(semantic_candidates())
        lexical, semantic = lexical_task.result(), semantic_task.result()
        if mode == "semantic" and details["fallback_reason"]:
            lexical = await asyncio.to_thread(self.store.search, query, 50, start, end)
        status = await asyncio.to_thread(self.status, start, end)
        details["embedding"] = status
        if self.set_id and status["indexed"] < status["total"]:
            details["partial"] = True
        rows = {row["id"]: row for row in lexical}
        scores, matches = {}, {}
        for rank, row in enumerate(lexical, 1):
            scores[row["id"]] = 1 / (60 + rank)
        semantic_rows = (
            await asyncio.to_thread(
                self.store.get_audio_many, [passage["audio_id"] for passage in semantic]
            )
            if semantic
            else {}
        )
        for rank, passage in enumerate(semantic, 1):
            key = passage["audio_id"]
            row = semantic_rows.get(key)
            if not row or row["transcript_version"] != passage["version"] or row["state"] != "done":
                continue
            previous = rows.get(key)
            if previous and previous["transcript_version"] == row["transcript_version"]:
                row = previous  # Preserve FTS-normalized match offsets for lexical evidence.
            rows[key] = row
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            matches[key] = excerpt(
                row["text"],
                query,
                passage["start_offset"],
                passage["end_offset"],
            )
        # Reserve strongest literal matches, then fill with fused ranks.
        ranked = sorted(scores, key=lambda key: (-scores[key], rows[key]["started_at"], key))
        protected = [row["id"] for row in lexical[: min(2, limit)]]
        keys = list(dict.fromkeys(protected + ranked))[:limit]
        result = []
        for key in keys:
            row = rows[key]
            row["matches"] = relevant_excerpts(row, query, matches.get(key))
            result.append(row)
        # Ranked hits keep all their slots. Neighbors fill only spare capacity, never recurse.
        included = set(keys)
        context = []
        for row in list(result):
            if len(result) >= limit:
                break
            neighbors = await asyncio.to_thread(self.store.contiguous_context, row, start, end)
            for neighbor in neighbors:
                if len(result) >= limit:
                    break
                if neighbor["id"] in included:
                    continue
                neighbor["matches"] = [excerpt(neighbor["text"], "")]
                neighbor["context_for"] = row["id"]
                result.append(neighbor)
                included.add(neighbor["id"])
                context.append(neighbor["id"])
        details["context_sources"] = context
        return result, details
