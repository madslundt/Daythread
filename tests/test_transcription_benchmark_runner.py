"""Live benchmark orchestration with mocked transport and private temporary output."""

import hashlib
import io
import json
import stat
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import pytest

from omi_local.settings import Settings
from scripts.evaluate_audio import evaluate_offline


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def benchmark(tmp_path, monkeypatch):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\0" * 16000)
    (tmp_path / "fixture.wav").write_bytes(output.getvalue())
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([{
        "id": "mixed", "audio": "fixture.wav", "category": "clean", "language": "da-en",
        "reference": "Vi review Friday", "reference_provenance": {
            "kind": "human", "source": "reviewed", "revision": "v1",
        },
    }]))

    def filter_audio(data, *, enabled, activity, chunk_seconds, boundary_search_seconds):
        return {"spans": [(0, data)], "status": "filtered" if enabled else "disabled",
                "original_seconds": 1, "retained_seconds": 1, "detected_speech_seconds": 1}

    monkeypatch.setattr("omi_local.transcription.filter_transcription_audio", filter_audio)
    settings = Settings(_env_file=None, data_dir=tmp_path / "untouched-archive",
                        stt_base_url="http://127.0.0.1:9999/v1", stt_model="same-model",
                        stt_api_key="private-secret", stt_max_gain_db=0,
                        stt_enhancement_mode="off", stt_segment_timestamps=True)
    return manifest, tmp_path / "private-results", settings


def response(request):
    assert b'name="model"\r\n\r\nsame-model\r\n' in request.content
    assert b'name="language"' not in request.content
    return httpx.Response(200, json={
        "text": "Vi review Friday", "segments": [{"start": 0, "end": 1,
                                                   "text": "Vi review Friday"}],
    })


