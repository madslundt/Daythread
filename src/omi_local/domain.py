import io
import json
import wave
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class EvidenceChanged(Exception):
    """Recorded sources changed while local inference was running."""


class ButtonEvent(BaseModel):
    event_id: UUID
    device_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    occurred_at: AwareDatetime
    action: Literal["mark", "meeting_toggle"]
    meeting_max_minutes: int | None = Field(default=None, ge=1, le=480, strict=True)
    meeting_min_seconds: int | None = Field(default=None, ge=60, le=3600, strict=True)
    marker_cooldown_seconds: int | None = Field(default=None, ge=0, le=3600, strict=True)
    meeting_intent: Literal["start", "end"] | None = None
    meeting_id: UUID | None = None
    meeting_capture_device_id: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[\w-]+$",
    )

    @model_validator(mode="after")
    def meeting_target(self):
        if self.meeting_intent is not None and self.action != "meeting_toggle":
            raise ValueError("Meeting intent requires a meeting event")
        if self.meeting_intent == "end" and self.meeting_id is None:
            raise ValueError("Ending a meeting requires its ID")
        if self.meeting_id is not None and self.meeting_intent != "end":
            raise ValueError("Meeting ID is only used when ending a meeting")
        if self.meeting_capture_device_id is not None and (
            self.action != "meeting_toggle" or self.meeting_intent != "start"
        ):
            raise ValueError("Capture device association requires a meeting start")
        return self


class RewindRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=60)
    end: AwareDatetime | None = None
    summarize: bool = True


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=12, ge=1, le=30)
    session_id: UUID | None = None
    day: date | None = None
    meeting_id: UUID | None = None
    start_day: date | None = None
    end_day: date | None = None

    @model_validator(mode="after")
    def exclusive_scope(self):
        if (self.start_day is None) != (self.end_day is None):
            raise ValueError("Provide both start_day and end_day")
        if self.start_day is not None and self.start_day > self.end_day:
            raise ValueError("start_day must be on or before end_day")
        if sum(value is not None for value in (self.day, self.meeting_id, self.start_day)) > 1:
            raise ValueError("Choose a date, a date range, or a meeting")
        return self


class ChatScopeConflict(ValueError):
    """An existing chat cannot change its recorded-evidence scope."""


class ChatScopeNotFound(LookupError):
    """The selected date or meeting does not exist."""


class TranscriptCorrection(BaseModel):
    text: str = Field(max_length=200000)


class CaptureGap(BaseModel):
    gap_id: UUID
    device_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    started_at: AwareDatetime
    ended_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=200)
    cause: Literal[
        "unknown",
        "connection",
        "packet_loss",
        "delivery_interrupted",
        "incomplete_frame",
        "storage",
        "decoder",
    ] = "unknown"

    @model_validator(mode="after")
    def ordered(self):
        if self.ended_at <= self.started_at:
            raise ValueError("Gap end must follow its start")
        return self


class HintPolicy(BaseModel):
    marker_cooldown_seconds: int = Field(default=60, ge=0, le=3600)
    marker_after_seconds: int = Field(default=60, ge=0, le=600)
    meeting_min_seconds: int = Field(default=60, ge=60, le=3600)
    meeting_context_seconds: int = Field(default=60, ge=0, le=600)
    meeting_quiet_minutes: int = Field(default=10, ge=1, le=30)


def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError("Audio must be mono PCM16 WAV at 16000 Hz")
            frames = wav.getnframes()
            actual = wav.readframes(frames)
            if not 0 < frames <= 16000 * 120 or len(actual) != frames * 2:
                raise ValueError("Audio must contain 0–120 seconds of complete PCM frames")
            return frames / 16000
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid PCM WAV file") from exc


