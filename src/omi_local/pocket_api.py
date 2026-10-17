"""Read-only Pocket transcript import using the documented public API.

Wire contract: https://docs.heypocketai.com/docs/api/recordings/list-recordings
and https://docs.heypocketai.com/docs/api/recordings/get-recording-details.
"""

from datetime import UTC, timedelta
from urllib.parse import quote

from pydantic import AwareDatetime, BaseModel, Field

from .vendor_imports import (
    TranscriptAdapter,
    TranscriptSegment,
    VendorImportRequest,
    VendorPage,
    VendorRecording,
)

ENDPOINT = "https://public.heypocketai.com/api/v1/public/recordings"


class PocketSegment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str | None = Field(default=None, max_length=20000)
    originalText: str | None = Field(default=None, max_length=20000)
    speaker: str | None = Field(default=None, max_length=200)
    speakerName: str | None = Field(default=None, max_length=200)

    def normalized(self) -> TranscriptSegment:
        return TranscriptSegment(
            start=self.start,
            end=self.end,
            text=self.text if self.text is not None else self.originalText or "",
            speaker_id=self.speaker,
            speaker_name=self.speakerName,
        )


class PocketTranscript(BaseModel):
    segments: list[PocketSegment] = Field(max_length=10000)


class PocketRecording(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    recording_at: AwareDatetime | None = None
    duration: float | None = Field(default=None, ge=0, le=7 * 86400, allow_inf_nan=False)
    transcript: PocketTranscript | None = None

    def source(self, request: VendorImportRequest) -> VendorRecording | None:
        # Missing capture time must never fall back to the upload's created_at.
        if not self.recording_at or not self.duration or not self.transcript:
            return None
        segments = sorted(
            (segment.normalized() for segment in self.transcript.segments),
            key=lambda segment: segment.start,
        )
        lines = []
        for segment in segments:
            if not segment.text.strip():
                continue
            speaker = segment.speaker_name
            if not speaker and segment.speaker_id is not None:
                speaker = f"Speaker {segment.speaker_id}"
            text = segment.text.strip()
            lines.append(f"{speaker}: {text}" if speaker else text)
        if not lines:
            return None
        recording = VendorRecording(
            source_id=self.id,
            started_at=self.recording_at,
            ended_at=self.recording_at + timedelta(seconds=self.duration),
            text="\n".join(lines),
            segments=segments,
        )
        # Pocket's remote date filters accept whole UTC days only. Preserve the
        # caller's exact start-inclusive/end-exclusive bounds on capture time.
        if request.start_date and recording.started_at < request.start_date:
            return None
        if request.end_date and recording.started_at >= request.end_date:
            return None
        return recording


def _data(payload: object) -> object:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Expected a successful Pocket response")
    return payload["data"]


class PocketAPI(TranscriptAdapter):
    vendor_id = "pocket"
    label = "Pocket"
    key_setting = "pocket_api_key"
    endpoint = ENDPOINT
    identity_namespace = ENDPOINT

    async def fetch_page(self, request: VendorImportRequest) -> VendorPage:
        if request.cursor is not None:
            raise ValueError("Pocket uses offset pagination")
        page_number, within_page = divmod(request.offset, request.limit)
        page_number += 1
        params = {"page": page_number, "limit": request.limit}
        for name in ("start_date", "end_date"):
            value = getattr(request, name)
            if value is not None:
                params[name] = value.astimezone(UTC).date().isoformat()
        payload, _ = await self.get_json(self.endpoint, params=params)
        data = _data(payload)
        if not isinstance(data, list) or len(data) > request.limit:
            raise ValueError("Expected a bounded Pocket recording list")
        pagination = payload["pagination"]
        if not isinstance(pagination, dict):
            raise ValueError("Invalid Pocket pagination")
        has_more = pagination["has_more"]
        actual_page, total_pages = pagination["page"], pagination["total_pages"]
        if (
            type(has_more) is not bool
            or type(actual_page) is not int
            or actual_page != page_number
            or type(total_pages) is not int
            or total_pages < 0
            or has_more != (actual_page < total_pages)
        ):
            raise ValueError("Inconsistent Pocket pagination")
        sources = []
        for item in data:
            recording = PocketRecording.model_validate(item)
            if recording.transcript is None:
                if recording.id in {".", ".."}:
                    raise ValueError("Invalid Pocket recording ID")
                detail, _ = await self.get_json(
                    f"{self.endpoint}/{quote(recording.id, safe='')}",
                    params={"include_transcript": "true", "include_summarizations": "false"},
                )
                full = PocketRecording.model_validate(_data(detail))
                if full.id != recording.id:
                    raise ValueError("Pocket detail ID does not match list")
                recording = full
            sources.append(recording.source(request))

        selected = sources[within_page:]
        return VendorPage(
            recordings=selected,
            has_more=has_more,
            next_offset=(page_number * request.limit if has_more
                         else request.offset + len(selected)),
        )
