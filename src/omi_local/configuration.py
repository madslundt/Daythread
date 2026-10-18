"""Editable service configuration. Secrets never enter the archive database or API reads."""

import json
import os
import tempfile
from pathlib import Path
from typing import get_args
from uuid import uuid4

from pydantic import SecretStr, ValidationError

from .settings import DEPLOYMENT_FIELDS, RUNTIME_FIELDS, Settings, validate_setting_changes

RESTART_FIELDS = {"data_dir", "web_dir", "worker_enabled"} | {
    key for key in Settings.model_fields if key.startswith("embedding_")
}
ROUTE_FIELDS = {"stt_routes", "llm_routes"}
RETIRED_FIELDS = {
    "meeting_max_minutes", "meeting_min_seconds", "meeting_context_seconds",
    "meeting_quiet_minutes",
    "marker_context_seconds", "marker_after_seconds", "marker_cooldown_seconds",
    "recap_hour", "recap_interval_minutes", "recap_quiet_minutes",
    "capture_paused",
}
SECRET_FIELDS = {
    key
    for key, field in Settings.model_fields.items() if key not in DEPLOYMENT_FIELDS
    if field.annotation is SecretStr or SecretStr in get_args(field.annotation)
}


def plain(value):
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    return value


def group(key: str) -> str:
    if key.startswith("apns_"):
        return "Push notifications"
    if key.startswith(("diarization_", "voice_recognition_")):
        return "Speaker detection"
    if key.startswith("stt_"):
        return "Transcription"
    if key.startswith(("llm_", "chat_")) or key in {
        "summary_language", "translation_languages"
    }:
        return "Language models & chat"
    if key.startswith("embedding_"):
        return "Semantic search"
    if key in {"inference_token", "inference_timeout_seconds"}:
        return "Local inference"
    if key.startswith("pocket_"):
        return "Device connections"
    if key.startswith("omi_") or key.startswith("audio_forwarding_"):
        return "Omi connections"
    if key.startswith(("meeting_", "marker_")):
        return "Capture & hints"
    return "Archive & processing"


