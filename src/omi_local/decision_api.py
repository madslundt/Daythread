"""Local decision register routes, independent of capture and chat."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from .decision_calendar import export_calendar
from .decision_export import day_bounds
from .decision_followups import CalendarExportRequest, FollowUpRequest, suggest_followups
from .decision_service import extract
from .decisions import DecisionCreate, DecisionEvent, ExtractionRequest
from .service import Memory


def decision_routes(memory: Callable[[], Memory]) -> APIRouter:
    router = APIRouter(prefix="/v1")

    def call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, "Decision or source not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/decisions")
    def listing(
        response: Response,
        day: date | None = None,
        q: str = Query(default="", max_length=200),
        include_dismissed: bool = False,
        review_status: Literal["proposed", "confirmed", "dismissed"] | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        response.headers["Cache-Control"] = "no-store"
        start, end = (
            tuple(
                datetime.fromisoformat(value)
                for value in day_bounds(day.isoformat(), memory().settings.timezone)
            )
            if day
            else (None, None)
        )
        return call(
            memory().store.decisions, start, end, q, include_dismissed, review_status, limit, offset
        )

    @router.post("/decisions")
    def create(body: DecisionCreate, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return call(memory().store.create_decision, body)

    @router.get("/decisions/{key}")
    def detail(key: UUID, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return call(memory().store.decision, str(key))

    @router.post("/decisions/{key}/events")
    def event(key: UUID, body: DecisionEvent, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return call(memory().store.decision_event, str(key), body)

    @router.delete("/decisions/{key}")
    def delete(key: UUID, response: Response, expected_version: int = Query(ge=1)):
        response.headers["Cache-Control"] = "no-store"
        return call(memory().store.delete_decision, str(key), expected_version)

    @router.post("/decisions/{key}/suggestions")
    async def suggestions(key: UUID, body: FollowUpRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            return await suggest_followups(memory(), str(key), body)
        except KeyError as exc:
            raise HTTPException(404, "Decision or source not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/decisions/{key}/calendar-export")
    def calendar_export(key: UUID, body: CalendarExportRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return call(export_calendar, memory(), str(key), body)

    @router.post("/decision-extractions")
    async def extraction(body: ExtractionRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            return await extract(memory(), body)
        except KeyError as exc:
            raise HTTPException(404, "Extraction or source not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/decision-extractions/{key}")
    def extraction_detail(key: UUID, response: Response):
        from .decision_service import run_result

        response.headers["Cache-Control"] = "no-store"
        return call(run_result, memory().store, str(key))

    return router
