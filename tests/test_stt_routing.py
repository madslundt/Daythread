"""Language routing against controlled local STT responses; no real model calls."""

import asyncio
import io
import json
import wave
from datetime import UTC, datetime
from email.parser import BytesParser
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from omi_local.api import create_app
from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings, public_settings
from omi_local.worker import process_one


@pytest.fixture
def anyio_backend():
    return "asyncio"


def config(tmp_path, **kwargs):
    return Settings(
        data_dir=tmp_path, worker_enabled=False, _env_file=None, stt_max_gain_db=0, **kwargs
    )


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
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message.get_payload()
    }


def test_nested_language_routes_inherit_and_keep_credentials_private(tmp_path, monkeypatch):
    monkeypatch.setenv("OMI_STT_ROUTES__DA__MODEL", "danish")
    monkeypatch.setenv("OMI_STT_ROUTES__EN__BASE_URL", "http://english.local/v1/")
    monkeypatch.setenv("OMI_STT_ROUTES__EN__TOKEN", "english-secret")
    settings = config(tmp_path, inference_token="shared-secret")
    assert settings.stt_route("da-DK").model == "danish"
    assert settings.stt_route("DA").base_url == settings.stt_base_url
    assert settings.stt_route("da").token.get_secret_value() == "shared-secret"
    assert settings.stt_route("en").model == settings.stt_model
    assert settings.stt_route("en").base_url == "http://english.local/v1"
    assert settings.stt_route("en").token.get_secret_value() == "english-secret"
    assert settings.stt_route("fr").model == settings.stt_model
    assert "english-secret" not in repr(settings)
    assert "stt_routes" not in public_settings(settings)
    monkeypatch.setenv("OMI_STT_ROUTES__EN__TOKEN", "")
    assert config(tmp_path).stt_route("en").token.get_secret_value() == ""


@pytest.mark.parametrize(
    "routes",
    [
        {"en_US": {"model": "x"}},
        {"DA": {}, "da": {}},
        {"da": {"base_url": "http://example.com/v1"}},
        {"da": {"base_url": "http://user:secret@localhost/v1"}},
        {"da": {"model": " "}},
        {"da": {"modle": "x"}},
    ],
)
def test_invalid_stt_routes_are_rejected(tmp_path, routes):
    with pytest.raises(ValidationError):
        config(tmp_path, stt_routes=routes)


