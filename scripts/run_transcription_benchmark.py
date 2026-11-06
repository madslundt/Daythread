"""Run resumable transcription comparisons on explicit local fixtures, without an archive."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omi_local.domain import stamp, utc_now
from omi_local.inference import Inference
from omi_local.settings import Settings
from scripts.evaluate_audio import Hypothesis, RunMetadata, read_audio, read_cases, read_json

VARIANTS = {"daythread-vad": True, "daythread-full-audio": False}
_SETTINGS = {
    "stt_enabled", "stt_chunk_seconds", "stt_filter_speech", "stt_preserve_quiet",
    "stt_max_gain_db", "stt_boundary_search_seconds", "stt_enhancement_mode",
    "stt_enhancement_timeout_seconds", "stt_segment_timestamps", "alignment_enabled",
    "alignment_timeout_seconds", "alignment_language", "inference_timeout_seconds",
}


def digest(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(serialized).hexdigest()


def load_settings(
    data_dir: Path, *, env_file: Path | None = None, settings_file: Path | None = None,
) -> Settings:
    """Read only explicitly selected configuration; no live archive or implicit .env access."""
    settings = Settings(data_dir=data_dir, worker_enabled=False, _env_file=env_file)
    if settings_file is not None:
        payload, _ = read_json(settings_file)
        overrides = payload.get("overrides") if isinstance(payload, dict) else None
        if not isinstance(overrides, dict):
            raise ValueError("Settings file must contain an overrides object")
        selected = {
            key: value for key, value in overrides.items()
            if key in Settings.model_fields and (
                key.startswith(("stt_", "alignment_"))
                or key in {"inference_timeout_seconds", "inference_token"}
            )
        }
        settings = Settings.model_validate(settings.model_dump() | selected)
    return settings


def implementation_digest() -> str:
    root = Path(__file__).resolve().parents[1] / "src" / "omi_local"
    names = (
        "transcription.py", "transcription_filter.py", "audio.py", "voice_activity.py",
        "stt_result.py", "speech_processing.py", "alignment_audio.py", "settings.py",
        "transcription_vocabulary.py", "stt_languages.py", "inference.py", "inference_errors.py",
    )
    source = hashlib.sha256(Path(__file__).read_bytes())
    for name in names:
        source.update(name.encode())
        source.update((root / name).read_bytes())
    for helper in ("transcription_benchmark_evidence.py", "check_transcription_repeatability.py"):
        source.update(helper.encode())
        source.update(Path(__file__).with_name(helper).read_bytes())
    return source.hexdigest()


def configuration_identity(settings: Settings, revision: str, implementation: str) -> dict:
    """Fingerprint effective requests without retaining endpoints, vocabulary or credentials."""
    routes = {}
    for language in sorted({"auto", *settings.stt_routes}):
        route = settings.stt_route(language)
        routes[language] = {
            "model": route.model, "endpoint_sha256": digest(route.base_url),
            "vocabulary_mode": route.vocabulary_mode,
        }
    return {
        "settings": {key: getattr(settings, key) for key in sorted(_SETTINGS)},
        "routes": routes, "vocabulary_sha256": digest(settings.stt_vocabulary),
        "alignment_endpoint_sha256": digest(settings.alignment_base_url),
        "model_revision": revision, "implementation_sha256": implementation,
    }


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Benchmark output directories cannot be symbolic links")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, payload: dict) -> None:
    if path.is_symlink():
        raise ValueError("Benchmark outputs cannot be symbolic links")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.chmod(output.name, 0o600)
            json.dump(payload, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cached_result(path: Path, cache_key: str) -> dict | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        record, _ = read_json(path)
        if record.get("cache_key") != cache_key or record.get("status") != "complete":
            return None
        Hypothesis.model_validate({
            "id": record["id"], "audio_sha256": record["audio_sha256"],
            "text": record["result"]["text"], "latency_ms": record["latency_ms"],
        })
        return record
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def publish(output_dir: Path, runs: list[dict], outcomes: list[dict], total: int) -> dict:
    saved = []
    for run in runs:
        saved.append({"id": run["id"], "metadata": run["metadata"], "cases": [
            {"id": item["id"], "audio_sha256": item["audio_sha256"],
             "text": item["result"]["text"], "latency_ms": item["latency_ms"]}
            for item in outcomes if item["run_id"] == run["id"] and item["status"] == "complete"
        ]})
    succeeded = sum(item["status"] == "complete" for item in outcomes)
    status = {
        "schema_version": 1, "complete": succeeded == total, "expected": total,
        "succeeded": succeeded, "failed": len(outcomes) - succeeded,
        "pending": total - len(outcomes), "cached": sum(item["cached"] for item in outcomes),
        "cases": [{key: item[key] for key in (
            "run_id", "id", "audio_sha256", "status", "cached", "latency_ms",
        )} | ({"error_type": item["error_type"]} if "error_type" in item else {})
                  for item in outcomes],
    }
    write_private(output_dir / "hypotheses.json", {"schema_version": 1, "runs": saved})
    write_private(output_dir / "status.json", status)
    return status


async def run_benchmark(
    manifest: Path, output_dir: Path, settings: Settings, revision: str,
    *, client: httpx.AsyncClient | None = None,
    on_progress: Callable[[dict], None] | None = None,
    fresh: bool = False, expected_headers: dict | None = None,
    variants: tuple[str, ...] | None = None, capture_requests: bool = False,
) -> dict:
    from scripts.check_transcription_repeatability import (
        observed_headers,
        provenance_pins,
        reported_cache_hit,
    )

    pins = provenance_pins(expected_headers)
    selected = tuple(VARIANTS) if variants is None else variants
    if (not selected or len(selected) != len(set(selected))
            or any(v not in VARIANTS for v in selected)):
        raise ValueError("Select unique known benchmark variants")
    if fresh and (output_dir.exists() or output_dir.is_symlink()):
        raise ValueError("Fresh benchmark runs require a new output directory")
    cases, manifest_digest = read_cases(manifest)
    private_directory(output_dir)
    private_directory(output_dir / "results")
    code_digest = implementation_digest()
    outcomes, runs = [], []
    owned_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False)
    attempts, requests = [], []

    async def capture_request(request):
        if capture_requests and request.url.path.endswith("/audio/transcriptions"):
            from scripts.transcription_benchmark_evidence import request_evidence

            evidence = request_evidence(
                await request.aread(), request.headers.get("content-type", ""),
            )
            evidence["index"] = len(requests) + 1
            requests.append(evidence)
            request.extensions["benchmark_evidence"] = evidence

    async def capture_provenance(response):
        authorization = response.request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization else None
        headers = observed_headers(response.headers, token)
        attempt = {
            "http_status": response.status_code, "response_headers": headers,
            "provenance_verified": bool(pins) and all(headers.get(k) == v for k, v in pins.items()),
        }
        attempts.append(attempt)
        if capture_requests and "benchmark_evidence" in response.request.extensions:
            attempt["request"] = response.request.extensions["benchmark_evidence"]
            if 200 <= response.status_code < 300:
                from scripts.transcription_benchmark_evidence import response_evidence

                attempt["response"] = response_evidence(await response.aread())

    client.event_hooks["request"].append(capture_request)
    client.event_hooks["response"].append(capture_provenance)
    original_cache_control = client.headers.get("Cache-Control")
    if fresh:
        client.headers["Cache-Control"] = "no-cache, no-store"
    try:
        with tempfile.TemporaryDirectory(prefix="daythread-benchmark-") as temporary:
            for run_id in selected:
                config = settings.model_copy(update={
                    "data_dir": Path(temporary), "worker_enabled": False,
                    "stt_filter_speech": VARIANTS[run_id],
                })
                identity = configuration_identity(config, revision, code_digest)
                identity["capture_request_evidence"] = capture_requests
                if pins:
                    identity["expected_provenance"] = pins
                configuration_hash = digest(identity)
                model_label = re.sub(r"[^A-Za-z0-9._/+@-]", "-", config.stt_route("auto").model)
                metadata = RunMetadata(
                    provider="daythread", model=model_label[:200], revision=revision,
                    configuration=f"sha256-{configuration_hash}",
                ).model_dump()
                runs.append({"id": run_id, "metadata": metadata, "settings": config,
                             "identity": identity, "configuration_sha256": configuration_hash})
            total = len(cases) * len(runs)
            publish(output_dir, runs, outcomes, total)
            # Interleave variants per fixture to reduce effects from changing server load.
            for case in cases:
                audio = read_audio(manifest, case)
                audio_digest = hashlib.sha256(audio).hexdigest()
                for run in runs:
                    cache_key = digest({"audio_sha256": audio_digest, "case_id": case.id,
                                        "configuration_sha256": run["configuration_sha256"]})
                    result_file = output_dir / "results" / f"{cache_key}.json"
                    record = None if fresh else cached_result(result_file, cache_key)
                    if record is not None:
                        outcomes.append({**record, "cached": True})
                        publish(output_dir, runs, outcomes, total)
                        if on_progress:
                            on_progress(outcomes[-1])
                        continue
                    record = {
                        "schema_version": 1, "cache_key": cache_key, "run_id": run["id"],
                        "id": case.id, "audio_sha256": audio_digest,
                        "manifest_sha256": manifest_digest, "configuration": run["identity"],
                        "started_at": stamp(utc_now()), "status": "failed", "cached": False,
                    }
                    started = perf_counter()
                    attempts.clear()
                    requests.clear()
                    try:
                        result = await Inference(run["settings"], client).transcribe_result(audio)
                        record["result"] = result
                        record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
                        Hypothesis.model_validate({
                            "id": case.id, "audio_sha256": audio_digest, "text": result["text"],
                            "latency_ms": record["latency_ms"],
                        })
                        if pins and any(not attempt["provenance_verified"] for attempt in attempts):
                            raise ValueError("Provider provenance did not match pinned runtime")
                        if fresh and any(reported_cache_hit(attempt["response_headers"])
                                         for attempt in attempts):
                            raise ValueError("Fresh benchmark received a reported cache hit")
                        record["status"] = "complete"
                    except Exception as exc:
                        record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
                        record["error_type"] = type(exc).__name__
                    record["provider_attempts"] = list(attempts)
                    if capture_requests:
                        record["provider_requests"] = list(requests)
                    record["inference_status"] = ("performed" if attempts else (
                        "skipped" if record["status"] == "complete" else "no_response"
                    ))
                    record["provenance_status"] = (
                        "verified" if attempts and all(a["provenance_verified"] for a in attempts)
                        else "unverified" if attempts else "not_applicable"
                    )
                    record["finished_at"] = stamp(utc_now())
                    write_private(result_file, record)
                    outcomes.append(record)
                    publish(output_dir, runs, outcomes, total)
                    if on_progress:
                        on_progress(record)
            return publish(output_dir, runs, outcomes, total)
    finally:
        client.event_hooks["request"].remove(capture_request)
        client.event_hooks["response"].remove(capture_provenance)
        if fresh:
            if original_cache_control is None:
                client.headers.pop("Cache-Control", None)
            else:
                client.headers["Cache-Control"] = original_cache_control
        if owned_client:
            await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--revision", required=True, help="Model revision or explicit unknown")
    parser.add_argument("--env-file", type=Path, help="Explicit env file; otherwise OMI_* only")
    parser.add_argument("--settings-file", type=Path, help="Explicit private settings overrides")
    parser.add_argument("--fresh", action="store_true", help="New output; no cached reuse")
    parser.add_argument("--expected-provenance", type=Path, help="Pinned model/runtime header JSON")
    parser.add_argument("--variant", action="append", choices=tuple(VARIANTS),
                        help="Run only selected variants; repeat to include both")
    parser.add_argument("--capture-requests", action="store_true",
                        help="Save private per-request audio hashes, text and reported language")
    parser.add_argument("--live", action="store_true", help="Authorize configured model calls")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly authorize speech-model calls")
    try:
        with tempfile.TemporaryDirectory(prefix="daythread-benchmark-config-") as temporary:
            settings = load_settings(Path(temporary), env_file=args.env_file,
                                     settings_file=args.settings_file)
            def progress(item):
                print(json.dumps({key: item[key] for key in (
                    "run_id", "id", "status", "cached", "latency_ms",
                )}), file=sys.stderr, flush=True)

            pins = read_json(args.expected_provenance)[0] if args.expected_provenance else None
            status = asyncio.run(run_benchmark(
                args.manifest, args.output_dir, settings, args.revision,
                on_progress=progress, fresh=args.fresh, expected_headers=pins,
                variants=tuple(args.variant) if args.variant else None,
                capture_requests=args.capture_requests,
            ))
        print(json.dumps({key: status[key] for key in (
            "complete", "expected", "succeeded", "failed", "pending", "cached",
        )}))
        raise SystemExit(0 if status["complete"] else 1)
    except Exception as exc:
        raise SystemExit(f"Transcription benchmark failed ({type(exc).__name__})") from None
