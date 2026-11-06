"""Opt-in language hints and exact producer identities fence transcription reuse."""

import asyncio
import io
import wave
from email.parser import BytesParser

import httpx
import pytest
from pydantic import ValidationError
from recording_transcription_helpers import make_ready_variant, pinned_settings

from omi_local.inference import Inference
from omi_local.recording_transcription_client import DECODING_FIELDS, _request, _validate_response
from omi_local.recording_transcription_store import pipeline_fingerprint
from omi_local.recording_transcription_worker import discover
from omi_local.settings import Settings
from omi_local.source_matching import recognition_contract


def fields(request):
    message = BytesParser().parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode()
        + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content
    )
    return {part.get_param("name", header="content-disposition"):
            part.get_payload(decode=True) for part in message.get_payload()}


@pytest.mark.parametrize("value", ["da,en", ["english"], ["da", " DA "], ["zz"],
                                  ["en-US"], [""], [None], [1], ["auto"], ["en"] * 17])
def test_invalid_candidate_preferences_are_rejected(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stt_language_candidates=value)


def test_candidates_are_canonical_and_disabled_by_default():
    assert Settings(_env_file=None).stt_language_candidates == []
    settings = Settings(_env_file=None, stt_language_candidates=[" EN ", "da"])
    assert settings.stt_language_candidates == ["da", "en"]


@pytest.mark.parametrize("candidates,expected", [([], None), (["EN", " da "], b"da,en")])
def test_normal_requests_send_candidates_only_when_enabled(tmp_path, candidates, expected):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_language_candidates=candidates,
                        stt_filter_speech=False, stt_max_gain_db=0, stt_enhancement_mode="off",
                        stt_api_key="synthetic-token")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        audio.writeframes(b"\xff\x01" * 16000)
    seen = []

    def respond(request):
        seen.append(fields(request))
        assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json={"text": "English og dansk."})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await Inference(settings, client).transcribe(output.getvalue()) == (
                "English og dansk."
            )
    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].get("language_candidates") == expected
    assert "language" not in seen[0] and "prompt" not in seen[0]


def test_whole_recording_fields_are_request_local_and_keep_original_pcm(tmp_path, monkeypatch):
    path = tmp_path / "input.wav"
    path.write_bytes(b"synthetic-original-wav")
    settings = Settings(_env_file=None, stt_language_candidates=["en", "da"],
                        recording_transcription_api_key="synthetic-token")
    seen = []
    original_client = httpx.AsyncClient

    async def respond(request):
        await request.aread()
        seen.append(fields(request))
        assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json={"text": "Words.", "segments": []})

    monkeypatch.setattr("omi_local.recording_transcription_client.httpx.AsyncClient",
                        lambda **kwargs: original_client(
                            transport=httpx.MockTransport(respond), **kwargs))
    asyncio.run(_request(settings, path))
    settings.stt_language_candidates = []
    asyncio.run(_request(settings, path))
    assert seen[0].pop("language_candidates") == b"da,en"
    assert seen[0] == seen[1]
    assert seen[0]["file"] == path.read_bytes()
    assert seen[0]["language"] == b"auto" and seen[0]["translate"] == b"false"
    assert "language_candidates" not in DECODING_FIELDS


def test_effective_fields_and_producer_pins_fence_ready_reuse():
    settings = Settings(_env_file=None)
    baseline = pipeline_fingerprint(settings)
    settings.stt_language_candidates = ["da", "en"]
    candidate = pipeline_fingerprint(settings)
    assert candidate != baseline
    settings.recording_transcription_expected_homecompute_revision = "c" * 40
    revision = pipeline_fingerprint(settings)
    assert revision != candidate
    settings.recording_transcription_expected_image_id = "sha256:" + "d" * 64
    assert pipeline_fingerprint(settings) != revision
    settings.stt_language_candidates = []
    settings.recording_transcription_expected_homecompute_revision = ""
    settings.recording_transcription_expected_image_id = ""
    assert pipeline_fingerprint(settings) == baseline


