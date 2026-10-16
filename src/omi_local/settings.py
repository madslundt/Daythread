import json
import re
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain import HintPolicy

LLMTask = Literal["chat", "summary", "extraction", "preparation", "weekly", "retrieval"]


def local_endpoint(value: str) -> str:
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


class ModelRouteOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = None
    token: SecretStr | None = None

    @field_validator("model")
    @classmethod
    def nonempty_model(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Model must not be blank")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value: str | None) -> str | None:
        return local_endpoint(value) if value is not None else None


class LLMRouteOverride(ModelRouteOverride):
    revision: str | None = Field(default=None, max_length=200)


class ModelRoute(BaseModel):
    model: str
    base_url: str
    token: SecretStr


class LLMRoute(ModelRoute):
    revision: str

    @property
    def identity(self) -> dict:
        # Credentials never belong in generated output, cache fingerprints, or archives.
        return {"model": self.model, "endpoint": self.base_url, "revision": self.revision}

    @property
    def cache_key(self) -> str:
        return json.dumps(self.identity, sort_keys=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMI_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    data_dir: Path = Path("data")
    timezone: str = "Europe/Copenhagen"
    stt_base_url: str = "http://127.0.0.1:4000/v1"
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    inference_token: SecretStr = SecretStr("")
    audio_forwarding_enabled: bool = False
    audio_forwarding_token: SecretStr = SecretStr("")
    omi_api_key: SecretStr = SecretStr("")
    stt_model: str = "stt-danish"
    stt_chunk_seconds: int = Field(default=30, ge=1, le=120)
    stt_preserve_quiet: bool = True
    stt_max_gain_db: float = Field(default=12, ge=0, le=24)
    stt_boundary_search_seconds: float = Field(default=0.5, ge=0, le=0.5)
    stt_enhancement_mode: Literal["off", "on_empty", "compare"] = "on_empty"
    stt_enhancement_timeout_seconds: float = Field(default=15, gt=0, le=60)
    stt_routes: dict[str, ModelRouteOverride] = Field(default_factory=dict)
    llm_model: str = "home"
    llm_enable_thinking: bool | None = None
    llm_revision: str = Field(default="", max_length=200)
    llm_routes: dict[LLMTask, LLMRouteOverride] = Field(default_factory=dict)
    embedding_enabled: bool = False
    embedding_base_url: str = "http://127.0.0.1:4000/v1"
    embedding_token: SecretStr = SecretStr("")
    embedding_model: str = ""
    embedding_revision: str = ""
    embedding_dimension: int = Field(default=0, ge=0, le=8192)
    embedding_passage_chars: int = Field(default=1000, ge=200, le=4000)
    embedding_max_input_chars: int = Field(default=4000, ge=200, le=20000)
    embedding_query_prefix: str = Field(default="", max_length=1000)
    embedding_document_prefix: str = Field(default="", max_length=1000)
    embedding_timeout_seconds: int = Field(default=10, ge=1, le=60)
    embedding_query_timeout_seconds: float = Field(default=2, gt=0, le=60)
    embedding_query_cache_size: int = Field(default=128, ge=0, le=4096)
    embedding_query_cache_seconds: int = Field(default=300, ge=0, le=3600)
    embedding_query_retry_seconds: float = Field(default=15, ge=0, le=300)
    embedding_min_score: float = Field(default=0.5, ge=-1, le=1)
    summary_language: str = Field(
        default="auto", min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$"
    )
    meeting_max_minutes: int = Field(default=120, ge=1, le=480)
    marker_context_seconds: int = Field(default=60, ge=0, le=300)
    marker_after_seconds: int = Field(default=60, ge=0, le=600)
    marker_cooldown_seconds: int = Field(default=60, ge=0, le=3600)
    meeting_min_seconds: int = Field(default=60, ge=60, le=3600)
    meeting_context_seconds: int = Field(default=60, ge=0, le=600)
    recap_hour: int = Field(default=22, ge=0, le=23)
    recap_interval_minutes: int = Field(default=60, ge=0, le=1440)
    recap_quiet_minutes: int = Field(default=5, ge=1, le=30)
    worker_enabled: bool = True
    capture_paused: bool = False
    audio_retention_days: int = Field(default=7, ge=0, le=36500)
    transcript_retention_days: int = Field(default=30, ge=0, le=36500)
    inference_timeout_seconds: int = Field(default=120, ge=1, le=1800)
    chat_query_expansion: bool = True
    chat_query_expansion_timeout_seconds: float = Field(default=5, gt=0, le=60)
    silence_threshold: int = Field(default=35, ge=0, le=32767)
    stt_segment_timestamps: bool = False
    diarization_enabled: bool = False
    diarization_base_url: str = "http://127.0.0.1:8790"
    diarization_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    web_dir: Path | None = None

    @field_validator("stt_routes", mode="before")
    @classmethod
    def valid_stt_languages(cls, value):
        if not isinstance(value, dict):
            raise ValueError("STT routes must map language codes or names to overrides")
        routes = {}
        for language, override in value.items():
            if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z-]{2,32}", language):
                raise ValueError("Use a language code or a provider's language name for STT routes")
            normalized = language.lower()
            if normalized in routes:
                raise ValueError("Duplicate STT language route")
            routes[normalized] = override
        return routes

    def stt_override(self, language: str) -> ModelRouteOverride | None:
        language = language.lower()
        return self.stt_routes.get(language) or self.stt_routes.get(language.split("-")[0])

    def stt_route(self, language: str) -> ModelRoute:
        override = self.stt_override(language) or ModelRouteOverride()
        return ModelRoute(
            model=override.model if override.model is not None else self.stt_model,
            base_url=override.base_url if override.base_url is not None else self.stt_base_url,
            token=override.token if override.token is not None else self.inference_token,
        )

    def llm_route(self, task: LLMTask) -> LLMRoute:
        override = self.llm_routes.get(task, LLMRouteOverride())
        return LLMRoute(
            model=override.model if override.model is not None else self.llm_model,
            base_url=override.base_url if override.base_url is not None else self.llm_base_url,
            token=override.token if override.token is not None else self.inference_token,
            revision=override.revision if override.revision is not None else self.llm_revision,
        )

    @field_validator("audio_forwarding_token")
    @classmethod
    def forwarding_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", secret):
            raise ValueError("Audio forwarding token needs 32–128 URL-safe letters/digits/_/-")
        return value

    @model_validator(mode="after")
    def meeting_period(self):
        if self.meeting_min_seconds > self.meeting_max_minutes * 60:
            raise ValueError("Meeting minimum must not exceed the automatic stop duration")
        return self

    @model_validator(mode="after")
    def embedding_contract(self):
        if self.embedding_enabled and not (
            self.embedding_model.strip()
            and self.embedding_revision.strip()
            and self.embedding_dimension > 0
        ):
            raise ValueError("Enabled embeddings require model, immutable revision and dimension")
        if self.embedding_passage_chars + len(self.embedding_document_prefix) > (
            self.embedding_max_input_chars
        ):
            raise ValueError("Embedding passage and prefix exceed input character ceiling")
        return self

    @property
    def embedding_manifest(self) -> dict | None:
        if not self.embedding_enabled:
            return None
        return {
            "model": self.embedding_model,
            "revision": self.embedding_revision,
            "dimension": self.embedding_dimension,
            "metric": "cosine-float32-v1",
            "splitter": "characters-overlap-v1",
            "passage_chars": self.embedding_passage_chars,
            "query_prefix": self.embedding_query_prefix,
            "document_prefix": self.embedding_document_prefix,
            # A different endpoint may resolve the same alias to a different model.
            "endpoint": self.embedding_base_url,
        }

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

    @field_validator("stt_base_url", "llm_base_url", "embedding_base_url", "diarization_base_url")
    @classmethod
    def local_endpoint(cls, value: str) -> str:
        return local_endpoint(value)


RUNTIME_FIELDS = (
    "audio_forwarding_enabled",
    "timezone",
    "meeting_max_minutes",
    "meeting_min_seconds",
    "meeting_context_seconds",
    "marker_context_seconds",
    "marker_after_seconds",
    "marker_cooldown_seconds",
    "recap_hour",
    "recap_interval_minutes",
    "recap_quiet_minutes",
    "summary_language",
    "capture_paused",
    "audio_retention_days",
    "transcript_retention_days",
)


def public_settings(settings: Settings) -> dict:
    return {field: getattr(settings, field) for field in RUNTIME_FIELDS}
