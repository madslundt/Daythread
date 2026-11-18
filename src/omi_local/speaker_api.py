"""Profile-scoped speaker detection, manual naming, corrections, and original-turn audio."""

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .speakers import Kind
from .transcript_turns import saved_speaker_turns, transcript_speaker_names, transcript_turns


class Detect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_speakers: int | None = Field(default=None, ge=1, le=64, strict=True)
    reset: bool = False


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=100)


class Rename(Version):
    name: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Use a name without control characters")
        return value.strip() or None


class Reassign(Version):
    speaker_id: UUID


class Merge(Version):
    source_id: UUID
    target_id: UUID


def speaker_routes(get_memory) -> APIRouter:
    router = APIRouter(prefix="/v1/speakers")

    @router.patch("/transcript/{key}/{speaker_key}")
    def rename_transcript_speaker(
        key: UUID, speaker_key: str, body: Rename,
    ) -> dict:
        from .service import public_source

        memory = get_memory()
        with memory.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = memory.store.get_audio(str(key), db)
            if row is None:
                raise HTTPException(404, "Transcript not found")
            saved = transcript_speaker_names(row)
            changed = "Speaker assignments or evidence changed; reload and retry"
            if body.version != saved.get("version", row["source_version"]):
                raise HTTPException(409, changed)
            attributed = saved_speaker_turns(memory, [row], db).get(str(key))
            if not any(turn.get("speaker_edit", {}).get("id") == speaker_key
                       and turn["speaker_edit"]["kind"] == "transcript"
                       for turn in transcript_turns(row, attributed)):
                raise HTTPException(409, changed)
            names = saved.get("names", {})
            if body.name is None:
                names.pop(speaker_key, None)
            else:
                names[speaker_key] = body.name
            row["transcript_speaker_names"] = json.dumps({
                "source_version": row["source_version"], "version": str(uuid4()), "names": names,
            })
            db.execute("UPDATE audio SET transcript_speaker_names=? WHERE id=?",
                       (row["transcript_speaker_names"], str(key)))
            return public_source(row, attributed=attributed)

    @router.get("/{kind}/{key}")
    def read(kind: Kind, key: UUID) -> dict:
        return get_memory().speakers.read(kind, str(key))

    @router.post("/{kind}/{key}")
    async def detect(kind: Kind, key: UUID, body: Detect | None = None) -> dict:
        body = body or Detect()
        try:
            return await get_memory().speakers.detect(kind, str(key), body.num_speakers, body.reset)
        except TimeoutError as exc:
            raise HTTPException(504, "Speaker detection timed out; try again") from exc

    @router.patch("/{kind}/{key}/turns/{turn_id}")
    def reassign(kind: Kind, key: UUID, turn_id: UUID, body: Reassign) -> dict:
        return get_memory().speakers.mutate(kind, str(key), body.version, "reassign", {
            "turn_id": str(turn_id), "speaker_id": str(body.speaker_id),
        })

    @router.post("/{kind}/{key}/merge")
    def merge(kind: Kind, key: UUID, body: Merge) -> dict:
        return get_memory().speakers.mutate(kind, str(key), body.version, "merge", {
            "source_id": str(body.source_id), "target_id": str(body.target_id),
        })

    @router.patch("/{kind}/{key}/{speaker_id}")
    def rename(kind: Kind, key: UUID, speaker_id: UUID, body: Rename) -> dict:
        return get_memory().speakers.mutate(kind, str(key), body.version, "rename", {
            "speaker_id": str(speaker_id), "name": body.name,
        })

    @router.get("/{kind}/{key}/{speaker_id}/audio")
    def audio(
        kind: Kind, key: UUID, speaker_id: UUID,
        version: str | None = Query(default=None, min_length=1, max_length=100),
        sample: bool = False,
    ) -> Response:
        return Response(get_memory().speakers.audio(
                            kind, str(key), str(speaker_id), version, sample=sample,
                        ),
                        media_type="audio/wav", headers={"Cache-Control": "no-store"})

    return router
