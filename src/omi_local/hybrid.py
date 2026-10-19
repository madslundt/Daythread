"""Scoped candidate fusion and bounded excerpts, independent of answer generation."""

import asyncio
import json
import re
import unicodedata
from datetime import datetime
from typing import Literal

from .embedding_chunking import topic_regions
from .embeddings import EmbeddingError, Embeddings
from .reranking import Reranker, RerankingError
from .semantic_metadata import json_object, prepare_source_provenance, refresh_passage_metadata
from .settings import Settings
from .store import Store
from .translation import analyze_transcript_languages, detect_language_code


def normalized_words(text: str) -> set[str]:
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    return set(re.findall(r"\w+", normalized))


def relevant_excerpts(
    row: dict, query: str, semantic_match: dict | list[dict] | None, limit: int = 3,
) -> list[dict]:
    """Prefer dense query coverage, retaining distant non-overlapping supporting excerpts."""
    candidates = []
    regions = topic_regions(
        row["text"], json_object(row.get("topic_details") or "{}").get("sections"),
    )
    for offset in row.get(
        "lexical_offsets", [row["lexical_offset"]] if "lexical_offset" in row else [],
    ):
        lower, upper = next(
            ((start, end) for start, end, _ in regions if start <= offset < end),
            (0, len(row["text"])),
        )
        candidates.append(excerpt(
            row["text"], query, max(lower, offset - 200), min(upper, offset + 800),
            lower=lower, upper=upper,
        ))
    semantic_matches = (
        semantic_match if isinstance(semantic_match, list)
        else [semantic_match] if semantic_match else []
    )
    candidates.extend(semantic_matches)
    if not candidates:
        return [excerpt(row["text"] or "", query)]
    words = normalized_words(query)
    # Stable ties retain semantic rank; chronological sorting would bury a strong
    # cross-language match behind unrelated passages that share no query tokens.
    candidates.sort(key=lambda match: -len(normalized_words(match["text"]) & words))
    selected, covered = [], set()
    for match in candidates:
        terms = normalized_words(match["text"]) & words
        if any(
            match["start_offset"] < previous["end_offset"]
            and match["end_offset"] > previous["start_offset"]
            for previous in selected
        ):
            continue
        if selected and not terms - covered and not any(match is m for m in semantic_matches):
            continue
        selected.append(match)
        covered |= terms
        if len(selected) == limit:
            break
    return selected


def excerpt(
    text: str, query: str, start: int | None = None, end: int | None = None,
    *, lower: int = 0, upper: int | None = None,
) -> dict:
    upper = len(text) if upper is None else min(len(text), upper)
    if start is None:
        words = re.findall(r"\w+", query)
        match = (
            re.search(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", text, re.I)
            if words
            else None
        )
        start = max(0, match.start() - 200) if match else 0
        end = min(len(text), start + 1000)
    start = max(lower, start - 150)
    end = min(upper, end + 150)
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
    character_budget: int = 12000,
) -> tuple[list[dict], bool]:
    selected, remaining, extras = [], character_budget, []
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
            truncated |= len(bounded["text"]) > remaining
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


def passage_excerpt(
    row: dict, passage: dict, query: str, provenance: dict | None = None,
) -> dict | None:
    """Expand only inside this source and its validated topic, preserving passage metadata."""
    start, end = passage.get("start_offset"), passage.get("end_offset")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(row["text"]):
        return None
    metadata = passage.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    # Topic details and segment timing may change while replacement vectors are
    # being prepared. Resolve those mutable references against this live source.
    metadata = refresh_passage_metadata(row, passage | {"metadata": metadata}, provenance)
    lower, upper = metadata.get("topic_start_offset"), metadata.get("topic_end_offset")
    if not (
        type(lower) is int and type(upper) is int
        and 0 <= lower <= start < end <= upper <= len(row["text"])
    ):
        lower, upper = 0, len(row["text"])
    result = excerpt(row["text"], query, start, end, lower=lower, upper=upper)
    result["passage_id"] = passage["id"]
    result["transcript_version"] = passage["version"]
    result["passage_start_offset"], result["passage_end_offset"] = start, end
    result["metadata"] = metadata
    return result


