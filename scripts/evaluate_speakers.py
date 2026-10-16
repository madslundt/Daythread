"""Calibrate voice suggestions on actual held-out local WAVs, then evaluate once.

Example: uv run python scripts/evaluate_speakers.py --archive data/omi.sqlite3 \
  --manifest recordings.json --service-url http://127.0.0.1:8790 --output report.json

The manifest is a JSON list of {path, conversation_id, split, speakers}, where
split is calibration or test and speakers maps EVERY runtime cluster label to a
confirmed profile ID or null (unknown/mixed). Paths are relative to the manifest.
Labels can be obtained from an earlier local detection run. This script always
uploads the actual WAV files again: supplied embeddings/results are rejected.
Protect the resulting report. Synthetic/mocked audio is not production evidence.
"""

import argparse
import hashlib
import io
import ipaddress
import json
import math
import socket
import sqlite3
import sys
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from omi_local.voice_matching import (
    build_report,
    canonical_hash,
    gallery_fingerprint,
    validate_model,
    validate_vector,
    verify_partitions,
)
from omi_local.voice_store import turn_signature

MAX_AUDIO_BYTES = 28800 * 32000 + 65536


def inspect_audio(path: Path) -> tuple[bytes, dict]:
    if not path.is_file() or not 44 <= path.stat().st_size <= MAX_AUDIO_BYTES:
        raise ValueError(f"Missing, empty or oversized WAV: {path}")
    data = path.read_bytes()
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError("Audio must be 16 kHz mono PCM16 WAV")
            frames = audio.getnframes()
            if not 0 < frames <= 28800 * 16000:
                raise ValueError("WAV duration must be positive and at most eight hours")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("Truncated WAV audio")
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Invalid WAV: {path}") from exc
    return data, {"audio_sha256": hashlib.sha256(data).hexdigest(),
                  "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                  "duration_seconds": frames / 16000}


def read_gallery(archive: Path) -> tuple[list[dict], dict, list[str]]:
    """Read only retained examples whose original evidence still matches on disk."""
    archive = archive.resolve(strict=True)
    gallery, pcm_hashes = [], set()
    with sqlite3.connect(archive.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        rows = db.execute("SELECT e.* FROM voice_examples e JOIN voice_profiles p "
                          "ON p.id=e.profile_id ORDER BY e.id").fetchall()
        for row in rows:
            example = dict(row)
            for field in ("embedding", "model", "source_ids", "source_digests"):
                example[field] = json.loads(example[field])
            if (isinstance(example["seconds"], bool)
                    or not isinstance(example["seconds"], (int, float))
                    or not math.isfinite(example["seconds"]) or example["seconds"] < 10):
                raise ValueError("Gallery example has insufficient clean speech")
            if not example["source_ids"] or len(example["source_ids"]) != len(
                example["source_digests"],
            ):
                raise ValueError("Gallery example has incomplete source provenance")
            valid = True
            example_pcm = set()
            for source_id, digest in zip(example["source_ids"], example["source_digests"],
                                         strict=True):
                source = db.execute("SELECT * FROM audio WHERE id=?", (source_id,)).fetchone()
                if (source is None or source["state"] == "expired" or source["digest"] != digest
                        or ("audio_available" in source.keys() and not source["audio_available"])):
                    valid = False
                    break
                source_path = Path(source["path"])
                if not source_path.is_absolute():
                    source_path = archive.parent / source_path
                if not source_path.is_file():
                    valid = False
                    break
                _, audio = inspect_audio(source_path)
                if audio["audio_sha256"] != digest:
                    valid = False
                    break
                example_pcm.add(audio["pcm_sha256"])
            if not valid:
                # Missing/expired/changed evidence never becomes recognition support.
                continue
            scope = db.execute("SELECT * FROM speaker_scopes WHERE scope_key=?",
                               (example["scope_key"],)).fetchone()
            if (scope is None or scope["fingerprint"] != example["fingerprint"]
                    or turn_signature(json.loads(scope["payload"])) != example["turn_signature"]):
                continue
            validate_model(example["model"])
            validate_vector(example["embedding"], example["model"]["dimension"])
            example["conversation_id"] = example["scope_key"]
            gallery.append(example)
            pcm_hashes.update(example_pcm)
    if not gallery:
        raise ValueError("No retained confirmed gallery examples are available")
    model = gallery[0]["model"]
    # Refuse mixed model/dimension galleries instead of silently dropping examples.
    gallery_fingerprint(gallery, model)
    return gallery, model, sorted(pcm_hashes)


def local_service_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname \
            or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a local http(s) service URL without credentials or a query")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 80,
                                       type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Local speaker service address cannot be resolved") from exc
    if not addresses or any(not (ipaddress.ip_address(address[4][0]).is_private
                                or ipaddress.ip_address(address[4][0]).is_loopback)
                            for address in addresses):
        raise ValueError("Speaker evaluation must use a local/private service address")
    return value.rstrip("/")


def evaluate(archive: Path, manifest_path: Path, service_url: str,
             client: httpx.Client | None = None) -> dict:
    gallery, model, gallery_pcm = read_gallery(archive)
    initial_gallery = gallery_fingerprint(gallery, model)
    service_url = local_service_url(service_url)
    entries = json.loads(manifest_path.read_text())
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest must be a nonempty list of held-out WAV recordings")
    manifest, audio_files = [], []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path", "conversation_id", "split", "speakers",
        }:
            raise ValueError("Each manifest entry needs path, conversation_id, split and speakers")
        if not isinstance(entry["path"], str):
            raise ValueError("Manifest audio path must be a string")
        path = (manifest_path.resolve().parent / entry["path"]).resolve(strict=True)
        data, metadata = inspect_audio(path)
        manifest.append({**entry, "audio_sha256": metadata["audio_sha256"],
                         "pcm_sha256": metadata["pcm_sha256"]})
        audio_files.append((path, metadata))
        del data
    # Check separation before sending any held-out audio for inference.
    verify_partitions(manifest, gallery, gallery_pcm)
    owned_client = client is None
    client = client or httpx.Client(timeout=3600, follow_redirects=False, trust_env=False)
    recordings = []
    try:
        for entry, (path, metadata) in zip(manifest, audio_files, strict=True):
            data, current_audio = inspect_audio(path)
            if current_audio != metadata:
                raise ValueError("Held-out WAV changed before inference")
            response = client.post(service_url + "/diarize", files={
                "file": (path.name, data, "audio/wav"),
            }, timeout=3600)
            response.raise_for_status()
            if len(response.content) > 16_000_000:
                raise ValueError("Oversized diarization response")
            detected = response.json()
            recordings.append({"conversation_id": entry["conversation_id"],
                               "split": entry["split"], **metadata, "response": detected})
    finally:
        if owned_client:
            client.close()
    report = build_report(manifest, recordings, gallery, model, gallery_pcm)
    # A moving archive/gallery cannot produce a usable report from an older snapshot.
    current, current_model, _ = read_gallery(archive)
    if current_model != model or gallery_fingerprint(current, model) != initial_gallery:
        raise ValueError("Voice gallery changed during evaluation; run evaluation again")
    for path, metadata in audio_files:
        if not path.is_file():
            raise ValueError("Held-out WAV disappeared during evaluation")
        with path.open("rb") as audio:
            if hashlib.file_digest(audio, "sha256").hexdigest() != metadata["audio_sha256"]:
                raise ValueError("Held-out WAV changed during evaluation")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".json" or args.output.resolve() in {
        args.archive.resolve(), args.manifest.resolve(),
    }:
        parser.error("Output must be a separate .json report file")
    try:
        report = evaluate(args.archive, args.manifest, args.service_url)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, httpx.HTTPError) as exc:
        report = {"schema_version": 1, "passed": False, "error": str(exc),
                  "interpretation": "No valid production accuracy evidence was produced."}
        report["report_sha256"] = canonical_hash(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=args.output.parent, prefix=".voice-evaluation-",
                                     suffix=".json", delete=False) as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
        temporary = Path(stream.name)
    temporary.replace(args.output)
    if not report["passed"]:
        print(report.get("error", "Held-out evaluation did not meet suggestion criteria"),
              file=sys.stderr)
        return 1
    metrics = report["metrics"]
    print(f"Observed precision {metrics['precision']:.3f}; known-speaker coverage "
          f"{metrics['known_coverage']:.3f}; accepted test suggestions {metrics['accepted']}. "
          "These results do not guarantee future accuracy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
