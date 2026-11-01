"""Vocabulary hints reach only explicitly compatible routes and fence audio reuse."""

import asyncio
import io
import json
import wave
from email.parser import BytesParser
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from omi_local.api import create_app
from omi_local.inference import Inference
from omi_local.settings import Settings
from omi_local.source_matching import recognition_contract
from omi_local.worker import process_one


def audio():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\xff\x01" * 16000)
    return output.getvalue()


def fields(request):
    message = BytesParser().parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
    )
    return {part.get_param("name", header="content-disposition"):
            part.get_payload(decode=True).decode(errors="replace")
            for part in message.get_payload()}


@pytest.mark.parametrize("mode,expected", [
    ("off", {}), ("prompt", {"prompt": "Søren, Daythread"}),
    ("keyterms", {"keyterms": '["Søren", "Daythread"]'}),
])
def test_vocabulary_uses_only_selected_multipart_format(tmp_path, mode, expected):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_filter_speech=False,
                        stt_enhancement_mode="off", stt_vocabulary=["Søren", "Daythread"],
                        stt_vocabulary_mode=mode)
    seen = []

    def respond(request):
        seen.append(fields(request))
        return httpx.Response(200, json={"text": "Søren uses Daythread."})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            assert await Inference(settings, http).transcribe(audio()) == "Søren uses Daythread."

    asyncio.run(run())
    assert len(seen) == 1
    assert {key: value for key, value in seen[0].items()
            if key in {"prompt", "keyterms"}} == expected


def test_specialist_can_disable_hints_even_when_detector_supports_them(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_filter_speech=False,
                        stt_enhancement_mode="off", stt_vocabulary=["Søren"],
                        stt_vocabulary_mode="prompt", stt_model="detector",
                        stt_routes={"da": {"model": "danish", "vocabulary_mode": "off"}})
    seen = []

    def respond(request):
        seen.append(fields(request))
        return httpx.Response(200, json={"text": "Hej Søren", "language": "da"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            await Inference(settings, http).transcribe(audio())

    asyncio.run(run())
    assert [item["model"] for item in seen] == ["detector", "danish"]
    assert seen[0]["prompt"] == "Søren"
    assert "prompt" not in seen[1] and "keyterms" not in seen[1]


def test_vocabulary_normalizes_spelling_whitespace_and_duplicates(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None,
                        stt_vocabulary=[" Søren  Lundt ", "søren Lundt", "", "Cafe\u0301"])
    assert settings.stt_vocabulary == ["Søren Lundt", "Café"]


@pytest.mark.parametrize("terms", ["Søren", [1], ["x"] * 101, ["x" * 81],
                                  ["x" * 79 + str(i) for i in range(26)], ["bad\x00term"]])
def test_invalid_vocabulary_is_rejected(tmp_path, terms):
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, _env_file=None, stt_vocabulary=terms)


def test_active_vocabulary_and_route_mode_invalidate_recognition_contract(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    before = recognition_contract(settings)
    settings.stt_vocabulary = ["Søren"]
    assert recognition_contract(settings) == before
    settings.stt_vocabulary_mode = "prompt"
    prompted = recognition_contract(settings)
    assert prompted != before
    settings.stt_vocabulary = ["Maria"]
    assert recognition_contract(settings) != prompted
    settings.stt_vocabulary_mode = "keyterms"
    assert recognition_contract(settings) != prompted
    routed = Settings.model_validate(settings.model_dump() | {
        "stt_routes": {"da": {"vocabulary_mode": "off"}},
    })
    assert recognition_contract(routed) != recognition_contract(settings)


def test_configuration_save_reload_keeps_terms_modes_and_route_override(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    with TestClient(create_app(settings)) as client:
        config = client.get("/v1/configuration").json()
        assert next(field for field in config["fields"]
                    if field["key"] == "stt_vocabulary")["kind"] == "terms"
        response = client.patch("/v1/configuration", json={
            "revision": config["revision"], "changes": {
                "stt_vocabulary": [" Søren ", "søren", "København"],
                "stt_vocabulary_mode": "prompt",
                "stt_routes": {"da": {"model": "da", "vocabulary_mode": "off"}},
            },
        })
        assert response.status_code == 200, response.text
        assert response.json()["pending_restart"] == []
    with TestClient(create_app(settings)) as client:
        values = client.get("/v1/configuration").json()["values"]
        assert values["stt_vocabulary"] == ["Søren", "København"]
        assert values["stt_vocabulary_mode"] == "prompt"
        assert values["stt_routes"]["da"]["vocabulary_mode"] == "off"
        contents = json.loads((tmp_path / ".private-settings.json").read_text())
        assert contents["overrides"]["stt_vocabulary"] == ["Søren", "København"]


def test_reupload_reuses_same_hints_but_recognizes_again_after_vocabulary_change(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False,
                        stt_filter_speech=False, stt_enhancement_mode="off",
                        stt_vocabulary=["Søren"], stt_vocabulary_mode="prompt")
    seen = []

    def respond(request):
        seen.append(fields(request)["prompt"])
        return httpx.Response(200, json={"text": "Søren has confirmed the project meeting."})

    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        with_http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        memory.inference = Inference(memory.settings, with_http)

        def upload_and_process():
            response = client.put(f"/v1/audio/{uuid4()}", params={
                "device_id": "manual-web", "started_at": "2026-10-15T12:00:00Z",
                "source_kind": "manual_upload", "source_id": "meeting.wav",
            }, content=audio(), headers={"Content-Type": "audio/wav"})
            assert response.status_code == 200, response.text
            assert asyncio.run(process_one(memory))
            assert memory.store.get_audio(response.json()["id"])["state"] == "done"

        try:
            upload_and_process()
            upload_and_process()
            assert seen == ["Søren"]
            memory.settings.stt_vocabulary = ["Maria"]
            upload_and_process()
            assert seen == ["Søren", "Maria"]
        finally:
            asyncio.run(with_http.aclose())
