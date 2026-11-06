"""Gate fresh STT repeats on identical audio/body bytes and pinned response provenance."""

import argparse
import hashlib
import json
import math
import os
import re
import ssl
import sys
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omi_local.domain import stamp, utc_now, wav_duration
from omi_local.settings import inference_endpoint
from scripts.evaluate_audio import read_json, words
from scripts.run_transcription_benchmark import write_private

MAX_BYTES = 4_000_000
MODEL_SHA = "x-homecompute-stt-model-sha256"
RUNTIME_COMMIT = "x-homecompute-stt-runtime-commit"
IMAGE_ID = "x-homecompute-stt-image-id"
PROVENANCE_HEADERS = {
    MODEL_SHA: r"[0-9a-f]{64}", RUNTIME_COMMIT: r"[0-9a-f]{7,40}",
    IMAGE_ID: r"sha256:[0-9a-f]{64}",
    "x-homecompute-stt-model-snapshot": r"[A-Za-z0-9._/+@:-]{1,200}",
    "x-homecompute-stt-runtime": r"[A-Za-z0-9._/+@:-]{1,200}",
    "x-homecompute-stt-homecompute-revision": r"[0-9a-f]{40}",
}
CACHE_HEADERS = {"x-litellm-cache-hit", "x-cache", "cache-status"}
PROFILE_FIELDS = {
    "temperature", "temperature_inc", "beam_size", "best_of", "max_context", "max_len",
    "carry_initial_prompt", "no_context", "seed", "threads", "processors", "no_timestamps",
    "language", "translate",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def profile_fields(profile: dict | None) -> dict[str, str]:
    profile = {} if profile is None else profile
    if not isinstance(profile, dict) or profile.keys() - PROFILE_FIELDS:
        raise ValueError("Profile may contain only supported non-secret decoder fields")
    result = {}
    for key, value in profile.items():
        if type(value) not in (str, int, float, bool) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError("Profile values must be finite scalars")
        result[key] = str(value).lower() if type(value) is bool else str(value)
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,200}", result[key]):
            raise ValueError("Invalid profile scalar")
    if result.get("language", "auto") != "auto" or result.get("translate", "false") != "false":
        raise ValueError("Repeatability checks must not force a language or translation")
    return result


def provenance_pins(headers: dict | None) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValueError("Expected provenance must map response header names to pinned values")
    pins = {}
    for key, value in headers.items():
        if (not isinstance(key, str) or key.lower() not in PROVENANCE_HEADERS
                or not isinstance(value, str)
                or not re.fullmatch(PROVENANCE_HEADERS[key.lower()], value)):
            raise ValueError("Invalid expected provenance header")
        if key.lower() in pins:
            raise ValueError("Duplicate provenance header")
        pins[key.lower()] = value
    if MODEL_SHA not in pins or not ({RUNTIME_COMMIT, IMAGE_ID} & pins.keys()):
        raise ValueError("Pin model SHA256 and runtime commit or image ID")
    return pins


def multipart(fields: dict[str, str], audio: bytes) -> tuple[bytes, str]:
    boundary = "daythread-repeat-" + sha(audio)[:32]
    body = bytearray()
    for name, value in sorted(fields.items()):
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                    '\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                'filename="prepared.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    body.extend(audio)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), "multipart/form-data; boundary=" + boundary


def observed_headers(headers: httpx.Headers, api_key: str | None) -> dict[str, str]:
    observed = {}
    for key in PROVENANCE_HEADERS.keys() | CACHE_HEADERS | {"content-type", "x-litellm-model-id"}:
        value = headers.get(key)
        if value is None:
            continue
        pattern = PROVENANCE_HEADERS.get(key, r"[ -~]{1,512}")
        valid = re.fullmatch(pattern, value) and not (api_key and api_key in value)
        observed[key] = value if valid else "<invalid>"
    return observed


def reported_cache_hit(headers: dict[str, str]) -> bool:
    return any(re.search(r"(?:^|[\W_])(?:true|1|hit)(?:$|[\W_])", headers.get(key, ""), re.I)
               for key in CACHE_HEADERS)


def save_bytes(path: Path, data: bytes) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(data)


