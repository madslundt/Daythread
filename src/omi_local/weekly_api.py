"""HTTP interface for local weekly review generation and immutable versions."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from .weekly_models import (
    GenerateReview,
    GenerationResult,
    ReviewJob,
    ReviewRead,
    VersionHistory,
    markdown,
)


def weekly_routes(get_weekly) -> APIRouter:
    router = APIRouter(prefix="/v1/weekly-reviews", tags=["Weekly reviews"])

    def read(day: date | None = None, version_id: str | None = None) -> dict:
        try:
            result = get_weekly().read(day, version_id)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if version_id and not result["version"]:
            raise HTTPException(404, "Review version not found")
        return result

    @router.get("", response_model=ReviewRead)
    def default_review() -> dict:
        """The last completed local Monday–Sunday week, without invoking inference."""
        return read()

    @router.get("/jobs/{job_id}", response_model=ReviewJob)
    def job(job_id: UUID) -> dict:
        result = get_weekly().store.job(str(job_id))
        if result is None:
            raise HTTPException(404, "Review job not found or removed by deletion/retention")
        return result

    @router.get("/{week_start}.md")
    def export(week_start: date, version_id: UUID | None = None) -> Response:
        result = read(week_start, str(version_id) if version_id else None)
        if not result["version"]:
            raise HTTPException(404, "Generate this week's review first")
        return Response(
            markdown(result["version"], result["stale"] or bool(version_id)),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="week-{week_start}.md"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/{week_start}/versions", response_model=VersionHistory)
    def versions(week_start: date) -> dict:
        read(week_start)
        return {"items": get_weekly().store.versions(week_start.isoformat())}

    @router.post("/{week_start}/generate", response_model=GenerationResult)
    def generate(week_start: date, body: GenerateReview, response: Response) -> dict:
        try:
            result = get_weekly().enqueue(week_start, str(body.request_id), body.force)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        response.status_code = (
            202 if (result.get("job") or {}).get("state") in ("queued", "running") else 200
        )
        return result

    @router.get("/{week_start}", response_model=ReviewRead)
    def review(week_start: date, version_id: UUID | None = Query(default=None)) -> dict:
        return read(week_start, str(version_id) if version_id else None)

    return router
