"""Bounded private diagnostics must not affect or expose model request secrets."""

import hashlib
import json

import httpx
import pytest

from scripts.transcription_benchmark_evidence import request_evidence, response_evidence


def evidence(prompt: str = "private vocabulary") -> dict:
    request = httpx.Request("POST", "http://local/audio/transcriptions", data={
        "model": "whisper", "response_format": "json", "prompt": prompt,
    }, files={"file": ("private-file-name.wav", b"exact audio", "audio/wav")},
        headers={"Authorization": "Bearer private-secret"})
    return request_evidence(request.read(), request.headers["content-type"])


def test_fingerprints_actual_audio_and_hashes_vocabulary_without_secrets():
    record = evidence()
    assert record["audio_sha256"] == hashlib.sha256(b"exact audio").hexdigest()
    assert record["fields"]["prompt"] == {
        "bytes": 18, "sha256": hashlib.sha256(b"private vocabulary").hexdigest(),
    }
    assert "private" not in json.dumps(record)
    assert record["semantic_sha256"] == evidence()["semantic_sha256"]
    assert record["semantic_sha256"] != evidence("different")["semantic_sha256"]


def test_response_keeps_text_and_reported_language_but_not_arbitrary_metadata():
    raw = json.dumps({"text": " Hej. ", "language": "danish", "debug": "secret"}).encode()
    record = response_evidence(raw)
    assert record["text"] == " Hej. "
    assert record["reported_language"] == "danish"
    assert record["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "secret" not in json.dumps(record)


@pytest.mark.parametrize("content", [b"not-json", b"[]", b'{"text":12}',
                                     json.dumps({"text": "x" * 20001}).encode()])
def test_invalid_or_unbounded_success_body_is_not_accepted(content):
    with pytest.raises(ValueError):
        response_evidence(content)


def test_nonmultipart_request_rejected():
    with pytest.raises(ValueError, match="multipart"):
        request_evidence(b"secret", "application/json")