@pytest.mark.anyio
async def test_legacy_language_argument_cannot_force_transcription_or_routing(tmp_path):
    settings = config(
        tmp_path,
        language="DA-dk",
        stt_model="multilingual",
        inference_token="shared",
        stt_routes={
            "da": {"model": "danish"},
            "da-DK": {"model": "regional", "base_url": "http://danish.local/v1", "token": ""},
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        form = fields(request)
        assert str(request.url) == f"{settings.stt_base_url}/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer shared"
        assert form["model"] == b"multilingual"
        assert "language" not in form
        assert form["response_format"] == b"verbose_json"
        assert form["file"] == audio()
        return httpx.Response(200, json={"text": "Hej. Hello.", "language": "mixed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Inference(settings, client).transcribe(audio()) == "Hej. Hello."
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("detected", ["da", "da-DK", "Danish", " DA "])
async def test_auto_detection_routes_codes_or_configured_provider_names(tmp_path, detected):
    settings = config(
        tmp_path,
        stt_model="multilingual",
        stt_segment_timestamps=True,
        inference_token="detector-secret",
        stt_routes={
            key: {"model": "danish", "base_url": "http://danish.local/v1", "token": "specialist"}
            for key in ("da", "danish")
        },
    )
    calls = []
    segments = [{"start": 0.1, "end": 0.9, "text": "Hej"}]

    def handler(request):
        calls.append(request)
        form = fields(request)
        assert form["file"] == audio()
        assert form["response_format"] == b"verbose_json"
        if len(calls) == 1:
            assert form["model"] == b"multilingual"
            assert "language" not in form
            assert request.headers["authorization"] == "Bearer detector-secret"
            return httpx.Response(200, json={"text": "Preliminary", "language": detected})
        assert form["model"] == b"danish"
        assert str(request.url) == "http://danish.local/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer specialist"
        assert "language" not in form
        return httpx.Response(200, json={"text": "Hej", "language": "en", "segments": segments})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Inference(settings, client).transcribe_result(audio())
    assert result["text"] == "Hej"
    assert result["segments"] == segments
    assert len(calls) == 2  # Never route recursively on the specialist's response.


@pytest.mark.anyio
@pytest.mark.parametrize("detected", [None, "fr", "auto", ["da", "en"], "mixed"])
async def test_unknown_or_missing_detection_keeps_multilingual_result(tmp_path, detected):
    settings = config(tmp_path, stt_routes={"da": {"model": "danish"}})
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "Mixed speech", "language": detected})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Inference(settings, client).transcribe(audio()) == "Mixed speech"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_same_route_is_not_transcribed_twice_and_auto_route_can_override_default(tmp_path):
    settings = config(
        tmp_path,
        stt_routes={"auto": {"model": "multi"}, "en": {"model": "multi"}},
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert fields(request)["model"] == b"multi"
        return httpx.Response(200, json={"text": "Hello", "language": "en"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Inference(settings, client).transcribe(audio()) == "Hello"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_failed_specialist_does_not_publish_detector_text(tmp_path):
    settings = config(tmp_path, stt_routes={"da": {"model": "danish"}})

    def handler(request):
        if fields(request)["model"] == b"danish":
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"text": "Preliminary", "language": "da"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        with pytest.raises(httpx.HTTPStatusError):
            await inference.transcribe(audio())
        assert inference.health["stt"]["state"] == "unavailable"


@pytest.mark.anyio
async def test_detection_and_specialist_share_total_deadline(tmp_path):
    settings = config(
        tmp_path, inference_timeout_seconds=1, stt_routes={"da": {"model": "danish"}}
    )
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.6)
        return httpx.Response(200, json={"text": "Hej", "language": "da"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        with pytest.raises(InferenceError, match="deadline"):
            await inference.transcribe(audio())
        assert inference.health["stt"]["state"] == "unavailable"
    assert len(calls) == 2


def test_legacy_saved_and_environment_languages_do_not_force_worker_after_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OMI_LANGUAGE", "da")
    settings = config(tmp_path, stt_model="multilingual", stt_routes={"da": {"model": "danish"}})
    (tmp_path / ".private-settings.json").write_text(
        json.dumps({"version": 1, "overrides": {"language": "da", "stt_chunk_seconds": 20}})
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.memory.store.save_settings({"language": "da"})
        assert client.patch("/v1/settings", json={"language": "da"}).status_code == 422
        view = client.get("/v1/configuration").json()
        assert "language" not in view["values"]
        assert not any(field["key"] == "language" for field in view["fields"])
        assert client.patch(
            "/v1/configuration", json={"revision": view["revision"], "changes": {"language": "da"}}
        ).status_code == 422
    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        key = uuid4()
        memory.store.ingest(key, "omi", datetime.now(UTC), audio(), 1)

        def handler(request):
            assert fields(request)["model"] == b"multilingual"
            assert "language" not in fields(request)
            return httpx.Response(200, json={"text": "Hej. Hello.", "language": "mixed"})

        async def transcribe():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                memory.inference = Inference(memory.settings, http)
                assert await process_one(memory)

        asyncio.run(transcribe())
        assert memory.store.get_audio(str(key))["text"] == "Hej. Hello."
        assert memory.settings.stt_chunk_seconds == 20
        assert "language" not in client.get("/v1/settings").json()
        assert "stt_routes" not in client.get("/v1/settings").json()
        assert "stt_routes" not in json.dumps(memory.store.runtime_settings())
