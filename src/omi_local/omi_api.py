"""Read-only import from Omi's personal developer API into the local archive."""

import json
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError, model_validator

from .domain import stamp, utc_now
from .settings import Settings
from .store import Store
from .vendor_imports import (
    TranscriptAdapter,
    VendorError,
    VendorImportRequest,
    VendorPage,
    VendorRecording,
)

ENDPOINT = "https://api.omi.me/v1/dev/user/conversations"


OmiAPIError = VendorError
ImportRequest = VendorImportRequest


class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(max_length=20000)
    speaker_name: str | None = Field(default=None, max_length=200)
    speaker_id: int | str | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Invalid segment interval")
        return self


class Conversation(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    transcript_segments: list[Segment] | None = Field(default=None, max_length=10000)

    def source(self) -> dict | None:
        # Wait for finished conversations. Summaries are never treated as source transcripts.
        if not self.started_at or not self.finished_at or not self.transcript_segments:
            return None
        if not self.started_at < self.finished_at <= utc_now() + timedelta(seconds=30):
            raise ValueError("Invalid conversation interval")
        duration = (self.finished_at - self.started_at).total_seconds()
        if duration > 7 * 86400:
            raise ValueError("Conversation exceeds import duration limit")
        segments = sorted(self.transcript_segments, key=lambda segment: segment.start)
        if any(segment.end > duration + 1 for segment in segments):
            raise ValueError("Transcript segment exceeds conversation interval")
        lines = []
        for segment in segments:
            if not segment.text.strip():
                continue
            speaker = segment.speaker_name
            if not speaker and segment.speaker_id is not None:
                speaker = f"Speaker {segment.speaker_id}"
            lines.append(f"{speaker}: {segment.text.strip()}" if speaker else segment.text.strip())
        text = "\n".join(lines)
        if not text:
            return None
        if len(text) > 200000:
            raise ValueError("Conversation transcript exceeds import size limit")
        return {
            "id": str(uuid5(NAMESPACE_URL, f"{ENDPOINT}/{self.id}")),
            "source_id": self.id,
            "started_at": stamp(self.started_at),
            "ended_at": stamp(self.finished_at),
            "text": text,
            "segments": json.dumps([segment.model_dump() for segment in segments]),
        }


class OmiAPI(TranscriptAdapter):
    vendor_id = "omi"
    label = "Omi"
    endpoint = ENDPOINT
    identity_namespace = ENDPOINT
    key_setting = "omi_api_key"

    def __init__(self, settings: Settings, client: httpx.AsyncClient, store: Store):
        self.key = settings.omi_api_key.get_secret_value().strip()
        self.client, self.store = client, store

    def status(self) -> dict:
        return {
            "configured": bool(self.key),
            "mode": "developer-api",
            "endpoint": ENDPOINT,
            "imports_audio": False,
        }

    async def fetch_page(self, request: ImportRequest) -> VendorPage:
        if not self.key:
            raise OmiAPIError(
                "Save your Omi API key in app or web server settings to enable import", 503
            )
        params = request.model_dump(exclude_none=True, mode="json", exclude={"cursor"}) | {
            "include_transcript": "true"
        }
        try:
            async with self.client.stream(
                "GET",
                ENDPOINT,
                params=params,
                headers={"Authorization": f"Bearer {self.key}"},
                follow_redirects=False,
                timeout=60,
            ) as response:
                if response.status_code in (401, 403):
                    raise OmiAPIError(
                        "Omi rejected the developer key; check its conversations:read permission"
                    )
                if response.status_code == 429:
                    raise OmiAPIError("Omi API rate limit reached; wait before retrying", 503)
                if response.status_code != 200:
                    raise OmiAPIError("Omi API could not return conversations; retry later")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 16_000_000:
                        raise OmiAPIError("Omi response is too large; import fewer conversations")
        except httpx.HTTPError as exc:
            raise OmiAPIError("Omi API is unavailable; check connectivity and retry") from exc
        try:
            payload = json.loads(data)
            if not isinstance(payload, list) or len(payload) > request.limit:
                raise ValueError("Expected a conversation list")
            # Validate the entire page before writing any of it to the archive.
            sources = [Conversation.model_validate(item).source() for item in payload]
            has_more = len(payload) == request.limit
            next_offset = request.offset + len(payload)
            if "X-Omi-Has-More" in response.headers:
                continuation = response.headers["X-Omi-Has-More"].strip().lower()
                if continuation not in {"true", "false"}:
                    raise ValueError("Invalid continuation header")
                has_more = continuation == "true"
                if has_more:
                    # Upstream filters inaccessible records after computing the next offset.
                    # A short or empty response can therefore still have another page.
                    next_offset = int(response.headers.get("X-Omi-Next-Offset", ""))
                    if not request.offset < next_offset <= 1_000_000:
                        raise ValueError("Invalid next offset")
        except (ValueError, TypeError, ValidationError) as exc:
            raise OmiAPIError(
                "Omi returned invalid conversation data; nothing was imported"
            ) from exc
        return VendorPage(
            recordings=[
                VendorRecording(
                    source_id=source["source_id"],
                    started_at=source["started_at"],
                    ended_at=source["ended_at"],
                    text=source["text"],
                    segments=json.loads(source["segments"]),
                )
                if source
                else None
                for source in sources
            ],
            has_more=has_more,
            next_offset=next_offset,
        )
