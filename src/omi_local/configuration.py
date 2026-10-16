"""Editable service configuration. Secrets never enter the archive database or API reads."""

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr, ValidationError

from .settings import RUNTIME_FIELDS, Settings

RESTART_FIELDS = {"data_dir", "web_dir", "worker_enabled"} | {
    key for key in Settings.model_fields if key.startswith("embedding_")
}
ROUTE_FIELDS = {"stt_routes", "llm_routes"}
SECRET_FIELDS = {
    key for key, field in Settings.model_fields.items() if field.annotation is SecretStr
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
    if key.startswith("stt_") or key == "language":
        return "Transcription"
    if key.startswith("llm_") or key.startswith("chat_"):
        return "Language models & chat"
    if key.startswith("embedding_"):
        return "Semantic search"
    if key in {"inference_token", "inference_timeout_seconds"}:
        return "Local inference"
    if key.startswith("omi_") or key.startswith("audio_forwarding_"):
        return "Omi connections"
    if key.startswith(("meeting_", "marker_")):
        return "Capture & hints"
    return "Archive & processing"


DESCRIPTIONS = {
    "stt_model": "Default transcription model served by your local gateway.",
    "stt_base_url": "Local transcription endpoint including /v1, reachable from home-core.",
    "stt_routes": "Override model, endpoint or token per language code (da, en), detector name "
    "(danish), or auto. Blank model/endpoint inherits the defaults. Automatic routing needs "
    "a multilingual default model that returns language metadata.",
    "llm_routes": "Overrides for chat, summary, extraction, preparation, weekly and retrieval. "
    "Blank model, endpoint or revision inherits the defaults.",
    "inference_token": "Shared local STT/LLM gateway credential. Leave unchanged to keep it.",
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
    "language": "auto for automatic/mixed speech, or a language code such as da or en.",
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
        variants = prop.get("anyOf", [prop])
        kind = next((item.get("type") for item in variants if item.get("type") != "null"), "string")
        if key in SECRET_FIELDS:
            kind = "secret"
        elif key in ROUTE_FIELDS:
            kind = "routes"
        title = key.replace("_", " ").capitalize()
        for word in ("Stt", "Llm", "Api", "Omi"):
            title = title.replace(word, "Omi" if word == "Omi" else word.upper())
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
                if self.overrides.keys() - Settings.model_fields.keys():
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("Invalid private settings file") from exc

    def startup(self, initial: Settings) -> Settings:
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
