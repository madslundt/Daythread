"""Explicit user-managed labels; imported names never establish identity."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .decisions import DecisionContent

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityDraft(Strict):
    kind: Literal["person", "project"]
    name: Name
    description: Description = ""
    aliases: list[Name] = Field(default_factory=list, max_length=30)


class SourceRef(Strict):
    id: UUID
    revision: str = Field(min_length=32, max_length=32, pattern="^[0-9a-f]{32}$")


class Mutation(Strict):
    mutation_id: UUID
    expected_revision: str = Field(min_length=32, max_length=32, pattern="^[0-9a-f]{32}$")
    id: UUID


class EntitySave(Mutation):
    action: Literal["entity.save"]
    create: bool = False
    value: EntityDraft


class Delete(Mutation):
    action: Literal["entity.delete", "link.delete", "note.delete", "decision_link.delete"]


class LinkSave(Mutation):
    action: Literal["link.save"]
    create: bool = False
    entity_id: UUID
    source: SourceRef
    relation: Literal["related", "mentioned", "participant", "about_project"]


class NoteSave(Mutation):
    action: Literal["note.save"]
    create: bool = False
    kind: Literal["conversation", "decision", "open_question"]
    title: Name
    body: Description = ""
    sources: list[SourceRef] = Field(min_length=1, max_length=100)
    entity_ids: list[UUID] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_refs(self):
        if len({ref.id for ref in self.sources}) != len(self.sources):
            raise ValueError("Select each source once")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("Select each label once")
        return self


class DecisionLink(Mutation):
    action: Literal["decision_link.save"]
    entity_id: UUID
    decision_id: UUID
    decision_version: int = Field(ge=1, strict=True)


class Merge(Mutation):
    action: Literal["entity.merge"]
    target_id: UUID


class Split(Mutation):
    action: Literal["entity.split"]
    new_id: UUID
    value: EntityDraft
    link_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    decision_link_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    note_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def selected_once(self):
        if not self.link_ids and not self.note_ids and not self.decision_link_ids:
            raise ValueError("Select at least one assignment to split")
        if len(set(self.link_ids)) != len(self.link_ids) or len(set(self.note_ids)) != len(
            self.note_ids
        ):
            raise ValueError("Select each assignment once")
        if len(set(self.decision_link_ids)) != len(self.decision_link_ids):
            raise ValueError("Select each decision association once")
        return self


LabelMutation = Annotated[
    EntitySave | Delete | LinkSave | NoteSave | DecisionLink | Merge | Split,
    Field(discriminator="action"),
]


class LabelError(Exception):
    def __init__(self, detail: str, status: int = 409):
        self.detail, self.status = detail, status
        super().__init__(detail)


class SourceView(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: UUID
    source_version: str
    device_id: str
    started_at: str
    ended_at: str
    state: str
    text: str | None
    audio_available: bool
    source_kind: str
    source_id: str | None


class EntityView(EntityDraft):
    id: UUID
    merged_into: UUID | None = None


class LinkView(Strict):
    id: UUID
    entity_id: UUID
    source_id: UUID
    source_revision: str
    relation: Literal["related", "mentioned", "participant", "about_project"]
    needs_review: bool


class RegisterDecision(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: UUID
    version: int
    content: DecisionContent | None
    review_status: Literal["proposed", "confirmed", "dismissed"]
    evidence_condition: Literal["current", "stale", "conflicted", "removed"]
    state: Literal["tentative", "final"]


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: UUID
    entry_type: Literal["source", "note", "decision"]
    started_at: str
    ended_at: str
    sources: list[SourceView]
    needs_review: bool
    title: str | None = None
    body: str | None = None
    kind: str | None = None
    relation: str | None = None
    decision_id: UUID | None = None
    decision: RegisterDecision | None = None


class DirectoryResponse(Strict):
    revision: str
    items: list[EntityView]


class SourceResponse(Strict):
    revision: str
    source: SourceView
    links: list[LinkView]


class TimelineResponse(Strict):
    revision: str
    entity: EntityView
    items: list[TimelineEntry]
    total: int = 0


class MutationResponse(Strict):
    applied: Literal[True] = True
    revision: str
