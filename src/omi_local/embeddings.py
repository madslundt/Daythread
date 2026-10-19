"""Optional explicit local embedding service and an independent durable indexing worker."""

import asyncio
import json
import logging
from collections import OrderedDict
from time import monotonic

import httpx

from .domain import stamp, utc_now
from .semantic_store import normalized
from .settings import Settings
from .store import Store

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Invalid or unavailable embeddings; caller must use keyword fallback."""


class Embeddings:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client
        self.health = {"state": "unknown" if settings.embedding_enabled else "disabled"}
        self._queries = OrderedDict()
        self._pending = {}
        self._waiters = {}
        self._retry_at = {}

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not self.settings.embedding_enabled:
            raise EmbeddingError("Embeddings disabled")
        if not query:
            return await self._embed(texts, query=False)
        # Only query vectors are cached. Documents and retrieval results always read current
        # source versions. Include the complete model contract so vectors cannot cross models.
        identity = json.dumps(self.settings.embedding_manifest, sort_keys=True)
        key = (identity, tuple(texts))
        now = monotonic()
        cached = self._queries.get(key)
        if cached and cached[0] > now:
            self._queries.move_to_end(key)
            return [list(vector) for vector in cached[1]]
        self._queries.pop(key, None)
        if self._retry_at.get(identity, 0) > now:
            raise EmbeddingError("Query embeddings are cooling down after an outage")
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._embed_query(texts, key, identity))
            self._pending[key] = task
        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            return [list(vector) for vector in await asyncio.shield(task)]
        finally:
            self._waiters[key] -= 1
            if not self._waiters[key]:
                self._waiters.pop(key)
                self._pending.pop(key, None)
                if not task.done():
                    task.cancel()
                    # Close the underlying HTTP stream before releasing the last consumer.
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def _embed_query(self, texts, key, identity):
        try:
            async with asyncio.timeout(self.settings.embedding_query_timeout_seconds):
                vectors = await self._embed(texts, query=True)
        except (EmbeddingError, TimeoutError) as exc:
            self.health = {"state": "unavailable", "checked_at": stamp(utc_now())}
            self._retry_at = {identity: monotonic() + self.settings.embedding_query_retry_seconds}
            raise EmbeddingError("Query embeddings unavailable") from exc
        self._retry_at.pop(identity, None)
        size = self.settings.embedding_query_cache_size
        ttl = self.settings.embedding_query_cache_seconds
        if size and ttl:
            self._queries[key] = (monotonic() + ttl, tuple(tuple(v) for v in vectors))
            self._queries.move_to_end(key)
            while len(self._queries) > size:
                self._queries.popitem(last=False)
        return vectors

    async def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        prefix = (
            self.settings.embedding_query_prefix
            if query
            else self.settings.embedding_document_prefix
        )
        inputs = [prefix + text for text in texts]
        try:
            if (
                not inputs
                or len(inputs) > 16
                or any(len(text) > self.settings.embedding_max_input_chars for text in inputs)
            ):
                raise ValueError("Embedding input exceeds configured contract")
            token = self.settings.embedding_token.get_secret_value()
            async with asyncio.timeout(self.settings.embedding_timeout_seconds):
                async with self.client.stream(
                    "POST",
                    f"{self.settings.embedding_base_url}/embeddings",
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    json={
                        "model": self.settings.embedding_model,
                        "input": inputs,
                        "encoding_format": "float",
                    },
                    timeout=self.settings.embedding_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 8_000_000:
                            raise ValueError("Embedding response exceeds budget")
            data = json.loads(body)["data"]
            if not isinstance(data, list) or len(data) != len(inputs):
                raise ValueError("Invalid embedding count")
            ordered = {}
            for item in data:
                index = item["index"]
                if type(index) is not int or not 0 <= index < len(inputs) or index in ordered:
                    raise ValueError("Invalid embedding index")
                ordered[index] = normalized(item["embedding"], self.settings.embedding_dimension)
            result = [ordered[index] for index in range(len(inputs))]
        except (
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
        ) as exc:
            self.health = {"state": "unavailable", "checked_at": stamp(utc_now())}
            raise EmbeddingError("Local embeddings unavailable or invalid") from exc
        self.health = {"state": "ready", "checked_at": stamp(utc_now())}
        return result


async def index_one(
    store: Store, embeddings: Embeddings, set_id: str, retain_routes: set[str] | None = None,
) -> bool:
    if not embeddings.settings.embedding_enabled:
        return False
    job = await asyncio.to_thread(
        store.claim_embedding_batch,
        set_id,
        embeddings.settings.embedding_passage_chars,
    )
    if not job:
        await asyncio.to_thread(
            store.prune_inactive_embeddings_if_ready, set_id, retain_routes
        )
        return False
    if not embeddings.settings.embedding_enabled:
        await asyncio.to_thread(store.retry_embedding, job, paused=True)
        return False
    try:
        texts = [
            job["text"][passage["start_offset"] : passage["end_offset"]]
            for passage in job["passages"]
        ]
        vectors = await embeddings.embed(texts)
        finished = await asyncio.to_thread(store.finish_embedding_batch, job, vectors)
        if finished:
            await asyncio.to_thread(
                store.prune_inactive_embeddings_if_ready, set_id, retain_routes
            )
    except asyncio.CancelledError:
        await asyncio.to_thread(store.retry_embedding, job)
        raise
    except Exception as exc:
        if not embeddings.settings.embedding_enabled:
            await asyncio.to_thread(store.retry_embedding, job, paused=True)
        else:
            logger.warning("Embedding indexing will retry (%s)", type(exc).__name__)
            await asyncio.to_thread(store.retry_embedding, job)
    return True


async def run_embedding_worker(
    store: Store, embeddings: Embeddings, set_id: str,
    retain_routes: set[str] | None = None,
) -> None:
    while True:
        try:
            worked = await index_one(store, embeddings, set_id, retain_routes)
        except Exception as exc:
            # Do not log model payloads, text, credentials or provider bodies.
            logger.warning("Embedding worker will retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.1 if worked else 2)


async def run_embedding_workers(store: Store, adapters: dict, set_ids: dict) -> None:
    """Backfill the default and language-specific indexes independently."""
    async with asyncio.TaskGroup() as group:
        for route, set_id in set_ids.items():
            if set_id and route in adapters:
                group.create_task(run_embedding_worker(
                    store, adapters[route], set_id, set(set_ids)
                ))
