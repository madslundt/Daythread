"""Command-line surface for importing an exact qualified recording transcript."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .merged_recordings import load_recording
from .recording_transcription_import import (
    ImportExpectations,
    ImportRefused,
    _LocalAudioReader,
    _receipt,
    publish_variant,
    read_qualified_variant,
    target_variant_disposition,
    validate_variant,
    verify_original_audio,
)
from .settings import Settings


def _loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ImportRefused("configuration URL must use local loopback HTTP")
    return value.rstrip("/")


def profile_directory(data_dir: Path, profile_id: str, known_profiles: set[str]) -> Path:
    if profile_id != "default":
        try:
            profile_id = str(UUID(profile_id))
        except ValueError as exc:
            raise ImportRefused("profile ID must be default or a known UUID") from exc
    if profile_id not in known_profiles:
        raise ImportRefused("target profile does not exist")
    return data_dir if profile_id == "default" else data_dir / "profiles" / profile_id


def _load_active_configuration(api_url: str) -> tuple[Settings, Path, set[str]]:
    api_url = _loopback_url(api_url)
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            config_response = client.get(f"{api_url}/v1/configuration")
            config_response.raise_for_status()
            config = config_response.json()
            profiles_response = client.get(f"{api_url}/v1/profiles")
            profiles_response.raise_for_status()
            profiles = profiles_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ImportRefused("local active configuration could not be read") from exc
    if config.get("pending_restart"):
        raise ImportRefused("active configuration has pending restart changes")
    values = config.get("values")
    if not isinstance(values, dict):
        raise ImportRefused("active configuration response is malformed")
    try:
        settings = Settings.model_validate(values)
        data_dir = Path(values["data_dir"]).resolve(strict=True)
        bootstrap = Settings()
        bootstrap_dir = bootstrap.data_dir.resolve(strict=True)
    except Exception as exc:
        raise ImportRefused("active local archive path or settings are unavailable") from exc
    if bootstrap_dir != data_dir:
        raise ImportRefused("local process archive path differs from active configuration")
    if bootstrap.database_url is not None or bootstrap.storage_bucket:
        raise ImportRefused("importer supports only the active local SQLite and audio store")
    profile_ids = {item.get("id") for item in profiles.get("profiles", [])
                   if isinstance(item, dict)}
    return settings, data_dir, profile_ids


def _connect_target(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise ImportRefused("target profile archive database is unavailable")
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True, help="private archive copy DB")
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--result-version", required=True)
    parser.add_argument("--expected-wav-sha256", required=True)
    parser.add_argument("--expected-service-image-id", required=True)
    parser.add_argument("--expected-homecompute-source-sha", required=True)
    parser.add_argument("--expected-model-sha256", required=True)
    parser.add_argument("--expected-model-snapshot", required=True)
    parser.add_argument("--expected-runtime", required=True)
    parser.add_argument("--expected-runtime-commit", required=True)
    parser.add_argument("--expected-alignment-model", required=True)
    parser.add_argument("--profile-id", required=True, help="default or an existing profile UUID")
    parser.add_argument("--api-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--apply", action="store_true", help="publish after full guarded validation",
    )
    args = parser.parse_args(argv)
    try:
        settings, data_dir, profiles = _load_active_configuration(args.api_url)
        profile_root = profile_directory(data_dir, args.profile_id, profiles)
        target_db_path = profile_root / "omi.sqlite3"
        audio_dir = profile_root / "audio"
        if target_db_path.resolve() == args.source_db.resolve():
            raise ImportRefused("source archive copy must differ from target profile")
        expected = ImportExpectations(
            args.recording_id, args.result_version, args.expected_wav_sha256,
            args.expected_service_image_id, args.expected_homecompute_source_sha,
            args.expected_model_sha256, args.expected_model_snapshot, args.expected_runtime,
            args.expected_runtime_commit, args.expected_alignment_model,
        )
        variant = read_qualified_variant(args.source_db, expected)
        with closing(_connect_target(target_db_path)) as db:
            recording = load_recording(_LocalAudioReader(audio_dir), expected.recording_id, db)
            validate_variant(variant, recording, settings, expected)
            wav_sha, wav_bytes = verify_original_audio(
                db, recording, audio_dir, settings, expected.original_wav_sha256,
            )
            disposition, _ = target_variant_disposition(db, variant)
            if not args.apply:
                receipt = _receipt("dry_run_validated", variant, wav_sha, wav_bytes)
                receipt["target_disposition"] = disposition
                print(json.dumps(receipt, sort_keys=True))
                return 0
        with closing(_connect_target(target_db_path)) as db:
            receipt = publish_variant(db, audio_dir, variant, settings, expected)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except ImportRefused as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