@pytest.mark.anyio
async def test_benchmark_preserves_outputs_hashes_and_resumes_without_model_calls(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    requests = []

    def handle(request):
        requests.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await run_benchmark(manifest, output_dir, settings, "model-v1", client=client)
        assert result["complete"] is True
        assert len(requests) == 2
        second = await run_benchmark(manifest, output_dir, settings, "model-v1", client=client)
        assert second["cached"] == 2
        assert len(requests) == 2
    report = evaluate_offline(manifest, output_dir / "hypotheses.json")
    assert report["groups"]["overall"]["daythread-vad"]["wer"] == 0
    assert report["groups"]["overall"]["daythread-full-audio"]["wer"] == 0
    records = [json.loads(path.read_text()) for path in (output_dir / "results").glob("*.json")]
    assert {record["result"]["processing"]["filtering"]["status"] for record in records} == {
        "filtered", "disabled",
    }
    assert all(record["result"]["segments"] for record in records)
    assert all(record["status"] == "complete" for record in records)
    assert not settings.data_dir.exists()
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    for path in output_dir.rglob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "private-secret" not in path.read_text()


@pytest.mark.anyio
async def test_failure_is_explicit_and_retried_while_success_is_reused(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("private-secret server detail", request=request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        first = await run_benchmark(manifest, output_dir, settings, "model-v1", client=client)
        assert first["complete"] is False
        assert first["failed"] == 1 and first["succeeded"] == 1
        with pytest.raises(ValueError):
            evaluate_offline(manifest, output_dir / "hypotheses.json")
        second = await run_benchmark(manifest, output_dir, settings, "model-v1", client=client)
    assert second["complete"] is True and second["cached"] == 1
    assert calls == 3
    assert "private-secret" not in (output_dir / "status.json").read_text()


@pytest.mark.anyio
async def test_changed_config_model_revision_and_audio_invalidate_cached_results(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    calls = []

    def handle(request):
        calls.append(request)
        return response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await run_benchmark(manifest, output_dir, settings, "model-v1", client=client)
        await run_benchmark(manifest, output_dir, settings, "model-v2", client=client)
        changed = settings.model_copy(update={"stt_max_gain_db": 1})
        await run_benchmark(manifest, output_dir, changed, "model-v2", client=client)
        audio = manifest.parent / "fixture.wav"
        data = audio.read_bytes()
        audio.write_bytes(data[:-2] + b"\x02\0")
        await run_benchmark(manifest, output_dir, changed, "model-v2", client=client)
    assert len(calls) == 8


def test_settings_loading_is_explicit_and_only_applies_transcription_overrides(tmp_path):
    from scripts.run_transcription_benchmark import load_settings

    env_file = tmp_path / "explicit.env"
    env_file.write_text("OMI_STT_MODEL=base-model\nOMI_STT_API_KEY=private-secret\n")
    private = tmp_path / "private.json"
    private.write_text(json.dumps({"overrides": {
        "stt_model": "same-model", "stt_chunk_seconds": 20,
        "data_dir": "/do-not-open", "worker_enabled": True,
    }}))
    config = load_settings(tmp_path / "isolated", env_file=env_file, settings_file=private)
    assert config.stt_model == "same-model"
    assert config.stt_chunk_seconds == 20
    assert config.stt_api_key.get_secret_value() == "private-secret"
    assert config.data_dir == tmp_path / "isolated"
    assert config.worker_enabled is False


def test_cli_requires_explicit_live_flag_before_reading_settings():
    result = subprocess.run([
        sys.executable, "scripts/run_transcription_benchmark.py", "missing.json",
        "--output-dir", "missing-results", "--revision", "v1",
    ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--live" in result.stderr


@pytest.mark.parametrize("helper", [
    "transcription_benchmark_evidence.py", "check_transcription_repeatability.py",
])
def test_changed_helper_invalidates_implementation_identity(monkeypatch, helper):
    from scripts.run_transcription_benchmark import implementation_digest

    before = implementation_digest()
    original_read = Path.read_bytes

    def changed_read(path):
        data = original_read(path)
        return data + b"\n# changed helper semantics\n" if path.name == helper else data

    monkeypatch.setattr(Path, "read_bytes", changed_read)
    assert implementation_digest() != before


@pytest.mark.anyio
async def test_fresh_runs_are_independent_and_capture_verified_provider_responses(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    pins = {"x-homecompute-stt-model-sha256": "a" * 64,
            "x-homecompute-stt-runtime-commit": "b" * 40}
    calls = []

    def handle(request):
        assert request.headers["cache-control"] == "no-cache, no-store"
        calls.append(request)
        result = response(request)
        result.headers.update(pins)
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        first = await run_benchmark(manifest, output_dir, settings, "v1", fresh=True,
                                    expected_headers=pins, client=client)
        second = await run_benchmark(manifest, output_dir.with_name("repeat-two"), settings,
                                     "v1", fresh=True, expected_headers=pins, client=client)
        with pytest.raises(ValueError, match="new output directory"):
            await run_benchmark(manifest, output_dir, settings, "v1", fresh=True, client=client)
        assert "cache-control" not in client.headers
        assert client.event_hooks["response"] == []
    assert first["cached"] == second["cached"] == 0
    assert len(calls) == 4
    for path in (output_dir / "results").glob("*.json"):
        record = json.loads(path.read_text())
        assert record["provider_attempts"][0]["provenance_verified"] is True
        headers = record["provider_attempts"][0]["response_headers"]
        assert all(headers[key] == value for key, value in pins.items())


@pytest.mark.anyio
async def test_benchmark_does_not_accept_text_from_a_different_pinned_runtime(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    pins = {"x-homecompute-stt-model-sha256": "a" * 64,
            "x-homecompute-stt-runtime-commit": "b" * 40}
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        report = await run_benchmark(manifest, output_dir, settings, "v1", fresh=True,
                                     expected_headers=pins, client=client)
    assert report["failed"] == 2
    assert report["complete"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("cache_header", [
    {"X-Cache": "HIT from speech-cache"}, {"Cache-Status": "speech-cache; hit; ttl=20"},
])
async def test_fresh_benchmark_rejects_other_reported_cache_hits(benchmark, cache_header):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark

    def handle(request):
        result = response(request)
        result.headers.update(cache_header)
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await run_benchmark(
            manifest, output_dir, settings, "v1", fresh=True, client=client,
        )
    assert report["failed"] == 2
    assert report["complete"] is False


@pytest.mark.anyio
async def test_intentional_silence_skip_is_explicit_not_verified_inference(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    audio = manifest.parent / "fixture.wav"
    data = audio.read_bytes()
    audio.write_bytes(data[:44] + b"\0" * (len(data) - 44))
    pins = {"x-homecompute-stt-model-sha256": "a" * 64,
            "x-homecompute-stt-runtime-commit": "b" * 40}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: pytest.fail("Digital silence must not invoke a provider"),
    )) as client:
        report = await run_benchmark(manifest, output_dir, settings, "v1", fresh=True,
                                     expected_headers=pins, client=client)
    assert report["complete"] is True
    for path in (output_dir / "results").glob("*.json"):
        record = json.loads(path.read_text())
        assert record["provider_attempts"] == []
        assert record["inference_status"] == "skipped"
        assert record["provenance_status"] == "not_applicable"


@pytest.mark.anyio
async def test_single_variant_evidence_preserves_json_fields_and_prepared_audio(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    settings = settings.model_copy(update={"stt_segment_timestamps": False})
    bodies = []

    def handle(request):
        bodies.append(request.content)
        assert b'name="response_format"\r\n\r\njson\r\n' in request.content
        assert b'name="language"' not in request.content
        return httpx.Response(200, json={"text": " Vi review Friday ", "debug": "private-secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await run_benchmark(
            manifest, output_dir, settings, "v1", client=client,
            variants=("daythread-full-audio",), capture_requests=True,
        )
        assert client.event_hooks["request"] == client.event_hooks["response"] == []
    assert report["expected"] == report["succeeded"] == 1
    record = json.loads(next((output_dir / "results").glob("*.json")).read_text())
    attempt = record["provider_attempts"][0]
    request = attempt["request"]
    assert request["body_sha256"] == hashlib.sha256(bodies[0]).hexdigest()
    assert request["audio_sha256"] == record["audio_sha256"]
    assert request["fields"] == {"model": "same-model", "response_format": "json"}
    assert record["provider_requests"] == [request]
    assert attempt["response"]["text"] == " Vi review Friday "
    assert attempt["response"]["reported_language"] is None
    assert "private-secret" not in json.dumps(record)


@pytest.mark.anyio
async def test_request_evidence_keeps_dispatch_without_response_and_never_error_body(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark

    def handle(request):
        raise httpx.ConnectError("private-secret failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report = await run_benchmark(manifest, output_dir, settings, "v1", client=client,
                                     capture_requests=True, variants=("daythread-full-audio",))
    assert report["failed"] == 1
    record = json.loads(next((output_dir / "results").glob("*.json")).read_text())
    assert len(record["provider_requests"]) == 1
    assert record["provider_attempts"] == []
    assert record["inference_status"] == "no_response"
    assert "private-secret" not in json.dumps(record)


@pytest.mark.anyio
async def test_request_evidence_does_not_save_failed_provider_payload(benchmark):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(400, json={"text": "private-secret error body"}),
    )) as client:
        report = await run_benchmark(manifest, output_dir, settings, "v1", client=client,
                                     capture_requests=True, variants=("daythread-full-audio",))
    assert report["failed"] == 1
    record = json.loads(next((output_dir / "results").glob("*.json")).read_text())
    assert len(record["provider_attempts"]) == len(record["provider_requests"]) == 1
    assert "response" not in record["provider_attempts"][0]
    assert "private-secret" not in json.dumps(record)


@pytest.mark.anyio
@pytest.mark.parametrize("variants", [(), ("unknown",), ("daythread-vad", "daythread-vad")])
async def test_invalid_variant_selection_fails_before_model_or_output(benchmark, variants):
    from scripts.run_transcription_benchmark import run_benchmark

    manifest, output_dir, settings = benchmark
    with pytest.raises(ValueError, match="unique known"):
        await run_benchmark(manifest, output_dir, settings, "v1", variants=variants)
    assert not output_dir.exists()