DESCRIPTIONS = {
    "voice_recognition_enabled": "Remember explicitly confirmed voices across conversations in "
    "this archive. Suggestions require a passing evaluation on separate recordings; names are "
    "never applied automatically. Original audio retention also removes learned examples.",
    "voice_recognition_report": "Local JSON report from scripts/evaluate_speakers.py. "
    "Suggestions stay off until its model, confirmed examples and accuracy checks match. "
    "A relative path is resolved within this archive's data directory.",
    "diarization_enabled": "Enable local speaker detection for meetings and conversations. "
    "Requires the separate local diarization service and downloaded model weights. "
    "Names apply only to the selected conversation; simultaneous voices remain mixed.",
    "diarization_base_url": "Local speaker service URL reachable from home-core. "
    "Start scripts/diarization_server.py in its isolated Python runtime; default port 8790.",
    "diarization_timeout_seconds": "Maximum time for one conversation's speaker detection. "
    "Runs separately from transcription and preserves original recordings and transcripts.",
    "recap_hour": "Legacy scheduling setting. Uploaded recordings are summarized after "
    "transcription; live conversations wait for the configured pause.",
    "recap_interval_minutes": "Legacy scheduling setting. Uploaded recordings are summarized "
    "after transcription; live conversations wait for the configured pause.",
    "recap_quiet_minutes": "Live conversations wait for this many minutes of recorded quiet. "
    "Default: 5 minutes. Uploaded files are summarized after transcription. "
    "Missing uploads do not count as silence.",
    "stt_model": "Default transcription model served by your configured provider.",
    "stt_chunk_seconds": "Maximum audio duration per transcription request, in seconds. "
    "Longer recordings are split and their transcripts joined. Default: 30. "
    "Use a duration accepted by every configured STT route; gateway model lists may not "
    "advertise audio limits. Applies to subsequent recordings without a restart.",
    "stt_filter_speech": "Skip locally detected silence and non-speech before transcription. "
    "Speech receives surrounding context; original audio and playback stay unchanged. "
    "Detection can miss quiet speech; disable to transcribe full recordings. "
    "If detection is unavailable, full audio is sent instead. Applies to new processing only.",
    "stt_preserve_quiet": "Avoid the legacy volume threshold for faint or overlapping voices. "
    "Speech filtering is controlled separately by STT filter speech. "
    "Disable only to opt into the legacy silence threshold, which may miss quiet voices.",
    "silence_threshold": "Legacy PCM16 RMS floor. Used only when STT preserve quiet is disabled; "
    "0 disables that gate. A positive floor can skip quiet speech. This is not speech detection.",
    "stt_max_gain_db": "Maximum uniform amplification for quiet transcription copies (0–24 dB, "
    "default 12; 0 disables). RMS and peak limits prevent clipping. "
    "Original playback is unchanged. "
    "Amplification also raises noise; it cannot separate simultaneous voices.",
    "stt_boundary_search_seconds": "Look back up to 0.5 seconds for a quiet request boundary. "
    "Every sample and pause is retained. 0 uses fixed boundaries. Applies inside an upload only.",
    "stt_enhancement_mode": "on_empty (default) creates a cleanup reading when the original "
    "transcript is empty; compare also checks nonempty chunks; off disables cleanup. Requires "
    "FFmpeg on home-core. Cleanup readings always need review before entering search or summaries; "
    "listen to the original and save a transcript correction. Denoising can suppress quiet voices.",
    "stt_enhancement_timeout_seconds": "Total optional cleanup budget per recording, including "
    "FFmpeg and speech requests. Also limited by the remaining inference budget. "
    "Cleanup failures preserve the original transcript.",
    "stt_base_url": "Compatible transcription endpoint including /v1. Local HTTP(S) and hosted "
    "HTTPS endpoints are supported. Hosted providers receive the audio sent for transcription.",
    "llm_base_url": "Compatible chat completions endpoint including /v1. Local HTTP(S) and hosted "
    "HTTPS endpoints are supported. Hosted providers receive prompts and selected transcripts.",
    "embedding_base_url": "Compatible embeddings endpoint including /v1. Hosted HTTPS providers "
    "receive indexed transcripts and search queries; local HTTP(S) endpoints are also supported.",
    "stt_api_key": "Transcription provider API key, sent as a Bearer credential. Use the shared "
    "key to inherit the gateway credential, or no authentication for an open local server.",
    "llm_api_key": "Language model provider API key, sent as a Bearer credential. Use the shared "
    "key to inherit the gateway credential, or no authentication for an open local server.",
    "stt_routes": "Override model, endpoint or token per language code (da, en), detector name "
    "(danish), or auto. Blank model/endpoint inherits the defaults. Automatic routing needs "
    "a multilingual default model that returns language metadata.",
    "llm_routes": "Overrides for chat, summary, extraction, preparation, weekly and retrieval. "
    "Blank model, endpoint or revision inherits the defaults. Route keys override provider keys.",
    "inference_token": "Shared STT/LLM gateway credential, used when no provider key is set. "
    "Leave unchanged to keep it.",
    "pocket_api_key": "Pocket developer API key for optional transcript import.",
    "omi_api_key": "Personal Omi developer API key for optional transcript import.",
    "audio_forwarding_token": "Separate receiver secret: 32–128 URL-safe letters, digits, _ or -.",
    "embedding_enabled": "Requires a model, immutable revision and positive output dimension.",
    "embedding_revision": "Change this when replacing embedding weights behind the same alias.",
    "llm_revision": "Change this when replacing model weights to invalidate derived caches.",
    "llm_enable_thinking": "Default uses the model's setting. Supported gateways can enable or "
    "disable reasoning explicitly.",
    "data_dir": "Archive path on home-core. Changing it selects that archive after restart; "
    "existing recordings are not moved.",
    "web_dir": "Optional custom web asset directory on home-core. Empty uses bundled assets.",
    "summary_language": "Language for summaries, independently of transcription. "
    "auto follows the source language; use a language code such as da or en to choose one.",
    "translation_languages": "Languages available for transcript and summary translation, "
    "independently of the generated summary language. Use language codes such as en or da; "
    "the first is the default target. An empty list disables translation until a language "
    "is added.",
    "timezone": "IANA timezone used for calendar boundaries, for example Europe/Copenhagen.",
    "audio_retention_days": "Days to keep audio. 0 keeps it indefinitely. "
    "Applies to existing audio.",
    "transcript_retention_days": "Days to keep transcripts. 0 keeps them indefinitely. Expiry "
    "also clears dependent generated content.",
}


