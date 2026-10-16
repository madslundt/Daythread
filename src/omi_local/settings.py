from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain import HintPolicy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMI_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    timezone: str = "Europe/Copenhagen"
    stt_base_url: str = "http://127.0.0.1:4000/v1"
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    inference_token: SecretStr = SecretStr("")
    omi_api_key: SecretStr = SecretStr("")
    stt_model: str = "stt-danish"
    llm_model: str = "home"
    llm_revision: str = Field(default="", max_length=200)
    language: str = Field(default="auto", min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")
    meeting_max_minutes: int = Field(default=120, ge=1, le=480)
    marker_context_seconds: int = Field(default=60, ge=0, le=300)
    marker_after_seconds: int = Field(default=60, ge=0, le=600)
    marker_cooldown_seconds: int = Field(default=60, ge=0, le=3600)
    meeting_min_seconds: int = Field(default=60, ge=60, le=3600)
    meeting_context_seconds: int = Field(default=60, ge=0, le=600)
    recap_hour: int = Field(default=22, ge=0, le=23)
    worker_enabled: bool = True
    capture_paused: bool = False
    audio_retention_days: int = Field(default=0, ge=0, le=36500)
    transcript_retention_days: int = Field(default=0, ge=0, le=36500)
    inference_timeout_seconds: int = Field(default=120, ge=1, le=1800)
    silence_threshold: int = Field(default=35, ge=0, le=32767)
    stt_segment_timestamps: bool = False
    web_dir: Path | None = None

    @model_validator(mode="after")
    def meeting_period(self):
        if self.meeting_min_seconds > self.meeting_max_minutes * 60:
            raise ValueError("Meeting minimum must not exceed the automatic stop duration")
        return self

    @property
    def hint_policy(self) -> HintPolicy:
        return HintPolicy(**{key: getattr(self, key) for key in HintPolicy.model_fields})

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Use an IANA timezone such as Europe/Copenhagen") from exc
        return value

    @field_validator("stt_base_url", "llm_base_url")
    @classmethod
    def local_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Use a local HTTP(S) inference endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint must not contain credentials, query, or fragment")
        host = parsed.hostname
        try:
            address = ip_address(host)
            local = (address.is_private or address.is_loopback) and not address.is_unspecified
        except ValueError:
            local = (
                host == "localhost"
                or "." not in host
                or host.endswith((".home.arpa", ".local", ".lan"))
            )
        if not local:
            raise ValueError("Use a private IP or a local hostname for inference")
        return value.rstrip("/")


RUNTIME_FIELDS = (
    "timezone",
    "meeting_max_minutes",
    "meeting_min_seconds",
    "meeting_context_seconds",
    "marker_context_seconds",
    "marker_after_seconds",
    "marker_cooldown_seconds",
    "recap_hour",
    "language",
    "capture_paused",
    "audio_retention_days",
    "transcript_retention_days",
)


def public_settings(settings: Settings) -> dict:
    return {field: getattr(settings, field) for field in RUNTIME_FIELDS}
