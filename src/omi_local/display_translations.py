"""Translate short canonical record labels for display without changing originals or evidence."""

import asyncio
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .display_translation_store import DisplayTranslationStore
from .inference import InferenceError
from .summary_translation_structure import gather, translate_structure
from .translation import translation_targets


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordRef(Boundary):
    kind: Literal["inbox", "decision"]
    id: UUID
    version: int = Field(ge=1, strict=True)


class TopicRef(Boundary):
    kind: Literal["source_topic"]
    id: UUID
    source_version: str = Field(min_length=1, max_length=200)
    transcript_version: str = Field(min_length=1, max_length=200)
    start_offset: int = Field(ge=0, strict=True)
    end_offset: int = Field(ge=1, strict=True)
    chapter_id: str | None = Field(default=None, min_length=1, max_length=400)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_offset <= self.start_offset:
            raise ValueError("Topic end must follow its start")
        return self


class DisplayBatch(Boundary):
    language: str = Field(min_length=1, max_length=35)
    items: list[Annotated[RecordRef | TopicRef, Field(discriminator="kind")]] = Field(
        min_length=1, max_length=50,
    )


async def translate_batch(memory, body):
    language, _ = translation_targets(memory, body.language)
    refs = [ref.model_dump(mode="json", exclude_none=True) for ref in body.items]
    async with memory.translation_lock:
        store = DisplayTranslationStore(memory)
        values, cached = await run_in_threadpool(store.load, refs, language)
        missing = {value["content_key"]: value for value in values
                   if value["content_key"] not in cached}
        translated = {}
        if missing:
            memory.settings.require_inference("llm")
            semaphore = asyncio.Semaphore(3)

            async def translate(key, value):
                text, _ = await translate_structure(
                    memory.inference, value["original_title"], language, semaphore=semaphore,
                )
                return key, text

            try:
                async with asyncio.timeout(memory.settings.inference_timeout_seconds):
                    translated = dict(await gather(*(
                        translate(key, value) for key, value in missing.items()
                    )))
            except (InferenceError, httpx.HTTPError, TimeoutError) as exc:
                raise HTTPException(
                    502, "Display translation failed. Original text is available."
                ) from exc
        await run_in_threadpool(store.save, values, translated, language)
        return {"language": language, "items": [
            {**value["ref"], "original_title": value["original_title"],
             "title": (translated | cached)[value["content_key"]],
             "content_key": value["content_key"]} for value in values
        ]}


def display_translation_routes(get_memory):
    router = APIRouter(prefix="/v1/display-translations")

    @router.post("")
    async def translate(body: DisplayBatch, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await translate_batch(get_memory(), body)

    return router
