"""Request-local numeric telemetry for an explicitly bound redo phase."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import uuid4

from .domain import stamp, utc_now

Stage = Literal["daily_summary", "day_overview", "week_overview"]
_phase: ContextVar[GenerationProgress | None] = ContextVar("redo_generation_phase", default=None)
_request: ContextVar[tuple[GenerationProgress, str] | None] = ContextVar(
    "redo_generation_request", default=None,
)


@dataclass
class _Request:
    id: str
    sequence: int
    started_at: datetime
    max_tokens: int
    thinking: bool | None
    status: str = "waiting"
    content_characters: int = 0
    reasoning_characters: int = 0
    output_bytes: int = 0
    events: int = 0
    provider_tokens: int | None = None
    last_reported_tokens: int = 0
    reported_bytes: int = 0
    last_token_at: datetime | None = None
    published_at: float = float("-inf")
    published: dict = field(default_factory=dict)

    def publish(self, *, force: bool = False) -> None:
        now = monotonic()
        if not force and now - self.published_at < 1:
            return
        self.published_at = now
        # SSE event counts are not tokens. Estimate only bytes after the latest exact sample.
        self.published = {
            "request_id": self.id, "request_sequence": self.sequence,
            "request_started_at": stamp(self.started_at), "status": self.status,
            "max_tokens": self.max_tokens, "thinking": self.thinking,
            "content_characters": self.content_characters,
            "reasoning_characters": self.reasoning_characters, "events": self.events,
            "output_tokens": self.provider_tokens if self.provider_tokens is not None
            else self.last_reported_tokens + ceil((self.output_bytes - self.reported_bytes) / 4),
            "token_count_source": "provider" if self.provider_tokens is not None else "estimate",
        }


class GenerationProgress:
    def __init__(self, stage: Stage, day: str):
        self.stage, self.day = stage, day
        self.started_at = utc_now()
        self._lock = Lock()
        self._requests: dict[str, _Request] = {}
        self._sequence = 0
        self._closed = False

    def begin(self, max_tokens: int, thinking: bool | None) -> str | None:
        with self._lock:
            if self._closed:
                return None
            self._sequence += 1
            request = _Request(str(uuid4()), self._sequence, utc_now(), max_tokens, thinking)
            request.publish(force=True)
            self._requests[request.id] = request
            return request.id

    def end(self, request_id: str) -> None:
        with self._lock:
            self._requests.pop(request_id, None)

    def update(
        self, request_id: str, *, content_characters: int, reasoning_characters: int,
        content_bytes: int, reasoning_bytes: int, output_tokens: int | None, finished: bool,
    ) -> None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or self._closed:
                return
            previous_status = request.status
            request.events += 1
            request.content_characters += content_characters
            request.reasoning_characters += reasoning_characters
            request.output_bytes += content_bytes + reasoning_bytes
            if content_characters or reasoning_characters:
                request.last_token_at = utc_now()
                request.provider_tokens = None  # An earlier usage sample is now out of date.
                request.status = "generating" if content_characters else "reasoning"
            if output_tokens is not None:
                if output_tokens > request.last_reported_tokens:
                    request.last_token_at = utc_now()
                    if request.status == "waiting":
                        request.status = "generating"
                request.last_reported_tokens = output_tokens
                request.reported_bytes = request.output_bytes
                request.provider_tokens = output_tokens
            first_provider_sample = output_tokens is not None and (
                request.published.get("token_count_source") != "provider"
            )
            request.publish(force=finished or previous_status != request.status
                            or first_provider_sample)

    def snapshot(self) -> dict | None:
        with self._lock:
            if self._closed:
                return None
            now = utc_now()
            result = {
                "stage": self.stage, "day": self.day, "active_requests": len(self._requests),
                "request_id": None, "request_sequence": self._sequence,
                "request_started_at": None, "status": "waiting", "max_tokens": None,
                "thinking": None, "content_characters": 0, "reasoning_characters": 0,
                "events": 0, "output_tokens": 0, "token_count_source": "estimate",
                "last_token_at": None, "seconds_since_token": 0, "quiet": False,
                "elapsed_seconds": max(0, (now - self.started_at).total_seconds()),
            }
            if self._requests:
                # Show one real request, never add counters from concurrent topic writers.
                request = next(iter(self._requests.values()))
                elapsed = max(0, (now - request.started_at).total_seconds())
                age = max(0, (now - (request.last_token_at or request.started_at)).total_seconds())
                result.update(request.published, elapsed_seconds=elapsed, seconds_since_token=age,
                              last_token_at=stamp(request.last_token_at)
                              if request.last_token_at else None, quiet=age >= 60)
            return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._requests.clear()


@contextmanager
def generation_phase(progress: GenerationProgress) -> Iterator[None]:
    phase_token = _phase.set(progress)
    request_token = _request.set(None)
    try:
        yield
    finally:
        progress.close()
        _request.reset(request_token)
        _phase.reset(phase_token)


@contextmanager
def generation_request(max_tokens: int, thinking: bool | None) -> Iterator[None]:
    phase = _phase.get()
    request_id = phase.begin(max_tokens, thinking) if phase else None
    if request_id is None:
        yield
        return
    token = _request.set((phase, request_id))
    try:
        yield
    finally:
        phase.end(request_id)
        _request.reset(token)


def generation_event(
    *, content_characters: int = 0, reasoning_characters: int = 0,
    content_bytes: int = 0, reasoning_bytes: int = 0,
    output_tokens: int | None = None, finished: bool = False,
) -> None:
    current = _request.get()
    if current:
        phase, request_id = current
        phase.update(request_id, content_characters=content_characters,
                     reasoning_characters=reasoning_characters, content_bytes=content_bytes,
                     reasoning_bytes=reasoning_bytes, output_tokens=output_tokens,
                     finished=finished)
