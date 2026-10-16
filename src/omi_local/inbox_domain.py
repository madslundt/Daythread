"""Validated proposals and conservative calendar resolution for the inbox."""

import re
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["commitment", "deadline", "question"]
Status = Literal["pending", "completed", "dismissed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Deadline(StrictModel):
    precision: Literal["unknown", "date", "datetime"] = "unknown"
    day: date | None = None
    instant: datetime | None = None
    timezone: str | None = None
    phrase: str | None = Field(default=None, max_length=300)
    basis: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError("Use a valid IANA timezone") from exc
        if self.precision != "unknown" and not self.timezone:
            raise ValueError("Resolved deadlines need a timezone")
        if self.precision == "date" and (self.day is None or self.instant is not None):
            raise ValueError("Date deadlines need only a day")
        if self.precision == "datetime" and (
            self.instant is None or self.instant.tzinfo is None or not self.timezone
        ):
            raise ValueError("Timed deadlines need an aware instant and timezone")
        if self.precision == "datetime":
            self.day = self.instant.astimezone(ZoneInfo(self.timezone)).date()
        if self.precision == "unknown" and (self.day is not None or self.instant is not None):
            raise ValueError("Unknown deadlines have no resolved date")
        return self


class Span(StrictModel):
    source_id: UUID
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=3000)


class Candidate(StrictModel):
    kind: Kind
    title: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=150)
    deadline_phrase: str | None = Field(default=None, max_length=300)
    evidence: list[Span] = Field(min_length=1, max_length=8)
    owner_evidence: Span | None = None
    deadline_evidence: Span | None = None


class Extraction(StrictModel):
    candidates: list[Candidate] = Field(max_length=30)


class Changes(StrictModel):
    kind: Kind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=150)
    deadline: Deadline | None = None


class Mutation(StrictModel):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    changes: Changes = Field(default_factory=Changes)
    status: Status | None = None
    reset: list[Literal["kind", "title", "owner", "deadline"]] = Field(default=[], max_length=4)
    duplicate_of: UUID | None = None

    @model_validator(mode="after")
    def valid(self):
        if any(
            getattr(self.changes, k) is None
            for k in ("kind", "title")
            if k in self.changes.model_fields_set
        ):
            raise ValueError("Kind and title cannot be cleared")
        if self.changes.title is not None and not self.changes.title.strip():
            raise ValueError("Title cannot be blank")
        if self.duplicate_of and self.status != "dismissed":
            raise ValueError("Duplicates must be dismissed")
        if set(self.reset) & self.changes.model_fields_set:
            raise ValueError("Cannot edit and reset the same field")
        return self


class Scan(StrictModel):
    start_day: date
    end_day: date
    retry_failed: bool = False

    @model_validator(mode="after")
    def bounded(self):
        if not 0 <= (self.end_day - self.start_day).days <= 30:
            raise ValueError("Choose up to 31 calendar days")
        return self


def resolve_deadline(phrase: str | None, source: dict, timezone: str) -> dict:
    """Only unambiguous civil dates; never trust an LLM-generated absolute deadline."""
    result = Deadline(phrase=phrase, timezone=timezone)
    if not phrase:
        return result.model_dump(mode="json")
    local = datetime.fromisoformat(source["started_at"]).astimezone(ZoneInfo(timezone))
    end = datetime.fromisoformat(source["ended_at"]).astimezone(ZoneInfo(timezone))
    value = phrase.strip().lower()
    # A chunk spanning midnight cannot establish the utterance date.
    if local.date() == end.date() and value in {"today", "tomorrow", "i dag", "i morgen"}:
        result.day = local.date() + timedelta(days=value in {"tomorrow", "i morgen"})
        result.precision, result.basis = "date", "capture date; archive timezone assumption"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            result.day = date.fromisoformat(value)
            result.precision, result.basis = "date", "explicit ISO date"
        except ValueError:
            pass
    return result.model_dump(mode="json")


def validate_candidates(raw: str, sources: list[dict], timezone: str) -> list[dict]:
    extracted = Extraction.model_validate_json(raw)
    allowed = {s["id"]: s for s in sources}

    def evidence(span: Span) -> dict:
        source = allowed.get(str(span.source_id))
        if (
            source is None
            or not source["slice_start"] <= span.start < span.end <= source["slice_end"]
        ):
            raise ValueError("Evidence is outside supplied transcript")
        text = source["text"][span.start - source["slice_start"] : span.end - source["slice_start"]]
        if text != span.quote:
            raise ValueError("Evidence quote does not match transcript")
        return span.model_dump(mode="json") | {
            "revision": source["revision"],
            "started_at": source["started_at"],
            "timing": "chunk",
        }

    result = []
    for candidate in extracted.candidates:
        if not candidate.title.strip():
            raise ValueError("Candidate title cannot be blank")
        spans = [evidence(s) for s in candidate.evidence]
        owner = candidate.owner
        if owner:
            if candidate.owner_evidence is None:
                raise ValueError("Owner needs explicit evidence")
            owner_span = evidence(candidate.owner_evidence)
            generic_speaker = re.fullmatch(r"(?:speaker|person)[_ -]?\d+", owner.lower())
            if (
                generic_speaker
                or owner.lower() not in owner_span["quote"].lower()
                or owner.lower()
                in {
                    "i",
                    "me",
                    "myself",
                    "we",
                    "us",
                    "you",
                    "jeg",
                    "mig",
                    "vi",
                    "du",
                    "wearer",
                    "owner",
                }
            ):
                raise ValueError("Owner is unsupported")
            source = allowed[owner_span["source_id"]]
            named_in_speech = False
            for occurrence in re.finditer(re.escape(owner), owner_span["quote"], re.IGNORECASE):
                position = owner_span["start"] - source["slice_start"] + occurrence.start()
                prefix = source["text"][:position].rsplit("\n", 1)[-1].strip()
                suffix = source["text"][position + len(owner) :].lstrip()
                if prefix or not suffix.startswith(":"):
                    named_in_speech = True
            if not named_in_speech:
                raise ValueError("A speaker label does not establish ownership")
            spans.append(owner_span)
        if candidate.deadline_phrase:
            if candidate.deadline_evidence is None:
                raise ValueError("Deadline needs explicit evidence")
            deadline_span = evidence(candidate.deadline_evidence)
            if candidate.deadline_phrase.lower() not in deadline_span["quote"].lower():
                raise ValueError("Deadline phrase is unsupported")
            spans.append(deadline_span)
            source = allowed[deadline_span["source_id"]]
        else:
            source = allowed[spans[0]["source_id"]]
        result.append(
            {
                "kind": candidate.kind,
                "title": candidate.title.strip(),
                "owner": owner,
                "deadline": resolve_deadline(candidate.deadline_phrase, source, timezone),
                "evidence": list(
                    {(s["source_id"], s["start"], s["end"]): s for s in spans}.values()
                ),
            }
        )
    return result
