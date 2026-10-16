"""Explicit voice enrollment and forgetting; suggestions are always user-confirmed."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, model_validator

from .speaker_api import Rename, Version
from .speakers import Kind


class Remember(Rename):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    profile_id: UUID | None = None

    @model_validator(mode="after")
    def one_identity(self):
        if (self.name is None) == (self.profile_id is None):
            raise ValueError("Choose an existing voice or provide a name for a new one")
        return self


class Confirm(Version):
    profile_id: UUID


def voice_routes(get_memory) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/voice-profiles")
    def directory() -> dict:
        return get_memory().voices.directory()

    @router.delete("/voice-profiles/{profile_id}/examples/{example_id}")
    def forget_example(
        profile_id: UUID, example_id: UUID, revision: str = Query(min_length=1, max_length=100),
    ) -> dict:
        return get_memory().voices.forget(str(profile_id), revision, str(example_id))

    @router.delete("/voice-profiles/{profile_id}")
    def forget_profile(
        profile_id: UUID, revision: str = Query(min_length=1, max_length=100),
    ) -> dict:
        return get_memory().voices.forget(str(profile_id), revision)

    @router.post("/speakers/{kind}/{key}/{speaker_id}/remember")
    async def remember(kind: Kind, key: UUID, speaker_id: UUID, body: Remember) -> dict:
        try:
            return await get_memory().voices.remember(
                kind, str(key), str(speaker_id), body.version,
                str(body.profile_id) if body.profile_id else None, body.name,
            )
        except TimeoutError as exc:
            raise HTTPException(504, "Learning this voice timed out; try again") from exc

    @router.post("/speakers/{kind}/{key}/{speaker_id}/confirm")
    async def confirm(kind: Kind, key: UUID, speaker_id: UUID, body: Confirm) -> dict:
        try:
            return await get_memory().voices.remember(
                kind, str(key), str(speaker_id), body.version, str(body.profile_id), confirm=True,
            )
        except TimeoutError as exc:
            raise HTTPException(504, "Confirming this voice timed out; try again") from exc

    return router
