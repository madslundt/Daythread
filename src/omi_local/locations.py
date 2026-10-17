"""Capture-time phone observations, separate from places mentioned in speech."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CaptureLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    horizontal_accuracy: float = Field(ge=0, le=1000)
    captured_at: AwareDatetime

    def validate_capture_time(self, start: datetime) -> None:
        age = (start - self.captured_at).total_seconds()
        if not -5 <= age <= 120:
            raise ValueError("Location must be observed within 120 seconds before capture")