def descriptors() -> list[dict]:
    schema = Settings.model_json_schema()["properties"]
    result = []
    for key, prop in schema.items():
        if key in DEPLOYMENT_FIELDS | RETIRED_FIELDS:
            continue
        variants = prop.get("anyOf", [prop])
        kind = next((item.get("type") for item in variants if item.get("type") != "null"), "string")
        if key in SECRET_FIELDS:
            kind = "secret"
        elif key in ROUTE_FIELDS:
            kind = "routes"
        title = key.replace("_", " ").capitalize()
        for word in ("Stt", "Llm", "Api", "Omi"):
            title = title.replace(word, "Omi" if word == "Omi" else word.upper())
        title = {
            "meeting_quiet_minutes": "End meeting after recorded quiet (minutes)",
            "pocket_api_key": "Pocket API key",
            "stt_base_url": "Transcription endpoint",
            "stt_api_key": "Transcription API key",
            "stt_model": "Transcription model",
            "llm_base_url": "Summaries & chat endpoint",
            "llm_api_key": "Summaries & chat API key",
            "llm_model": "Summaries & chat model",
            "voice_recognition_enabled": "Remember speakers",
            "voice_recognition_report": "Speaker recognition evaluation report",
            "diarization_enabled": "Enable speaker detection",
            "diarization_base_url": "Speaker service address",
            "diarization_timeout_seconds": "Speaker detection timeout (seconds)",
        }.get(key, title)
        result.append(
            {
                "key": key,
                "label": title,
                "group": group(key),
                "kind": kind,
                "nullable": any(item.get("type") == "null" for item in variants),
                "restart_required": key in RESTART_FIELDS,
                "description": DESCRIPTIONS.get(key, ""),
                **{
                    name: prop[name]
                    for name in ("minimum", "maximum", "minLength", "maxLength")
                    if name in prop
                },
            }
        )
    return result


class Configuration:
    def __init__(self, initial: Settings):
        # Bind to the bootstrap archive location, even if a different data_dir is staged.
        self.path = initial.data_dir / ".private-settings.json"
        self.overrides = {}
        self.revision = str(uuid4())
        if self.path.exists():
            try:
                self.overrides = json.loads(self.path.read_text())["overrides"]
                if not isinstance(self.overrides, dict):
                    raise ValueError
                # Older installations could force a transcription language. Retire that
                # preference while preserving all remaining private configuration.
                self.overrides.pop("language", None)
                for key in DEPLOYMENT_FIELDS:
                    self.overrides.pop(key, None)
                if self.overrides.keys() - Settings.model_fields.keys():
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("Invalid private settings file") from exc

    def startup(self, initial: Settings) -> Settings:
        if initial.managed_hosting:
            # Imported private preferences must not replace the operator's model
            # contract, credentials, worker policy or archive location on restart.
            self.overrides = {
                key: value for key, value in self.overrides.items()
                if not key.startswith((
                    "stt_", "llm_", "embedding_", "chat_", "inference_", "diarization_",
                    "voice_recognition_", "audio_forwarding_",
                )) and key not in {"worker_enabled", "silence_threshold", "data_dir", "web_dir"}
            }
        try:
            return Settings.model_validate(initial.model_dump() | self.overrides)
        except ValidationError:
            # Do not let startup diagnostics print invalid write-only credentials.
            raise ValueError("Invalid private settings configuration") from None

    def desired(self, active: Settings) -> Settings:
        pending = {key: value for key, value in self.overrides.items() if key in RESTART_FIELDS}
        return Settings.model_validate(active.model_dump() | pending)

    def view(self, active: Settings) -> dict:
        desired = self.desired(active)
        values = plain(desired.model_dump())
        for key in DEPLOYMENT_FIELDS:
            values.pop(key, None)
        secrets = {}
        for key in SECRET_FIELDS:
            secrets[key] = bool(values[key])
            values[key] = None
        for key in ROUTE_FIELDS:
            for name, route in values[key].items():
                secrets[f"{key}.{name}.token"] = bool(route.pop("token", None))
        return {
            "values": values,
            "secrets": secrets,
            "fields": descriptors(),
            "revision": self.revision,
            "pending_restart": sorted(
                key for key in RESTART_FIELDS if getattr(desired, key) != getattr(active, key)
            ),
        }

    def prepare(self, active: Settings, changes: dict) -> Settings:
        validate_setting_changes(active, changes)
        if changes.keys() - Settings.model_fields.keys():
            raise ValueError("Unknown configuration setting")
        base = self.desired(active).model_dump()
        changes = dict(changes)
        for key in ROUTE_FIELDS & changes.keys():
            if not isinstance(changes[key], dict) or len(changes[key]) > 100:
                raise ValueError("Invalid model routes")
            routes = {}
            for name, override in changes[key].items():
                if not isinstance(override, dict):
                    raise ValueError("Invalid model route")
                # Replacing route lists must not clear write-only tokens just because
                # a client loaded the redacted configuration and edited a model name.
                old = base[key].get(name.lower(), {})
                routes[name] = {**({"token": old["token"]} if "token" in old else {}), **override}
            changes[key] = routes
        return Settings.model_validate(base | changes)

    def save(self, desired: Settings, keys) -> None:
        values = plain(desired.model_dump())
        overrides = self.overrides | {key: values[key] for key in keys if key not in RUNTIME_FIELDS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"version": 1, "overrides": overrides}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.overrides = overrides
        self.revision = str(uuid4())
