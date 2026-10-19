"""Shared, evidence-bearing utility judgments for one-pass capture extraction."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaptureSelection[Evidence: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: Literal[
        "lasting_follow_up",
        "explicit_reminder",
        "agreed_event",
        "consequential_question",
        "meaningful_reflection",
        "durable_reference",
        "consequential_decision",
        "none",
    ]
    reason: str = Field(min_length=1, max_length=1200, pattern=r"\S")
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


def passes_selection(selection: CaptureSelection | None, allowed_criteria: set[str]) -> bool:
    """Missing/negative judgments never default to keeping an inferred item.

    The caller must validate exact supporting quotations against its supplied original sources.
    Criterion and explanation are model interpretations, not independently established facts.
    """
    return bool(
        selection
        and selection.criterion in allowed_criteria
        and selection.reason.strip()
        and selection.evidence
    )