def verified_context(source: dict, neighbor: dict) -> bool:
    """Capture adjacency alone is not a conversation or topic boundary."""
    def edge_topic(row, beginning):
        sections = json_object(row.get("topic_details") or "{}").get("sections")
        regions = topic_regions(row["text"], sections)
        if not regions:
            return set()
        _, _, index = regions[0 if beginning else -1]
        if index is not None:
            title = sections[index].get("title", "")
            return normalized_words(title) if isinstance(title, str) else set()
        return set()

    before = source["started_at"] <= neighbor["started_at"]
    left_topic = edge_topic(source, not before)
    right_topic = edge_topic(neighbor, before)
    if left_topic and right_topic and left_topic != right_topic:
        return False
    try:
        a = json.loads(source.get("source_metadata") or "{}")
        b = json.loads(neighbor.get("source_metadata") or "{}")
    except (ValueError, TypeError):
        return False
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if a.get("recording_id") and a.get("recording_id") == b.get("recording_id"):
        return True
    left, right = a.get("conversation_source_id"), b.get("conversation_source_id")
    return bool(
        (left and left == right) or left == neighbor["id"] or right == source["id"]
    )


class HybridRetriever:
    def __init__(self, store: Store, settings: Settings, embeddings: Embeddings | None = None):
        self.store, self.settings, self.embeddings = store, settings, embeddings
        self.reranker: Reranker | None = None
        self.route_set_ids = {}
        if settings.background_processing_enabled and settings.embedding_enabled:
            self.set_id = store.configure_embeddings(settings.embedding_manifest)
            for language in settings.embedding_languages:
                self.route_set_ids[language] = store.configure_embeddings(
                    settings.embedding_manifest_for(language)
                )
            store.reconcile_embedding_routes({"default", *settings.embedding_languages})
        else:
            self.set_id = None
            # Disabling inference pauses retrieval/indexing; keeping the derived index
            # lets an unchanged contract resume without replacing the only good copy.

    def embedding_route(self, query: str) -> tuple[str, str | None]:
        analysis = analyze_transcript_languages(query)
        language = analysis["dominant_language"]
        if not language or analysis["dominant_language_share"] < 0.7:
            language = detect_language_code(query)
        if language:
            route = language.split("-")[0]
            if route in self.route_set_ids:
                return route, self.route_set_ids[route]
        return "default", self.set_id

    def status(self, start: datetime | None = None, end: datetime | None = None,
               route: str = "default") -> dict:
        set_id = self.set_id if route == "default" else self.route_set_ids.get(route)
        if not set_id:
            return {"state": "disabled", "set_id": None, "total": 0, "indexed": 0}
        coverage = self.store.embedding_coverage(set_id, start, end)
        adapter = (
            self.embeddings if route == "default"
            else getattr(self, "embedding_adapters", {}).get(route, self.embeddings)
        )
        health = adapter.health if adapter else {"state": "unavailable"}
        state = health["state"]
        if state == "ready" and coverage["indexed"] < coverage["total"]:
            state = "partial"
        return coverage | {"state": state, "set_id": set_id}

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
        candidate_limit = self.settings.retrieval_candidate_limit
        matches_per_source = self.settings.retrieval_matches_per_source
        if mode != "keyword":
            route, route_set_id = await asyncio.to_thread(self.embedding_route, question)
        else:
            route, route_set_id = "default", self.set_id

        async def lexical_candidates():
            return (
                await asyncio.to_thread(self.store.search, query, candidate_limit, start, end)
                if mode != "semantic" or not route_set_id
                else []
            )

        async def semantic_candidates():
            if not route_set_id or mode == "keyword":
                return []
            try:
                adapter = (
                    self.embeddings if route == "default"
                    else getattr(self, "embedding_adapters", {}).get(route, self.embeddings)
                )
                if adapter is None:
                    raise EmbeddingError("No adapter")
                vector = (await adapter.embed([question], query=True))[0]
                semantic = await asyncio.to_thread(
                    self.store.semantic_candidates,
                    route_set_id,
                    vector,
                    self.settings.embedding_min_score,
                    candidate_limit,
                    start,
                    end,
                    matches_per_source,
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
            lexical = await asyncio.to_thread(self.store.search, query, candidate_limit, start, end)
        status = await asyncio.to_thread(self.status, start, end, route)
        details["embedding"] = status
        if route_set_id and status["indexed"] < status["total"]:
            details["partial"] = True
        rows = {row["id"]: row for row in lexical}
        scores, matches = {}, {}
        rrf_k = self.settings.retrieval_rrf_k
        for rank, row in enumerate(lexical, 1):
            scores[row["id"]] = self.settings.retrieval_lexical_weight / (rrf_k + rank)
        semantic_rows = (
            await asyncio.to_thread(
                self.store.get_audio_many, list(dict.fromkeys(p["audio_id"] for p in semantic))
            )
            if semantic
            else {}
        )
        lexical_scores, provenance_by_source = dict(scores), {}
        for rank, passage in enumerate(semantic, 1):
            key = passage["audio_id"]
            row = semantic_rows.get(key)
            if not row or row["transcript_version"] != passage["version"] or row["state"] != "done":
                continue
            if key not in provenance_by_source:
                provenance_by_source[key] = prepare_source_provenance(row)
            match = passage_excerpt(row, passage, query, provenance_by_source[key])
            if match is None:
                continue
            previous = rows.get(key)
            if previous and previous["transcript_version"] == row["transcript_version"]:
                # Preserve only FTS-normalized offsets. Topic/timing metadata can change
                # without changing transcript text while candidate retrieval is running.
                row = row | {
                    field: previous[field]
                    for field in ("lexical_offset", "lexical_offsets") if field in previous
                }
            rows[key] = row
            # Fuse each passage independently. The source's strongest fused passage ranks
            # it; additional passages provide evidence without inflating long recordings.
            score = lexical_scores.get(key, 0) + (
                self.settings.retrieval_semantic_weight / (rrf_k + rank)
            )
            scores[key] = max(scores.get(key, 0), score)
            matches.setdefault(key, []).append(match)
        # Reserve strongest literal matches, then fill with fused ranks.
        ranked = sorted(scores, key=lambda key: (-scores[key], rows[key]["started_at"], key))
        for key in ranked:
            rows[key]["matches"] = relevant_excerpts(
                rows[key], query, matches.get(key), matches_per_source,
            )
        details["reranking"] = "disabled"
        if self.settings.reranker_enabled and ranked and mode == "hybrid":
            if self.reranker is None:
                details["reranking"] = "unavailable"
            else:
                # At least one candidate per recording precedes extra passages, preserving
                # source diversity when the reranker has a smaller candidate budget.
                candidates = [(key, rows[key]["matches"][0]) for key in ranked]
                candidates.extend(
                    (key, match) for key in ranked for match in rows[key]["matches"][1:]
                )
                candidates = candidates[:self.settings.reranker_max_candidates]
                try:
                    reranked = await self.reranker.scores(
                        question, [match["text"] for _, match in candidates],
                    )
                except RerankingError:
                    details["reranking"] = "unavailable"
                else:
                    source_scores, match_scores = {}, {}
                    for (key, match), score in zip(candidates, reranked, strict=True):
                        source_scores[key] = max(source_scores.get(key, -float("inf")), score)
                        match_scores[id(match)] = score
                    original_rank = {key: rank for rank, key in enumerate(ranked)}
                    ranked.sort(key=lambda key: (
                        key not in source_scores, -source_scores.get(key, 0), original_rank[key],
                    ))
                    for key in source_scores:
                        rows[key]["matches"].sort(key=lambda match: (
                            id(match) not in match_scores, -match_scores.get(id(match), 0),
                        ))
                    details["reranking"] = "ready"
                    details["reranked_passages"] = len(candidates)
        protected = [row["id"] for row in lexical[: min(2, limit)]]
        keys = list(dict.fromkeys(protected + ranked))[:limit]
        result = []
        for key in keys:
            row = rows[key]
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
                if neighbor["id"] in included or not verified_context(row, neighbor):
                    continue
                neighbor["matches"] = [excerpt(neighbor["text"], "")]
                neighbor["context_for"] = row["id"]
                result.append(neighbor)
                included.add(neighbor["id"])
                context.append(neighbor["id"])
        details["context_sources"] = context
        return result, details
