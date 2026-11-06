"""Repeatability must come from fresh identical requests, never cached or selected text."""

import io
import stat
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import pytest

MODEL_HEADER = "x-homecompute-stt-model-sha256"
RUNTIME_HEADER = "x-homecompute-stt-runtime-commit"
PINNED = {MODEL_HEADER: "a" * 64, RUNTIME_HEADER: "b" * 40}


@pytest.fixture
def prepared_audio(tmp_path):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\0" * 16000)
    path = tmp_path / "prepared.wav"
    path.write_bytes(buffer.getvalue())
    return path


def check(prepared_audio, handler, **kwargs):
    from scripts.check_transcription_repeatability import check_repeatability

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return check_repeatability(
            prepared_audio, prepared_audio.parent / "probe", "https://speech.local/inference",
            "whisper", repeats=3, expected_headers=PINNED, client=client, **kwargs,
        )


def test_identical_uncached_requests_with_verified_identity_pass(prepared_audio):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "Vi review på fredag."}, headers=PINNED)

    report = check(prepared_audio, handle, profile={"temperature": 0, "temperature_inc": 0})
    assert report["passed"] is True
    assert report["exact_text_equal"] is True
    assert report["normalized_words_equal"] is True
    assert report["provenance_verified"] is True
    assert len(requests) == 3
    assert len({request.content for request in requests}) == 1
    assert len({request.headers["content-type"] for request in requests}) == 1
    assert all(request.headers["cache-control"] == "no-cache, no-store" for request in requests)
    assert b'name="language"' not in requests[0].content
    root = prepared_audio.parent / "probe"
    assert len(list(root.glob("response-*.raw"))) == 3
    for path in root.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@pytest.mark.parametrize("texts,normalized_equal", [
    (["Friday", "Saturday", "Friday"], False),
    (["Friday!", "friday", "Friday!"], True),
    (["same", "same", "different"], False),
])
def test_any_text_difference_fails_even_if_words_normalize_the_same(
    prepared_audio, texts, normalized_equal,
):
    responses = iter(texts)
    report = check(prepared_audio, lambda _: httpx.Response(
        200, json={"text": next(responses)}, headers=PINNED,
    ))
    assert report["passed"] is False
    assert report["exact_text_equal"] is False
    assert report["normalized_words_equal"] is normalized_equal
    assert "text_changed" in report["failure_reasons"]


@pytest.mark.parametrize("problem", ["changed_identity", "missing_identity", "cache_hit", "http"])
def test_same_text_cannot_hide_runtime_mismatch_caching_or_failures(prepared_audio, problem):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        headers = dict(PINNED)
        status = 200
        if calls == 2:
            if problem == "changed_identity":
                headers[RUNTIME_HEADER] = "c" * 40
            elif problem == "missing_identity":
                headers.pop(MODEL_HEADER)
            elif problem == "cache_hit":
                headers["x-litellm-cache-hit"] = "True"
            else:
                status = 503
        return httpx.Response(status, json={"text": "Friday"}, headers=headers)

    report = check(prepared_audio, handle)
    assert report["passed"] is False
    assert calls == 3
    assert report["failure_reasons"]


def test_unpinned_identity_is_diagnostic_not_verified_success(prepared_audio):
    from scripts.check_transcription_repeatability import check_repeatability

    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"text": "Friday"}, headers=PINNED),
    )) as client:
        report = check_repeatability(
            prepared_audio, prepared_audio.parent / "probe", "https://speech.local/inference",
            "whisper", repeats=2, client=client,
        )
    assert report["exact_text_equal"] is True
    assert report["passed"] is False
    assert "provenance_unverified" in report["failure_reasons"]


def test_failure_bodies_and_authentication_never_reach_artifacts(prepared_audio):
    key = "private-authentication-value"

    def handle(request):
        assert request.headers["authorization"] == f"Bearer {key}"
        return httpx.Response(401, json={"error": key}, headers={"set-cookie": key})

    report = check(prepared_audio, handle, api_key=key)
    assert report["passed"] is False
    for path in (prepared_audio.parent / "probe").iterdir():
        assert key.encode() not in path.read_bytes()
    assert not list((prepared_audio.parent / "probe").glob("*.raw"))


def test_existing_output_is_rejected_instead_of_reusing_prior_responses(prepared_audio):
    directory = prepared_audio.parent / "probe"
    directory.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        check(prepared_audio, lambda _: pytest.fail("Must not send a request"))


@pytest.mark.parametrize("profile", [
    {"language": "da"}, {"translate": True}, {"api_key": "private"},
    {"temperature": float("nan")}, {"model": "different"},
])
def test_profile_cannot_force_language_translate_or_include_credentials(prepared_audio, profile):
    with pytest.raises(ValueError):
        check(prepared_audio, lambda _: pytest.fail("Must not send a request"), profile=profile)


def test_cli_requires_live_before_reading_private_inputs():
    result = subprocess.run([
        sys.executable, "scripts/check_transcription_repeatability.py", "missing.wav",
        "--endpoint", "https://speech.local/inference", "--model", "whisper",
        "--output-dir", "missing-output",
    ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--live" in result.stderr


@pytest.mark.parametrize("header,value", [
    ("x-cache", "HIT from speech-cache"), ("cache-status", "speech-cache; hit; ttl=20"),
    ("x-cache", "TCP_HIT"),
])
def test_gate_rejects_other_reported_cache_hits(prepared_audio, header, value):
    report = check(prepared_audio, lambda _: httpx.Response(
        200, json={"text": "Friday"}, headers=PINNED | {header: value},
    ))
    assert report["passed"] is False
    assert report["cache_hits"] == 3
    assert "cache_hit_observed" in report["failure_reasons"]
