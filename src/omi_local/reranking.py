"""Optional bounded /rerank service; scores reorder transcript spans, never create evidence."""

import asyncio
import json
import math

import httpx

from .settings import Settings


class RerankingError(Exception):
    """The optional service failed; retain the fused candidate order."""


class Reranker:
    """Explicit generic rerank API contract, independent of any model's native scoring format."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def scores(self, query: str, documents: list[str]) -> list[float]:
        settings = self.settings
        try:
            if (
                not settings.reranker_enabled
                or not query.strip()
                or len(query) > settings.reranker_max_query_chars
                or not documents
                or len(documents) > settings.reranker_max_candidates
                or any(not text.strip() for text in documents)
            ):
                raise ValueError("Invalid reranking input")
            token = settings.reranker_token.get_secret_value()
            async with asyncio.timeout(settings.reranker_timeout_seconds):
                async with self.client.stream(
                    "POST",
                    f"{settings.reranker_base_url}/rerank",
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    json={
                        "model": settings.reranker_model,
                        "query": query,
                        "documents": [
                            text[:settings.reranker_max_passage_chars] for text in documents
                        ],
                        "top_n": len(documents),
                        "return_documents": False,
                    },
                    timeout=settings.reranker_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 256_000:
                            raise ValueError("Reranking response exceeds budget")
            results = json.loads(body)["results"]
            if not isinstance(results, list) or len(results) != len(documents):
                raise ValueError("Invalid reranking result count")
            scores = {}
            for result in results:
                index, score = result["index"], result["relevance_score"]
                if (
                    type(index) is not int
                    or not 0 <= index < len(documents)
                    or index in scores
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                ):
                    raise ValueError("Invalid reranking result")
                scores[index] = float(score)
            return [scores[index] for index in range(len(documents))]
        except (
            httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, OverflowError,
        ) as exc:
            # Provider bodies, transcript text, query text and credentials never enter logs.
            raise RerankingError("Reranking unavailable or invalid") from exc
