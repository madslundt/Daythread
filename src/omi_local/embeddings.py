"""Optional explicit local embedding service and an independent durable indexing worker."""

import asyncio
import json
import logging

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

    async def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not self.settings.embedding_enabled:
            raise EmbeddingError("Embeddings disabled")
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


async def index_one(store: Store, embeddings: Embeddings, set_id: str) -> bool:
    job = await asyncio.to_thread(
        store.claim_embedding_batch,
        set_id,
        embeddings.settings.embedding_passage_chars,
    )
    if not job:
        return False
    try:
        texts = [
            job["text"][passage["start_offset"] : passage["end_offset"]]
            for passage in job["passages"]
        ]
        vectors = await embeddings.embed(texts)
        await asyncio.to_thread(store.finish_embedding_batch, job, vectors)
    except asyncio.CancelledError:
        await asyncio.to_thread(store.retry_embedding, job)
        raise
    except Exception as exc:
        logger.warning("Embedding indexing will retry (%s)", type(exc).__name__)
        await asyncio.to_thread(store.retry_embedding, job)
    return True


async def run_embedding_worker(store: Store, embeddings: Embeddings, set_id: str) -> None:
    while True:
        try:
            worked = await index_one(store, embeddings, set_id)
        except Exception as exc:
            # Do not log model payloads, text, credentials or provider bodies.
            logger.warning("Embedding worker will retry (%s)", type(exc).__name__)
            worked = False
        await asyncio.sleep(0.1 if worked else 2)
