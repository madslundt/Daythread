"""Guarded local import of a previously qualified whole-recording variant.

This is an operator tool, not an API. Source archives are opened read-only; the target
profile receives one READY variant without changing child audio, transcripts or selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .domain import stamp
from .merged_recordings import Recording, load_recording
from .recording_transcription_client import _spool_wav
from .recording_transcription_store import (
    pipeline_fingerprint,
    recording_source_map,
    recording_transcription_result_version,
    source_fingerprint,
)
from .settings import Settings


class ImportRefused(ValueError):
    """The candidate or current target does not meet the explicit import contract."""


@dataclass(frozen=True)
class ImportExpectations:
    recording_id: str
    result_version: str
    original_wav_sha256: str
    service_image_id: str
    homecompute_source_sha: str
    model_sha256: str
    model_snapshot: str
    runtime: str
    runtime_commit: str
    alignment_model: str


@dataclass(frozen=True)
class QualifiedVariant:
    recording_id: str
    input_fingerprint: str
    pipeline_fingerprint: str
    result_version: str
    transcript: str
    native_segments: list[dict]
    word_segments: list[dict]
    provenance: dict
    source_map: list[dict]
    alignment_status: str
    alignment_model: str
    total_parts: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> QualifiedVariant:
        try:
            return cls(
                recording_id=row["recording_id"],
                input_fingerprint=row["input_fingerprint"],
                pipeline_fingerprint=row["pipeline_fingerprint"],
                result_version=row["result_version"],
                transcript=row["transcript"],
                native_segments=json.loads(row["native_segments"]),
                word_segments=json.loads(row["word_segments"]),
                provenance=json.loads(row["provenance"]),
                source_map=json.loads(row["source_map"]),
                alignment_status=row["alignment_status"],
                alignment_model=row["alignment_model"],
                total_parts=row["total_parts"],
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ImportRefused("qualified row is malformed") from exc


def validate_variant(
    variant: QualifiedVariant,
    recording: Recording,
    settings: Settings,
    expectations: ImportExpectations,
) -> None:
    if (variant.recording_id != expectations.recording_id
            or recording.id != expectations.recording_id):
        raise ImportRefused("recording identity mismatch")
    if variant.result_version != expectations.result_version:
        raise ImportRefused("result version mismatch")
    if not recording.complete or not recording.audio_available:
        raise ImportRefused("target recording is incomplete or has unavailable audio")
    if any(row.get("state") != "done" for row in recording.rows):
        raise ImportRefused("target recording has unfinished members")
    if any(row.get("transcription_outcome") == "corrected" for row in recording.rows):
        raise ImportRefused("target recording has corrected member transcripts")
    current_source = source_fingerprint(recording)
    current_pipeline = pipeline_fingerprint(settings)
    if variant.input_fingerprint != current_source:
        raise ImportRefused("source fingerprint differs from qualified variant")
    if variant.pipeline_fingerprint != current_pipeline:
        raise ImportRefused("pipeline fingerprint differs from qualified variant")
    expected_map = recording_source_map(recording)
    if variant.source_map != expected_map:
        raise ImportRefused("source map differs from current recording members")
    if variant.total_parts != len(recording.rows):
        raise ImportRefused("source part count differs")
    _validate_provenance(variant, expectations)
    _validate_segments(variant, sum(recording.frames) / 16000)
    if recording_transcription_result_version(
        current_source, current_pipeline, variant.transcript, variant.native_segments,
        variant.word_segments, variant.provenance,
    ) != variant.result_version:
        raise ImportRefused("result version does not match stored content")


def _validate_provenance(variant: QualifiedVariant, expected: ImportExpectations) -> None:
    provenance = variant.provenance
    if not isinstance(provenance, dict):
        raise ImportRefused("qualified provenance is malformed")
    checks = {
        "model-sha256": expected.model_sha256,
        "model-snapshot": expected.model_snapshot,
        "runtime": expected.runtime,
        "runtime-commit": expected.runtime_commit,
        "homecompute-revision": expected.homecompute_source_sha,
        "image-id": expected.service_image_id,
    }
    if any(not expected_value or provenance.get(key) != expected_value
           for key, expected_value in checks.items()):
        raise ImportRefused("qualified service provenance mismatch")
    if (not isinstance(variant.alignment_status, str)
            or variant.alignment_status not in {"aligned", "partial"}):
        raise ImportRefused("qualified variant has no usable alignment")
    if variant.alignment_model != expected.alignment_model:
        raise ImportRefused("alignment model revision mismatch")


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _validate_segments(variant: QualifiedVariant, duration: float) -> None:
    if not isinstance(variant.transcript, str) or not variant.transcript:
        raise ImportRefused("canonical transcript is empty")
    if not isinstance(variant.native_segments, list) or not isinstance(variant.word_segments, list):
        raise ImportRefused("timing segments are malformed")
    prior_end = 0.0
    prior_native_right = 0
    for item in variant.native_segments:
        if (not isinstance(item, dict) or item.get("kind") != "segment"
                or not isinstance(item.get("text"), str)):
            raise ImportRefused("native timing segment is malformed")
        start, end = item.get("start"), item.get("end")
        if (not _number(start) or not _number(end) or start < prior_end
                or end <= start or end > duration):
            raise ImportRefused("native timing bounds are invalid")
        prior_end = end
        left, right = item.get("start_offset"), item.get("end_offset")
        if left is None and right is None:
            continue
        if (type(left) is not int or type(right) is not int or not 0 <= left < right
                or left < prior_native_right or right > len(variant.transcript)
                or variant.transcript[left:right] != item["text"]):
            raise ImportRefused("native segment text anchors are invalid")
        prior_native_right = right

    prior_end = 0.0
    prior_right = 0
    for word in variant.word_segments:
        if not isinstance(word, dict) or word.get("kind") != "word":
            raise ImportRefused("aligned fragment kind is invalid")
        left, right, text = word.get("start_offset"), word.get("end_offset"), word.get("text")
        start, end = word.get("start"), word.get("end")
        if (type(left) is not int or type(right) is not int or not 0 <= left < right
                or right > len(variant.transcript) or not isinstance(text, str) or not text
                or variant.transcript[left:right] != text or left < prior_right):
            raise ImportRefused("word text anchors are invalid")
        if (not _number(start) or not _number(end) or start < prior_end
                or end <= start or end > duration):
            raise ImportRefused("word timing bounds are invalid")
        prior_right, prior_end = right, end


def read_qualified_variant(source_db: Path, expected: ImportExpectations) -> QualifiedVariant:
    if not source_db.is_file():
        raise ImportRefused("source archive database is unavailable")
    uri = source_db.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM recording_transcription_jobs WHERE recording_id=? "
                "AND result_version=? AND state='ready'",
                (expected.recording_id, expected.result_version),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ImportRefused("source archive could not be read") from exc
    if row is None:
        raise ImportRefused("qualified READY row not found")
    return QualifiedVariant.from_row(dict(row))


class _LocalAudioReader:
    """Read local WAV objects without constructing Store or mutating the archive."""

    def __init__(self, audio_dir: Path):
        self.audio_dir = audio_dir

    def read_audio(self, audio_id: str) -> bytes:
        path = self.audio_dir / f"{UUID(audio_id)}.wav"
        return path.read_bytes()


def verify_original_audio(
    db: sqlite3.Connection,
    recording: Recording,
    audio_dir: Path,
    settings: Settings,
    expected_sha256: str,
) -> tuple[str, int]:
    if not expected_sha256 or len(expected_sha256) != 64:
        raise ImportRefused("expected original WAV SHA-256 is required")
    if any(str(row.get("path", "")).startswith("s3://") for row in recording.rows):
        raise ImportRefused("local importer requires locally stored target audio")
    temp_root = Path(tempfile.mkdtemp(prefix="daythread-transcript-import-"))
    os.chmod(temp_root, 0o700)
    spool = temp_root / "original.wav"
    try:
        _spool_wav(
            _LocalAudioReader(audio_dir), recording, spool,
            settings.recording_transcription_max_bytes,
            settings.recording_transcription_max_seconds,
            lambda _done, _total: None,
        )
        with spool.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected_sha256:
            raise ImportRefused("target original WAV SHA-256 mismatch")
        return actual, spool.stat().st_size
    finally:
        spool.unlink(missing_ok=True)
        temp_root.rmdir()


def publish_variant(
    target_db: sqlite3.Connection,
    target_audio_dir: Path,
    variant: QualifiedVariant,
    settings: Settings,
    expectations: ImportExpectations,
) -> dict:
    """Revalidate and publish under an immediate write lock; selection is untouched."""
    target_db.row_factory = sqlite3.Row
    target_db.execute("BEGIN IMMEDIATE")
    try:
        recording = load_recording(_LocalAudioReader(target_audio_dir), expectations.recording_id,
                                   target_db)
        validate_variant(variant, recording, settings, expectations)
        wav_sha, wav_bytes = verify_original_audio(
            target_db, recording, target_audio_dir, settings, expectations.original_wav_sha256,
        )
        existing = target_db.execute(
            "SELECT * FROM recording_transcription_jobs WHERE recording_id=? "
            "AND input_fingerprint=? AND pipeline_fingerprint=?",
            (variant.recording_id, variant.input_fingerprint, variant.pipeline_fingerprint),
        ).fetchone()
        if existing and existing["result_version"]:
            if not _same_variant(dict(existing), variant):
                raise ImportRefused("target already contains a different result version")
            target_db.rollback()
            return _receipt("already_present", variant, wav_sha, wav_bytes)
        if existing and existing["state"] == "processing":
            raise ImportRefused("target variant is currently processing")
        now = stamp(datetime.now(UTC))
        if existing:
            if existing["state"] not in {"queued", "error", "stale", "blocked"}:
                raise ImportRefused("target has a conflicting in-progress state")
            target_db.execute(
                "UPDATE recording_transcription_jobs SET state='ready',phase='ready',"
                "completed_parts=total_parts,error_code=NULL,retry_at=NULL,transcript=?,"
                "native_segments=?,word_segments=?,provenance=?,source_map=?,alignment_status=?,"
                "alignment_model=?,result_version=?,spool_path=NULL,updated_at=? "
                "WHERE recording_id=? AND input_fingerprint=? AND pipeline_fingerprint=? "
                "AND result_version IS NULL AND state IN ('queued','error','stale','blocked')",
                _write_values(variant, now),
            )
            if target_db.execute("SELECT changes()").fetchone()[0] != 1:
                raise ImportRefused("target job changed before publication")
        else:
            target_db.execute(
                "INSERT INTO recording_transcription_jobs "
                "(recording_id,input_fingerprint,pipeline_fingerprint,state,phase,completed_parts,"
                "total_parts,transcript,native_segments,word_segments,provenance,source_map,"
                "alignment_status,alignment_model,result_version,created_at,updated_at) "
                "VALUES(?,?,?,'ready','ready',?,?,?,?,?,?,?,?,?,?,?,?)",
                (variant.recording_id, variant.input_fingerprint, variant.pipeline_fingerprint,
                 variant.total_parts, variant.total_parts, variant.transcript,
                 _json(variant.native_segments), _json(variant.word_segments),
                 _json(variant.provenance), _json(variant.source_map), variant.alignment_status,
                 variant.alignment_model, variant.result_version, now, now),
            )
        target_db.commit()
        return _receipt("imported", variant, wav_sha, wav_bytes)
    except Exception:
        target_db.rollback()
        raise


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_values(variant: QualifiedVariant, now: str) -> tuple:
    return (
        variant.transcript, _json(variant.native_segments), _json(variant.word_segments),
        _json(variant.provenance), _json(variant.source_map), variant.alignment_status,
        variant.alignment_model, variant.result_version, now, variant.recording_id,
        variant.input_fingerprint, variant.pipeline_fingerprint,
    )


def _same_variant(row: dict, variant: QualifiedVariant) -> bool:
    try:
        return (
            row.get("state") == "ready"
            and row.get("total_parts") == variant.total_parts
            and row.get("completed_parts") == variant.total_parts
            and row.get("result_version") == variant.result_version
            and row.get("transcript") == variant.transcript
            and json.loads(row.get("native_segments") or "[]") == variant.native_segments
            and json.loads(row.get("word_segments") or "[]") == variant.word_segments
            and json.loads(row.get("provenance") or "{}") == variant.provenance
            and json.loads(row.get("source_map") or "[]") == variant.source_map
            and row.get("alignment_status") == variant.alignment_status
            and row.get("alignment_model") == variant.alignment_model
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ImportRefused("existing target variant is malformed") from exc


def _receipt(action: str, variant: QualifiedVariant, wav_sha: str, wav_bytes: int) -> dict:
    return {
        "action": action,
        "recording_id": variant.recording_id,
        "result_version": variant.result_version,
        "input_fingerprint": variant.input_fingerprint,
        "pipeline_fingerprint": variant.pipeline_fingerprint,
        "part_count": variant.total_parts,
        "aligned_fragment_count": len(variant.word_segments),
        "alignment_status": variant.alignment_status,
        "audio_sha256": wav_sha,
        "audio_bytes": wav_bytes,
        "selection_changed": False,
        "child_sources_changed": False,
    }


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
        with _connect_target(target_db_path) as db:
            recording = load_recording(_LocalAudioReader(audio_dir), expected.recording_id, db)
            validate_variant(variant, recording, settings, expected)
            wav_sha, wav_bytes = verify_original_audio(
                db, recording, audio_dir, settings, expected.original_wav_sha256,
            )
            if not args.apply:
                print(json.dumps(_receipt("dry_run_validated", variant, wav_sha, wav_bytes),
                                 sort_keys=True))
                return 0
        with _connect_target(target_db_path) as db:
            receipt = publish_variant(db, audio_dir, variant, settings, expected)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except ImportRefused as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
