"""Typed contracts and calendar rules for evidence-backed weekly reviews."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from .domain import utc_now

PROMPT_VERSION = 1
MAX_INPUT_CHARS = 2_000_000
MAX_MODEL_CALLS = 256
BATCH_CHARS = 9_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    source_id: UUID
    quote: str = Field(min_length=1, max_length=1600)


class ReviewItem(StrictModel):
    category: Literal["decision", "commitment", "discussion", "change"]
    claim: str = Field(min_length=1, max_length=1200)
    qualification: str = Field(min_length=1, max_length=800)
    status: Literal["supported", "explicitly_open", "follow_through_unconfirmed"]
    evidence: list[Evidence] = Field(min_length=1, max_length=12)


class CandidateItem(ReviewItem):
    category: Literal["decision", "commitment", "discussion", "change", "resolution"]


class CandidateItems(StrictModel):
    items: list[CandidateItem] = Field(max_length=80)


class ReviewItems(StrictModel):
    items: list[ReviewItem] = Field(max_length=80)


class GenerateReview(StrictModel):
    request_id: UUID
    force: bool = False


class Coverage(StrictModel):
    sources: int
    speech_sources: int
    speech_days: int
    pending_chunks: int
    empty_chunks: int
    expired_chunks: int
    audio_unavailable: int
    captured_seconds: float
    capture_gaps: int
    known_gap_seconds: float


class ReviewVersion(StrictModel):
    id: UUID
    week_start: date
    timezone: str
    summary_language: str = "auto"
    start: datetime
    end: datetime
    comparison_start: datetime
    generated_at: datetime
    cutoff: datetime
    partial: bool
    fingerprint: str
    prompt_version: int
    model: str
    coverage: Coverage
    comparison_coverage: Coverage
    items: list[ReviewItem]
    # Source snapshots preserve original evidence through reduction and corrections.
    sources: list[dict]


class ReviewJob(StrictModel):
    id: UUID
    week_start: date
    state: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    completed_calls: int
    version_id: UUID | None = None
    error: str | None = None
    created_at: str


class ReviewRead(StrictModel):
    week_start: date
    timezone: str
    summary_language: str = "auto"
    start: datetime
    end: datetime
    comparison_start: datetime
    cutoff: datetime
    partial: bool
    fingerprint: str
    coverage: Coverage
    comparison_coverage: Coverage
    state: Literal["generated", "not_generated", "empty"]
    version: ReviewVersion | None
    sources: list[dict]
    stale: bool
    job: ReviewJob | None


class GenerationResult(StrictModel):
    job: ReviewJob | None
    version_id: UUID | None
    state: Literal["empty"] | None = None


class VersionMetadata(StrictModel):
    id: UUID
    week_start: date
    fingerprint: str
    created_at: datetime


class VersionHistory(StrictModel):
    items: list[VersionMetadata]


def week_bounds(day: date, timezone: str) -> tuple[datetime, datetime, datetime]:
    if day.weekday() != 0 or day.year < 2 or day.year >= 9999:
        raise ValueError("Choose a Monday in a supported calendar year")
    zone = ZoneInfo(timezone)
    return tuple(
        datetime.combine(day + timedelta(days=offset), time.min, zone).astimezone(UTC)
        for offset in (-7, 0, 7)
    )


def default_week(timezone: str, now: datetime | None = None) -> date:
    today = (now or utc_now()).astimezone(ZoneInfo(timezone)).date()
    return today - timedelta(days=today.weekday() + 7)


def markdown(version: dict, stale: bool = False) -> str:
    headings = {
        "decision": "Supported decisions",
        "commitment": "Commitments needing attention",
        "discussion": "Recurring discussions",
        "change": "Changes since the previous week",
    }
    lines = [
        f"# Weekly review · {version['week_start']}",
        "",
        f"Version: {version['id']} · {version['timezone']}",
        f"Selected: [{version['start']}, {version['end']})",
        f"Compared with: [{version['comparison_start']}, {version['start']})",
        f"Evidence cutoff: {version['cutoff']} · generated: {version['generated_at']}",
        f"Model route: {version['model']} · prompt/schema: {version['prompt_version']}",
        "STALE / HISTORICAL: evidence or configuration has changed." if stale else "",
        "Partial week." if version["partial"] else "",
        "Coverage describes received evidence only. Unuploaded phone backlog is unknown.",
        "Whole overlapping transcript chunks may extend beyond the calendar boundaries.",
        "Absence of later mention never establishes completion.",
        "This is a two-week review, not a complete backlog of older commitments.",
        "",
    ]
    for label, key in (("Selected week", "coverage"), ("Comparison week", "comparison_coverage")):
        lines.extend([f"## {label} coverage", "", str(version[key]), ""])
    for category, title in headings.items():
        lines.extend([f"## {title}", ""])
        matching = [item for item in version["items"] if item["category"] == category]
        if not matching:
            lines.extend(["No supported items found in the recorded evidence.", ""])
        for item in matching:
            lines.append(f"- {item['claim']} ({item['status']}) — {item['qualification']}")
            lines.extend(f"  - [{ref['source_id']}] {ref['quote']}" for ref in item["evidence"])
        lines.append("")
    lines.extend(["## Original transcript evidence", ""])
    lines.extend(
        f"- [{source['id']}] {source['started_at']}–{source['ended_at']}\n{source['text']}\n"
        for source in version["sources"]
    )
    return "\n".join(lines)
