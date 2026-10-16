"""Typed decision commands; original speech and owner annotation remain distinct."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Anchor(Boundary):
    source_id: UUID
    source_version: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=20000)
    start: int = Field(ge=0, le=200000, strict=True)
    end: int = Field(ge=1, le=200000, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Evidence end must follow start")
        return self


class Claim(Boundary):
    text: str = Field(min_length=1, max_length=4000)
    evidence: list[Anchor] = Field(min_length=1, max_length=20)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A recorded statement must contain text")
        return value.strip()


class Relationship(Boundary):
    kind: Literal["project", "person", "commitment", "meeting"]
    label: str = Field(min_length=1, max_length=200)
    target_id: UUID | None = None


class DecisionContent(Boundary):
    choice: Claim
    context: Claim | None = None
    rationale: Claim | None = None
    alternatives: list[Claim] = Field(default_factory=list, max_length=20)
    owner_notes: str = Field(default="", max_length=8000)
    relationships: list[Relationship] = Field(default_factory=list, max_length=30)

    def anchors(self) -> list[Anchor]:
        claims = [self.choice, self.context, self.rationale, *self.alternatives]
        return [anchor for claim in claims if claim for anchor in claim.evidence]


class DecisionCreate(Boundary):
    command_id: UUID
    state: Literal["tentative", "final"] = "tentative"
    content: DecisionContent


class DecisionEvent(Boundary):
    command_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    type: Literal["edit", "confirm", "dismiss", "conflict", "resolve"]
    content: DecisionContent | None = None
    state: Literal["tentative", "final"] | None = None
    predecessor_id: UUID | None = None
    predecessor_version: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def command_shape(self):
        if self.type == "edit" and self.content is None:
            raise ValueError("Editing requires complete content")
        if self.type != "edit" and (self.content is not None or self.state is not None):
            raise ValueError("Only editing can replace content/state")
        if (self.predecessor_id is None) != (self.predecessor_version is None):
            raise ValueError("Revision confirmation requires predecessor ID and version")
        if self.predecessor_id and self.type != "confirm":
            raise ValueError("Link a revision only on confirmation")
        return self


class ExtractionRequest(Boundary):
    request_id: UUID
    day: date | None = None
    source_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)
    run_id: UUID | None = None

    @model_validator(mode="after")
    def scope(self):
        if sum(value is not None for value in (self.day, self.source_ids, self.run_id)) != 1:
            raise ValueError("Choose a day, source selection, or continuation run")
        return self


class Candidate(Boundary):
    classification: Literal["decision", "suggestion", "disagreement", "interpretation"]
    state: Literal["tentative", "final"] = "tentative"
    content: DecisionContent


class ExtractionOutput(Boundary):
    candidates: list[Candidate] = Field(max_length=20)