def test_disabled_new_preferences_keep_the_published_legacy_pipeline_fingerprint():
    assert pipeline_fingerprint(Settings(_env_file=None)) == (
        "8b724756a0e4b68d68d8e83dd39fbee4dffcd347950b56caceca717efbb01d41"
    )


@pytest.mark.parametrize("name,value", [
    ("recording_transcription_expected_homecompute_revision", "short"),
    ("recording_transcription_expected_homecompute_revision", "A" * 40),
    ("recording_transcription_expected_image_id", "latest"),
    ("recording_transcription_expected_image_id", "d" * 64),
])
def test_invalid_configured_producer_identity_is_rejected(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_configuration_round_trip_exposes_validated_language_preference(app_recording):
    client, *_ = app_recording
    config = client.get("/v1/configuration").json()
    descriptor = next(field for field in config["fields"]
                      if field["key"] == "stt_language_candidates")
    assert descriptor["kind"] == "terms"
    changed = client.patch("/v1/configuration", json={
        "revision": config["revision"], "changes": {"stt_language_candidates": ["EN", "da"]},
    })
    assert changed.status_code == 200
    assert changed.json()["values"]["stt_language_candidates"] == ["da", "en"]
    assert changed.json()["pending_restart"] == []


@pytest.mark.parametrize("setting,header,pin", [
    ("recording_transcription_expected_homecompute_revision",
     "x-homecompute-stt-homecompute-revision", "c" * 40),
    ("recording_transcription_expected_image_id",
     "x-homecompute-stt-image-id", "sha256:" + "d" * 64),
])
def test_configured_producer_pin_is_required_and_compared_exactly(
    app_recording, setting, header, pin,
):
    client, *_ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    headers = {"x-homecompute-stt-model-sha256": "a" * 64,
               "x-homecompute-stt-model-snapshot": "model-r1",
               "x-homecompute-stt-runtime-commit": "1234567890ab",
               "x-homecompute-stt-runtime": "whisper.cpp"}
    payload = {"text": "Words.", "segments": [{"text": "Words.", "start": 0, "end": 1}]}
    assert _validate_response(headers, payload, memory.settings, 1)[0] == "Words."
    setattr(memory.settings, setting, pin)
    with pytest.raises(ValueError, match="provenance_mismatch"):
        _validate_response(headers, payload, memory.settings, 1)
    with pytest.raises(ValueError, match="provenance_mismatch"):
        _validate_response(headers | {header: pin.replace("c", "a").replace("d", "a")},
                           payload, memory.settings, 1)
    assert _validate_response(headers | {header: pin}, payload, memory.settings, 1)[0] == "Words."


def test_candidate_change_fences_normal_reupload_reuse():
    settings = Settings(_env_file=None)
    original = recognition_contract(settings)
    settings.stt_language_candidates = ["da", "en"]
    assert recognition_contract(settings) != original
    settings.stt_language_candidates = []
    assert recognition_contract(settings) == original


@pytest.mark.parametrize("corrected", [False, True])
def test_discovery_preserves_ready_variants_and_corrections_after_policy_change(
    app_recording, corrected,
):
    client, store, rid, ids, texts, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Original READY variant.")
    if corrected:
        store.correct_transcript(ids[0], "Human correction.")
        texts[0] = "Human correction."
    memory.settings.stt_language_candidates = ["da", "en"]
    memory.settings.worker_enabled = True
    assert discover(memory) == (0 if corrected else 1)
    assert discover(memory) == 0
    with store.connect() as db:
        saved = db.execute("SELECT state,transcript FROM recording_transcription_jobs "
                           "WHERE result_version=?", (version,)).fetchone()
        assert tuple(saved) == ("ready", "Original READY variant.")
    assert [store.get_audio(source_id)["text"] for source_id in ids] == texts
    historical = client.get(f"/v1/recordings/{rid}/transcriptions/{version}").json()
    assert historical["text"] == "Original READY variant." and historical["state"] == "stale"