def check_repeatability(
    audio_path: Path, output_dir: Path, endpoint: str, model: str, *, repeats: int = 3,
    profile: dict | None = None, expected_headers: dict | None = None,
    ca_file: Path | None = None, api_key: str | None = None, timeout: float = 180,
    client: httpx.Client | None = None, on_progress: Callable[[dict], None] | None = None,
) -> dict:
    endpoint = inference_endpoint(endpoint)
    if type(repeats) is not int or not 2 <= repeats <= 20:
        raise ValueError("Use between 2 and 20 independent requests")
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 1 <= timeout <= 1800:
        raise ValueError("Timeout must be between 1 and 1800 seconds")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._/+@:-]{1,200}", model):
        raise ValueError("Use a non-secret model identifier")
    fields = {"model": model, "response_format": "json", **profile_fields(profile)}
    if api_key and api_key in json.dumps(fields):
        raise ValueError("Authentication data must not appear in decoder fields")
    pins = provenance_pins(expected_headers)
    with audio_path.open("rb") as source:
        audio = source.read(MAX_BYTES + 1)
    if len(audio) > MAX_BYTES:
        raise ValueError("Prepared WAV exceeds size limit")
    duration = wav_duration(audio)
    body, content_type = multipart(fields, audio)
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("Choose a fresh private output directory; cached repeats are not evidence")
    output_dir.mkdir(mode=0o700, parents=True)
    plan = {
        "schema_version": 1, "audio_sha256": sha(audio), "duration_seconds": duration,
        "request_body_sha256": sha(body), "request_fields": fields, "repeats": repeats,
        "endpoint_sha256": sha(endpoint.encode()), "expected_provenance": pins,
        "cache_control": "no-cache, no-store", "started_at": stamp(utc_now()),
    }
    write_private(output_dir / "request.json", plan)
    save_bytes(output_dir / "prepared.wav", audio)
    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    owned_client = client is None
    client = client or httpx.Client(verify=context, timeout=timeout, follow_redirects=False)
    records = []
    headers = {"Content-Type": content_type, "Cache-Control": "no-cache, no-store"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        for index in range(1, repeats + 1):
            record = {"repeat": index, "status": "failed", "audio_sha256": sha(audio),
                      "request_body_sha256": sha(body), "started_at": stamp(utc_now())}
            started = perf_counter()
            try:
                with client.stream("POST", endpoint, content=body, headers=headers,
                                   timeout=timeout, follow_redirects=False) as response:
                    record["http_status"] = response.status_code
                    record["response_headers"] = observed_headers(response.headers, api_key)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            raise ValueError("Response exceeds size limit")
                    record["response_sha256"] = sha(raw)
                    response.raise_for_status()
                if api_key and api_key.encode() in raw:
                    raise ValueError("Response unexpectedly contains authentication data")
                save_bytes(output_dir / f"response-{index:02}.raw", raw)
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                    raise ValueError("Expected a JSON transcription text field")
                text = payload["text"]
                record.update(status="complete", exact_text_sha256=sha(text.encode()),
                              normalized_words_sha256=sha(" ".join(words(text)).encode()))
            except Exception as exc:
                record["error_type"] = type(exc).__name__
            record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
            write_private(output_dir / f"response-{index:02}.json", record)
            records.append(record)
            if on_progress:
                on_progress({key: record[key] for key in ("repeat", "status", "latency_ms")})
    finally:
        if owned_client:
            client.close()
    complete = [record for record in records if record["status"] == "complete"]
    all_complete = len(complete) == repeats
    exact = all_complete and len({row["exact_text_sha256"] for row in complete}) == 1
    normalized = all_complete and len({row["normalized_words_sha256"] for row in complete}) == 1
    verified = bool(pins) and all_complete and all(
        all(row.get("response_headers", {}).get(key) == value for key, value in pins.items())
        for row in records
    )
    identities = [{key: row.get("response_headers", {}).get(key) for key in PROVENANCE_HEADERS}
                  for row in records]
    consistent = all_complete and len({json.dumps(identity, sort_keys=True)
                                      for identity in identities}) == 1
    cache_hits = sum(reported_cache_hit(row.get("response_headers", {})) for row in records)
    reasons = []
    for problem, condition in (
        ("request_failed", not all_complete), ("text_changed", all_complete and not exact),
        ("words_changed", all_complete and not normalized), ("provenance_unverified", not verified),
        ("provenance_changed", not consistent), ("cache_hit_observed", cache_hits > 0),
    ):
        if condition:
            reasons.append(problem)
    report = {
        "schema_version": 1, "passed": not reasons, "repeats": repeats, "completed": len(complete),
        "exact_text_equal": exact, "normalized_words_equal": normalized,
        "provenance_verified": verified, "provenance_consistent": consistent,
        "cache_hits": cache_hits, "failure_reasons": reasons,
        "audio_sha256": sha(audio), "request_body_sha256": sha(body), "responses": records,
        "notice": "Fresh calls only; no response selection or client cache. Passing establishes "
        "repeatability only for these requests and pinned response identities, not accuracy "
        "or determinism on every possible recording.",
    }
    write_private(output_dir / "summary.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Already prepared mono 16 kHz PCM16 WAV")
    parser.add_argument("--endpoint", required=True, help="Full transcription endpoint URL")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--profile", type=Path, help="Optional JSON decoder fields")
    parser.add_argument("--expected-provenance", type=Path, help="Pinned model/runtime header JSON")
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--api-key-env", help="Environment variable containing an optional API key")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to authorize fresh inference requests")
    try:
        profile = read_json(args.profile)[0] if args.profile else None
        expected = read_json(args.expected_provenance)[0] if args.expected_provenance else None
        api_key = os.environ[args.api_key_env] if args.api_key_env else None
        report = check_repeatability(
            args.audio, args.output_dir, args.endpoint, args.model, repeats=args.repeats,
            profile=profile, expected_headers=expected, ca_file=args.ca_file, api_key=api_key,
            timeout=args.timeout,
            on_progress=lambda row: print(json.dumps(row), file=sys.stderr, flush=True),
        )
        print(json.dumps({key: report[key] for key in (
            "passed", "repeats", "completed", "exact_text_equal", "normalized_words_equal",
            "provenance_verified", "failure_reasons",
        )}))
        raise SystemExit(0 if report["passed"] else 1)
    except Exception as exc:
        raise SystemExit(f"Repeatability check failed ({type(exc).__name__})") from None
