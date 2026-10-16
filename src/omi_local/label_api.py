"""Manual label API, deliberately independent from inference and capture hints."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from .label_models import (
    DirectoryResponse,
    LabelError,
    LabelMutation,
    MutationResponse,
    SourceResponse,
    TimelineResponse,
)

router = APIRouter(prefix="/v1/labels")


@router.get("", response_model=DirectoryResponse)
def directory(
    request: Request,
    q: str = Query(default="", max_length=200),
    kind: Literal["person", "project"] | None = None,
) -> DirectoryResponse:
    return DirectoryResponse.model_validate(request.state.memory.store.label_directory(q, kind))


@router.post("/mutations", response_model=MutationResponse)
def mutate(request: Request, body: LabelMutation) -> MutationResponse:
    return MutationResponse.model_validate(request.state.memory.store.label_mutate(body))


@router.get("/sources/{source_id}", response_model=SourceResponse)
def source(request: Request, source_id: UUID) -> SourceResponse:
    return SourceResponse.model_validate(
        request.state.memory.store.label_source(str(source_id))
    )


@router.get("/{entity_id}/timeline", response_model=TimelineResponse)
def timeline(
    request: Request,
    entity_id: UUID,
    start_day: date | None = None,
    end_day: date | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    revision: str | None = Query(default=None, max_length=32),
) -> TimelineResponse:
    memory = request.state.memory
    if start_day and end_day and end_day < start_day:
        raise HTTPException(422, "End date must follow start date")
    start = memory.day_window(start_day)[0] if start_day else None
    end = memory.day_window(end_day)[1] if end_day else None
    result = memory.store.label_timeline(str(entity_id), start, end)
    if revision and revision != result["revision"]:
        raise LabelError("Timeline changed; refresh before loading another page")
    result["total"] = len(result["items"])
    result["items"] = result["items"][offset : offset + limit]
    return TimelineResponse.model_validate(result)
