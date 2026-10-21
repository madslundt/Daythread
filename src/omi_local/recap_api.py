"""Daily and filtered recap routes; filtered GET starts durable work, never inline inference."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Response

from .day_overview import DayOverview
from .scoped_recap_jobs import with_generation


def recap_routes(get_memory, get_jobs):
    router = APIRouter(prefix="/v1/recaps")

    @router.get("/{day}/overview")
    def read_overview(day: date, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return DayOverview(get_memory()).cached(day)

    @router.post("/{day}/overview")
    async def generate_overview(day: date, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return await DayOverview(get_memory()).generate(day)

    @router.get("/{day}")
    def read(day: date, response: Response, tag_id: UUID | None = None,
             zone_id: UUID | None = None) -> dict:
        response.headers["Cache-Control"] = "no-store"
        result = get_memory().cached_recap(day, tag_id, zone_id)
        return with_generation(get_jobs(), result) if tag_id or zone_id else result

    @router.post("/{day}")
    async def generate(day: date, response: Response, tag_id: UUID | None = None,
                       zone_id: UUID | None = None) -> dict:
        response.headers["Cache-Control"] = "no-store"
        if tag_id or zone_id:
            return await get_memory().recap(day, tag_id, zone_id)
        return await get_memory().recap(day)

    return router