def project_events(
    events: list[dict], now: datetime, quiet_by_device: dict[str, list] | None = None,
) -> dict:
    """Replay raw capture events into approximate hints, suppressing accidental gestures."""
    meetings: list[dict] = []
    markers: list[dict] = []
    active: dict | None = None
    next_marker_at: datetime | None = None
    policy = HintPolicy()

    def close_after_quiet(before: datetime) -> None:
        nonlocal active
        if not active:
            return
        # A brief pause during the first hour must never end a meeting. Only
        # continuous, positively observed quiet after that hour counts.
        eligible = datetime.fromisoformat(active["started_at"]) + timedelta(hours=1)
        for start, end, _proof in (quiet_by_device or {}).get(
            active["meeting_capture_device_id"], [],
        ):
            finish = max(datetime.fromisoformat(start), eligible) + timedelta(
                minutes=active["meeting_quiet_minutes"],
            )
            if finish <= min(datetime.fromisoformat(end), before):
                active.update(ended_at=stamp(finish), end_reason="observed_non_speech")
                active = None
                return

    for event in sorted(events, key=lambda event: (event["occurred_at"], event["event_id"])):
        at = datetime.fromisoformat(event["occurred_at"])
        if at > now:
            continue
        close_after_quiet(at)
        # Legacy timing overrides remain stored for retry identity only.
        # Only the new quiet threshold varies, captured when a meeting starts.
        metadata = json.loads(event.get("hint_policy", "{}"))
        if event["action"] == "mark":
            if next_marker_at is not None and at < next_marker_at:
                continue
            markers.append(
                {key: value for key, value in event.items() if key != "hint_policy"}
                | {
                    "context_start": stamp(at - timedelta(seconds=60)),
                    "context_end": stamp(at + timedelta(seconds=policy.marker_after_seconds)),
                    "timing": "approximate",
                }
            )
            next_marker_at = at + timedelta(seconds=policy.marker_cooldown_seconds)
        elif metadata.get("meeting_intent") == "end":
            if active and active["id"] == metadata.get("meeting_id"):
                active.update(ended_at=stamp(at), end_reason="button")
                active = None
        elif active and metadata.get("meeting_intent") == "start":
            continue
        elif active:
            active.update(ended_at=stamp(at), end_reason="button")
            active = None
        else:
            active = {
                "id": event["event_id"],
                "device_id": event["device_id"],
                "meeting_capture_device_id": metadata.get("meeting_capture_device_id") or (
                    None if event["device_id"] == "web" else event["device_id"]
                ),
                "started_at": stamp(at),
                "auto_stop_at": None,
                "meeting_quiet_minutes": metadata.get("meeting_quiet_minutes", 10),
                "ended_at": None,
                "end_reason": None,
                "qualifies_at": stamp(at + timedelta(seconds=policy.meeting_min_seconds)),
                "context_padding_seconds": policy.meeting_context_seconds,
                "timing": "approximate",
            }
            meetings.append(active)
    close_after_quiet(now)
    accepted = []
    for meeting in meetings:
        end = datetime.fromisoformat(meeting["ended_at"]) if meeting["ended_at"] else now
        qualified = end >= datetime.fromisoformat(meeting["qualifies_at"])
        meeting["status"] = (
            ("closed" if meeting["ended_at"] else "active") if qualified else "provisional"
        )
        padding = timedelta(seconds=meeting["context_padding_seconds"])
        meeting["context_start"] = stamp(datetime.fromisoformat(meeting["started_at"]) - padding)
        meeting["context_end"] = stamp(end + padding)
        if qualified:
            accepted.append(meeting)
    return {"meetings": accepted, "markers": markers, "active_meeting": active}


CaptureCause = Literal[
    "unknown",
    "user_pause",
    "connection",
    "packet_loss",
    "delivery_interrupted",
    "incomplete_frame",
    "storage",
    "decoder",
    "resumed",
]


class PhoneObservation(BaseModel):
    sequence: int = Field(ge=0, le=2**53 - 1, strict=True)
    observed_at: AwareDatetime
    device_id: str = Field(default="", max_length=80, pattern=r"^[\w-]*$")
    capture_source: Literal["omi", "phone", "unknown"] = "unknown"
    capture_state: Literal["capturing", "paused", "disconnected", "blocked", "unknown"]
    cause: CaptureCause = "unknown"
    requested_pause: bool
    last_decoded_at: AwareDatetime | None = None
    last_persisted_at: AwareDatetime | None = None
    pending_recordings: int = Field(ge=0, le=1_000_000)
    recovery_items: int = Field(ge=0, le=1_000_000)
    pending_seconds: float = Field(ge=0, le=1e10, allow_inf_nan=False)
    oldest_capture_at: AwareDatetime | None = None
    upload_policy: Literal["wifi_only", "any_network"]
    upload_state: Literal["idle", "active", "waiting", "retrying", "unknown"]
    storage_bytes: int = Field(ge=0, le=10**13)
    storage_budget_bytes: int = Field(gt=0, le=10**13)

    @model_validator(mode="after")
    def clock(self):
        if self.observed_at > utc_now() + timedelta(seconds=30):
            raise ValueError("Observation is in the future")
        for value in (self.last_decoded_at, self.last_persisted_at, self.oldest_capture_at):
            if value and value > self.observed_at + timedelta(seconds=30):
                raise ValueError("Capture time follows observation")
        return self


class CaptureState(BaseModel):
    phone_id: UUID
    occurred_at: AwareDatetime
    state: Literal["capturing", "paused", "disconnected", "blocked", "unknown"]
    cause: CaptureCause = "unknown"

    @model_validator(mode="after")
    def clock(self):
        if self.occurred_at > utc_now() + timedelta(seconds=30):
            raise ValueError("Capture state is in the future")
        if self.state == "paused" and self.cause != "user_pause":
            raise ValueError("Intentional pause requires user_pause cause")
        return self


class Feedback(BaseModel):
    issue: Literal["incorrect", "missing"]
    source_id: UUID | None = None
    output_id: UUID | None = None
    expected_source_ids: list[UUID] = Field(default_factory=list, max_length=30)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    stage: Literal["unknown", "capture", "stt", "retrieval", "generation"] = "unknown"
    note: str = Field(default="", max_length=2000)
    excerpt: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def references(self):
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end <= self.start
        ):
            raise ValueError("Provide an ordered missing interval")
        if not (self.source_id or self.output_id or self.start):
            raise ValueError("Attach a source, output or missing interval")
        return self


class FeedbackEvent(BaseModel):
    event_id: UUID
    status: Literal["open", "resolved"]
    stage: Literal["unknown", "capture", "stt", "retrieval", "generation"] = "unknown"
    note: str = Field(default="", max_length=2000)
