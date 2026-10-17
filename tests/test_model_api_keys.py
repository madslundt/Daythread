"""Provider credentials and hosted endpoints use mock HTTP, never real services."""

import httpx
import pytest
from pydantic import ValidationError

from omi_local.inference import Inference
from omi_local.settings import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("provider,route", [("stt", "en"), ("llm", "chat")])
def test_environment_provider_keys_and_route_precedence(tmp_path, monkeypatch, provider, route):
    monkeypatch.setenv(f"OMI_{provider.upper()}_API_KEY", "provider-secret")
    settings = Settings(data_dir=tmp_path, _env_file=None, inference_token="shared-secret")
    resolve = getattr(settings, f"{provider}_route")
    assert resolve(route).token.get_secret_value() == "provider-secret"
    assert "provider-secret" not in repr(settings)
    for token, expected in [("route-secret", "route-secret"), ("", ""), (None, "provider-secret")]:
        configured = Settings.model_validate(
            settings.model_dump()
            | {
                f"{provider}_routes": {route: {"token": token}},
            }
        )
        assert getattr(configured, f"{provider}_route")(route).token.get_secret_value() == expected
    monkeypatch.setenv(f"OMI_{provider.upper()}_API_KEY", "")
    assert Settings(_env_file=None).model_api_key(provider).get_secret_value() == ""


@pytest.mark.parametrize("field", ["stt_base_url", "llm_base_url", "embedding_base_url"])
def test_hosted_https_and_local_http_endpoints_are_supported(field):
    for endpoint in (
        "https://models.example.com/v1/",
        "http://localhost:4000/v1",
        "http://192.168.1.20/v1",
        "http://gateway:4000/v1",
    ):
        settings = Settings(_env_file=None, **{field: endpoint})
        assert getattr(settings, field) == endpoint.rstrip("/")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://models.example.com/v1",
        "ftp://models.example.com/v1",
        "https://user:secret@models.example.com/v1",
        "https://models.example.com/v1?key=secret",
        "https://models.example.com/v1#secret",
        "https://models.example.com:bad/v1",
        "https://models.example.com/with space",
        "https://models.example.com/\n",
    ],
)
def test_unsafe_hosted_endpoints_are_rejected(endpoint):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url=endpoint)


def test_speaker_service_remains_local():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, diarization_base_url="https://models.example.com")


def test_hosted_route_endpoints_override_local_defaults():
    settings = Settings(
        _env_file=None,
        stt_routes={"en": {"base_url": "https://speech.example.com/v1/", "token": "speech"}},
        llm_routes={"chat": {"base_url": "https://chat.example.com/v1/", "token": "chat"}},
    )
    assert settings.stt_route("en").base_url == "https://speech.example.com/v1"
    assert settings.llm_route("chat").base_url == "https://chat.example.com/v1"


@pytest.mark.anyio
async def test_actual_inference_sends_separate_provider_keys(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        _env_file=None,
        inference_token="shared-secret",
        stt_api_key="speech-secret",
        llm_api_key="language-secret",
        stt_base_url="https://speech.example.com/v1",
        llm_base_url="https://chat.example.com/v1",
    )
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "", "choices": [{"message": {"content": "OK"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        inference = Inference(settings, client)
        await inference._transcription(b"audio", settings.stt_route("auto"), settings)
        await inference.generate("task", "evidence")
    assert [(request.url.host, request.headers["authorization"]) for request in calls] == [
        ("speech.example.com", "Bearer speech-secret"),
        ("chat.example.com", "Bearer language-secret"),
    ]
