"""Ephemeral conversation preparation contracts; no archive or user-context writes."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PrepModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrepSearch(PrepModel):
    kind: Literal["person", "project", "topic"] = "person"
    subject: Phrase
    aliases: list[Phrase] = Field(default_factory=list, max_length=10)
    qualifier: str = Field(default="", max_length=200)
    start_day: date | None = None
    end_day: date | None = None
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def valid_range(self):
        if bool(self.start_day) != bool(self.end_day):
            raise ValueError("Supply both dates or neither")
        if self.start_day and self.start_day > self.end_day:
            raise ValueError("Start date must precede end date")
        self.qualifier = self.qualifier.strip()
        return self


class PrepSelection(PrepModel):
    id: UUID
    version: str = Field(pattern=r"^[a-f0-9]{64}$")


class PrepValidate(PrepModel):
    search: PrepSearch
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")


class PrepGenerate(PrepValidate):
    selected: list[PrepSelection] = Field(min_length=1, max_length=40)
    latest_source_id: UUID
    user_context: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique_selection(self):
        ids = [entry.id for entry in self.selected]
        if len(set(ids)) != len(ids) or self.latest_source_id not in ids:
            raise ValueError("Select unique sources and confirm the latest relevant recording")
        return self


class PrepSource(PrepModel):
    id: str
    version: str
    device_id: str
    started_at: str
    ended_at: str
    state: str
    text: str
    segments: list[dict]
    audio_available: bool
    source_kind: str
    source_id: str
    source_version: str = ""
    transcript_version: str | None = None


class PrepCoverage(PrepModel):
    total_matches: int
    pending_chunks: int
    capture_gaps: int
    returned: int
    omitted_matches: int
    limitations: list[str]


class PrepSearchResult(PrepModel):
    as_of: str
    search: PrepSearch
    snapshot: str
    timezone: str
    sources: list[PrepSource]
    coverage: PrepCoverage


class PrepCitation(PrepModel):
    source_id: UUID
    excerpt: str = Field(min_length=1, max_length=2000)


class PrepItem(PrepModel):
    text: str = Field(min_length=1, max_length=2000)
    citations: list[PrepCitation] = Field(min_length=1, max_length=8)


class PrepOutput(PrepModel):
    latest_discussion: list[PrepItem] = Field(min_length=1, max_length=6)
    promises_to_check: list[PrepItem] = Field(max_length=12)
    recorded_questions: list[PrepItem] = Field(max_length=12)
    suggested_questions: list[PrepItem] = Field(max_length=12)


class PrepEvidence(PrepCitation):
    version: str
    started_at: str
    ended_at: str
    segment_start: float | None = None
    segment_end: float | None = None


class PrepResultItem(PrepModel):
    text: str
    citations: list[PrepEvidence]
    completion_status: Literal["unknown"] | None = None
    owner: None = None


class PrepReviewedExcerpt(PrepModel):
    id: str
    text: str
    started_at: str
    omitted_characters: int


class PrepResult(PrepSearchResult):
    reviewed_excerpts: list[PrepReviewedExcerpt]
    generated_at: str
    model: str
    prompt_version: str
    latest_source_id: UUID
    user_context: str
    latest_discussion: list[PrepResultItem]
    promises_to_check: list[PrepResultItem]
    recorded_questions: list[PrepResultItem]
    suggested_questions: list[PrepResultItem]
