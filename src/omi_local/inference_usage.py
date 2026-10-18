"""Record provider attempts without recording credentials, prompts, or responses."""

import logging
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from uuid import UUID, uuid4

import httpx

from .domain import stamp, utc_now

logger = logging.getLogger(__name__)
_recording_id: ContextVar[str | None] = ContextVar("usage_recording_id", default=None)
_summary_run_id: ContextVar[str | None] = ContextVar("usage_summary_run_id", default=None)


@contextmanager
def summary_usage():
    token = _summary_run_id.set(str(uuid4()))
    try:
        yield
    finally:
        _summary_run_id.reset(token)


def submitted_characters(body: dict) -> int | None:
    """Count actual text messages, including instructions; never retain their text."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return None
    total = 0
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return None
        total += len(message["content"])
    return total


def summary_source_duration(sources: list[dict]) -> dict:
    """Measure selected recording time, never infer audio duration from text.

    Include empty source placeholders: cached conversation notes can represent
    several original recordings while only the first source carries their text.
    Overlapping intervals count once within this logical summary pass.
    """
    intervals = []
    complete = True
    for source in sources:
        try:
            start, end = (datetime.fromisoformat(source[key])
                          for key in ("started_at", "ended_at"))
            if start.tzinfo is None or end.tzinfo is None or end < start:
                raise ValueError("invalid_source_interval")
            intervals.append((start.astimezone(UTC), end.astimezone(UTC)))
        except (KeyError, TypeError, ValueError, OverflowError):
            complete = False
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return {
        "source_seconds": sum((end - start).total_seconds() for start, end in merged),
        "source_duration_complete": complete,
    }


def reported_tokens(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        return {}
    usage = payload["usage"]
    tokens = {}
    for name, aliases in (("input_tokens", ("prompt_tokens", "input_tokens")),
                          ("output_tokens", ("completion_tokens", "output_tokens"))):
        value = next((usage[key] for key in aliases if key in usage), None)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            tokens[name] = value
    return tokens


@contextmanager
def recording_usage(recording_id: str | None):
    token = _recording_id.set(recording_id)
    try:
        yield
    finally:
        _recording_id.reset(token)


class InferenceUsage:
    usage_recorder: Callable[[dict], None] | None = None

    def record_usage(self, event: dict) -> None:
        if self.usage_recorder is not None:
            self.usage_recorder({
                "recording_id": _recording_id.get(), "summary_run_id": _summary_run_id.get(),
                **event,
            })

    def record_summary_filter(self, filtering: dict, sources: list[dict]) -> None:
        self.record_usage({
            "kind": "summary_filter", "summary_run_id": _summary_run_id.get() or str(uuid4()),
            "filter_version": filtering["version"], "source_count": len(sources),
            **summary_source_duration(sources),
            **{key: filtering[key] for key in (
                "original_characters", "retained_characters", "removed_fillers", "omitted_sources",
            )},
        })

    def begin_usage(
        self, kind: str, submitted_seconds: float = 0, *, task: str | None = None,
        characters: int | None = None,
    ) -> dict:
        event = {
            "attempt_id": str(uuid4()), "created_at": stamp(utc_now()),
            "kind": kind, "task": task or (self.task if kind == "llm" else "transcription"),
            "submitted_seconds": submitted_seconds, "outcome": "pending",
            "credit_units": None, "billing": "unknown",
            "submitted_characters": characters,
        }
        # Persist before the provider can charge. Failure here prevents the request.
        self.record_usage(event)
        return event

    def finish_usage(
        self, attempt: dict, response: httpx.Response | None, outcome: str,
        *, token_usage: dict | None = None,
    ) -> None:
        event = {**attempt, "outcome": outcome}
        if attempt["kind"] == "llm":
            if token_usage is not None:
                event.update(token_usage)
            elif response is not None:
                try:
                    event.update(reported_tokens(response.json()))
                except (ValueError, httpx.ResponseNotRead):
                    pass
        if response is not None:
            try:
                billing = response.headers["X-Daythread-Billing"]
                operation_id = str(UUID(response.headers["X-Daythread-Operation-Id"]))
                credits = Decimal(response.headers["X-Daythread-Credits-Used"])
                units = credits * 1000
                if (
                    billing not in {"credits", "provider"} or not units.is_finite()
                    or not 0 <= units <= 2**63 - 1 or units != units.to_integral_value()
                    or (billing == "provider" and units != 0)
                ):
                    raise ValueError("billing_metadata_invalid")
                event.update(credit_units=int(units), operation_id=operation_id, billing=billing)
            except (KeyError, ValueError, DecimalException):
                pass  # Unknown is distinct from zero Daythread credits.
        try:
            self.record_usage(event)
        except Exception:
            # The durable pending attempt remains unknown. Do not retry paid work
            # merely because its final local accounting write failed.
            logger.warning("Could not finalize inference usage; pending charge remains unknown")
