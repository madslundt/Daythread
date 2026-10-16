"""Shared web/native inbox boundary."""

from datetime import datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from .inbox_domain import Kind, Mutation, Scan, Status
from .inbox_jobs import InboxJobs
from .inbox_store import InboxConflict, InboxStore


def inbox_router(memory) -> APIRouter:
    router = APIRouter(prefix="/v1/inbox")

    @router.get("")
    def listing(
        status: Status = "pending",
        kind: Kind | None = None,
        due: Literal["overdue", "upcoming", "unknown"] | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return InboxStore(memory().store).listing(status, kind, due, limit, offset)

    @router.post("/extractions")
    def extract(body: Scan) -> dict:
        timezone = memory().settings.timezone
        zone = ZoneInfo(timezone)
        start = datetime.combine(body.start_day, time(), zone)
        end = datetime.combine(body.end_day + timedelta(days=1), time(), zone)
        jobs = InboxJobs(memory().store).discover(
            timezone,
            start,
            end,
            body.retry_failed,
            memory().settings.llm_route("extraction").cache_key,
        )
        return {"job_ids": jobs, "total": len(jobs)}

    @router.get("/extractions/{key}")
    def extraction(key: UUID) -> dict:
        result = InboxJobs(memory().store).status(str(key))
        if result is None:
            raise HTTPException(404, "Extraction not found")
        return result

    @router.get("/{key}")
    def item(key: UUID) -> dict:
        result = InboxStore(memory().store).item(str(key))
        if result is None:
            raise HTTPException(404, "Inbox item not found")
        return result

    @router.patch("/{key}")
    def mutate(key: UUID, body: Mutation) -> dict:
        try:
            return InboxStore(memory().store).mutate(str(key), body)
        except KeyError as exc:
            raise HTTPException(404, "Inbox item not found") from exc
        except InboxConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
