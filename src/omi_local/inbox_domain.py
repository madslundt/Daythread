"""Validated proposals and conservative calendar resolution for the inbox."""

import re
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .capture_relevance import CaptureSelection, passes_selection

Kind = Literal["commitment", "deadline", "question", "reminder", "event", "reflection", "note"]
ACTIONABLE_KINDS = frozenset({"commitment", "deadline", "question", "reminder", "event"})
SELECTION_CRITERIA = {
    "commitment": {"lasting_follow_up", "explicit_reminder"},
    "deadline": {"lasting_follow_up"},
    "question": {"consequential_question"},
    "reminder": {"explicit_reminder"},
    "event": {"agreed_event"},
    "reflection": {"meaningful_reflection"},
    "note": {"durable_reference"},
}
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
    selection: CaptureSelection[Span]


class Extraction(StrictModel):
    candidates: list[Candidate]


class Changes(StrictModel):
    kind: Kind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=150)
    deadline: Deadline | None = None
    user_note: str | None = Field(default=None, max_length=8000)


class Mutation(StrictModel):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    changes: Changes = Field(default_factory=Changes)
    status: Status | None = None
    reset: list[Literal["kind", "title", "owner", "deadline", "user_note"]] = Field(
        default=[], max_length=5
    )
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
        if candidate.selection.criterion == "none":
            continue
        if not passes_selection(candidate.selection, SELECTION_CRITERIA[candidate.kind]):
            raise ValueError("Selection criterion does not match candidate kind")
        # Utility must be supported by the same original context, never the model's rationale alone.
        for support in candidate.selection.evidence:
            evidence(support)
        if not any(
            support.source_id == primary.source_id
            and (
                support.start <= primary.start < primary.end <= support.end
                or primary.start <= support.start < support.end <= primary.end
            )
            for support in candidate.selection.evidence
            for primary in candidate.evidence
        ):
            raise ValueError("Selection evidence must include the candidate's own point")
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
                "_criterion": candidate.selection.criterion,
                "_primary": sorted((str(s.source_id), s.start, s.end) for s in candidate.evidence),
                "title": candidate.title.strip(),
                "owner": owner,
                "deadline": resolve_deadline(candidate.deadline_phrase, source, timezone),
                "evidence": list(
                    {(s["source_id"], s["start"], s["end"]): s for s in spans}.values()
                ),
            }
        )
    return consolidate_points(result)


def consolidate_points(proposals: list[dict]) -> list[dict]:
    """Consolidate exact duplicate points; overlapping claims may be distinct obligations."""
    result = []
    priority = {
        kind: rank
        for rank, kind in enumerate(
            ("reminder", "event", "commitment", "deadline", "question", "reflection", "note")
        )
    }
    for proposal in proposals:
        title = " ".join(re.findall(r"\w+", proposal["title"].casefold()))
        for index, other in enumerate(result):
            if title != " ".join(re.findall(r"\w+", other["title"].casefold())):
                continue
            if other["owner"] != proposal["owner"]:
                continue
            if deadline_key(other["deadline"]) != deadline_key(proposal["deadline"]):
                continue
            if (
                other["_primary"] != proposal["_primary"]
                or other["_criterion"] != proposal["_criterion"]
            ):
                continue
            kinds = {other["kind"], proposal["kind"]}
            if len(kinds) > 1 and not (
                kinds <= {"commitment", "reminder"}
                and proposal["_criterion"] == "explicit_reminder"
                or kinds <= {"commitment", "deadline"}
                and proposal["_criterion"] == "lasting_follow_up"
            ):
                continue
            chosen = proposal if priority[proposal["kind"]] < priority[other["kind"]] else other
            merged = chosen.copy()
            merged["evidence"] = list(
                {
                    (span["source_id"], span["start"], span["end"]): span
                    for span in [*other["evidence"], *proposal["evidence"]]
                }.values()
            )
            result[index] = merged
            break
        else:
            result.append(proposal)
    return [
        {key: value for key, value in item.items() if not key.startswith("_")} for item in result
    ]


def deadline_key(deadline: dict) -> tuple:
    if deadline["precision"] == "datetime":
        return "datetime", datetime.fromisoformat(deadline["instant"]).timestamp()
    if deadline["precision"] == "date":
        return "date", deadline["day"], deadline["timezone"]
    return "unknown", (deadline.get("phrase") or "").casefold().strip()
