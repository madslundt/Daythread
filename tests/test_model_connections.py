"""Connection previews use only draft settings and synthetic input, never archive audio."""

import asyncio
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.model_connections import MAX_RESPONSE_BYTES
from omi_local.settings import Settings


@pytest.fixture
def connection(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "archive",
        worker_enabled=False,
        _env_file=None,
        inference_token="shared-secret",
        stt_model="default-stt",
        llm_model="default-llm",
        stt_routes={
            "auto": {"model": "automatic-model", "token": "automatic-secret"},
            "da": {"model": "danish", "token": "route-secret"},
        },
        llm_routes={"chat": {"model": "chat-model", "token": "chat-secret"}},
    )
    calls = []
    replies = [httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}]})]

    async def handle(request):
        await request.aread()
        calls.append(request)
        response = replies[-1]
        if isinstance(response, Exception):
            raise response
        return response

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True)
    try:
        with TestClient(create_app(settings)) as client:
            client.app.state.memory.inference.client = upstream
            yield client, settings, calls, replies
    finally:
        asyncio.run(upstream.aclose())


def post(client, action="models", provider="llm", **kwargs):
    return client.post(f"/v1/configuration/{action}", json={"provider": provider, **kwargs})


@pytest.mark.parametrize("provider", ["llm", "stt"])
def test_discovery_uses_draft_endpoint_shared_key_and_never_saves(connection, provider):
    client, settings, calls, replies = connection
    replies.append(
        httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-b"},
                    {"id": "model-a"},
                    {"id": "model-b"},
                ]
            },
        )
    )
    before = client.get("/v1/configuration").json()
    revision = client.app.state.memory.store.archive_revision()
    response = post(
        client,
        provider=provider,
        changes={
            f"{provider}_base_url": "http://draft.local:1234/v1/",
            "inference_token": "draft-secret",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"models": ["model-a", "model-b"]}
    assert response.headers["cache-control"] == "no-store"
    assert calls[-1].url == "http://draft.local:1234/v1/models"
    assert calls[-1].headers["authorization"] == "Bearer draft-secret"
    assert client.get("/v1/configuration").json() == before
    assert client.app.state.memory.store.archive_revision() == revision
    assert not (settings.data_dir / ".private-settings.json").exists()


@pytest.mark.parametrize("provider,route", [("stt", "en"), ("llm", "summary")])
@pytest.mark.parametrize("action", ["models", "test"])
@pytest.mark.parametrize(
    "key,expected",
    [("provider-secret", "Bearer provider-secret"), ("", None), (None, "Bearer shared-secret")],
)
def test_hosted_previews_use_provider_keys_without_saving(
    connection, provider, route, action, key, expected
):
    client, _, calls, replies = connection
    if action == "test":
        replies.append(
            httpx.Response(200, json={"text": "", "choices": [{"message": {"content": "OK"}}]})
        )
    before = client.get("/v1/configuration").json()
    for selected in (None, route):
        response = post(
            client,
            action,
            provider=provider,
            route=selected,
            changes={
                f"{provider}_base_url": "https://models.example.com/v1/",
                f"{provider}_api_key": key,
            },
        )
        assert response.status_code == 200
        if action == "test":
            assert response.json()["ok"]
        assert calls[-1].url.host == "models.example.com"
        assert calls[-1].headers.get("authorization") == expected
    assert client.get("/v1/configuration").json() == before


@pytest.mark.parametrize(
    "provider,route,secret",
    [
        ("stt", "da-DK", "route-secret"),
        ("llm", "chat", "chat-secret"),
    ],
)
def test_routes_follow_inheritance_and_preserve_stored_credentials(
    connection, provider, route, secret
):
    client, _, calls, _ = connection
    key = route.split("-")[0]
    result = post(
        client,
        provider=provider,
        route=route,
        changes={
            f"{provider}_routes": {key: {"model": None, "base_url": None}},
            f"{provider}_base_url": "http://inherited.local/v1",
            "inference_token": "new-shared-secret",
        },
    )
    assert result.status_code == 200
    assert calls[-1].url == "http://inherited.local/v1/models"
    assert calls[-1].headers["authorization"] == f"Bearer {secret}"


@pytest.mark.parametrize(
    "token,authorization",
    [
        ("replacement", "Bearer replacement"),
        (None, "Bearer shared-secret"),
        ("", None),
    ],
)
def test_draft_route_secret_can_be_replaced_inherited_or_disabled(connection, token, authorization):
    client, _, calls, _ = connection
    response = post(
        client,
        provider="stt",
        route="da",
        changes={
            "stt_routes": {"da": {"token": token, "base_url": "http://route.local/v1"}},
        },
    )
    assert response.status_code == 200
    assert calls[-1].url == "http://route.local/v1/models"
    assert calls[-1].headers.get("authorization") == authorization


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "other", "inference_token": "must-not-echo"},
        {"provider": "llm", "route": "invalid-private-route"},
        {"provider": "stt", "route": "../must-not-echo"},
        {"provider": "llm", "changes": {"data_dir": "must-not-echo"}},
        {"provider": "llm", "changes": {"llm_base_url": "http://example.com/v1"}},
        {"provider": "llm", "changes": {"inference_token": 12345}},
        {
            "provider": "llm",
            "changes": {
                "llm_routes": {
                    "chat": {
                        "base_url": "http://user:must-not-echo@localhost/v1",
                    }
                }
            },
        },
        {"provider": "llm", "changes": {"inference_token": "must-not-echo"}, "extra": True},
        ["must-not-echo"],
    ],
)
@pytest.mark.parametrize("action", ["models", "test"])
def test_invalid_previews_are_sanitized_without_network_or_persistence(connection, body, action):
    client, settings, calls, _ = connection
    before = client.get("/v1/configuration").json()
    response = client.post(f"/v1/configuration/{action}", json=body)
    assert response.status_code == 422
    assert "must-not-echo" not in response.text
    assert "invalid-private-route" not in response.text
    assert not calls
    assert client.get("/v1/configuration").json() == before
    assert not (settings.data_dir / ".private-settings.json").exists()


def test_invalid_json_is_sanitized(connection):
    client, _, calls, _ = connection
    response = client.post("/v1/configuration/models", content='{"inference_token":"secret"')
    assert response.status_code == 422
    assert "secret" not in response.text
    assert not calls


@pytest.mark.parametrize("action,status", [("models", 200), ("test", 422)])
def test_empty_custom_model_can_discover_options_but_cannot_be_tested(connection, action, status):
    client, _, calls, _ = connection
    response = post(client, action, changes={"llm_model": ""})
    assert response.status_code == status
    assert bool(calls) == (action == "models")


@pytest.mark.parametrize(
    "reply,status,message",
    [
        (httpx.Response(401, text="shared-secret"), 502, "credential"),
        (httpx.Response(403, text="shared-secret"), 502, "credential"),
        (httpx.Response(404, text="shared-secret"), 502, "model discovery"),
        (httpx.Response(405, text="shared-secret"), 502, "model discovery"),
        (httpx.Response(500, text="shared-secret"), 502, "server"),
        (httpx.Response(302, headers={"location": "https://example.com"}), 502, "redirect"),
        (httpx.Response(200, text="shared-secret"), 502, "invalid"),
        (httpx.Response(200, json={"models": []}), 502, "invalid"),
        (httpx.Response(200, json={"data": [{"id": 12}]}), 502, "invalid"),
        (httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)), 502, "large"),
        (httpx.ConnectError("shared-secret"), 502, "reach"),
        (httpx.ReadTimeout("shared-secret"), 504, "timed out"),
    ],
)
def test_discovery_failures_are_actionable_and_never_echo_provider_output(
    connection, reply, status, message
):
    client, _, calls, replies = connection
    replies.append(reply)
    response = post(client)
    assert response.status_code == status
    assert message in response.json()["detail"].lower()
    assert "shared-secret" not in response.text
    assert len(calls) == 1  # Even redirects never follow the provider elsewhere.


def test_empty_catalog_is_supported(connection):
    client, _, _, replies = connection
    replies.append(httpx.Response(200, json={"data": []}))
    assert post(client).json() == {"models": []}


@pytest.mark.parametrize(
    "route,model,token",
    [
        (None, "default-llm", "shared-secret"),
        ("chat", "chat-model", "chat-secret"),
    ],
)
def test_llm_probe_uses_selected_model_and_draft_thinking_without_saved_data(
    connection, route, model, token
):
    client, settings, calls, replies = connection
    replies.append(httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}))
    before = client.get("/v1/configuration").json()
    response = post(client, "test", route=route, changes={"llm_enable_thinking": False})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["latency_ms"] >= 0
    assert calls[-1].url.path == "/v1/chat/completions"
    assert calls[-1].headers["authorization"] == f"Bearer {token}"
    payload = json.loads(calls[-1].content)
    assert payload["model"] == model
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["max_tokens"] <= 64
    assert payload["messages"] == [{"role": "user", "content": "Reply with OK."}]
    assert client.get("/v1/configuration").json() == before
    assert not (settings.data_dir / ".private-settings.json").exists()


@pytest.mark.parametrize(
    "route,model,token",
    [
        (None, "default-stt", "shared-secret"),
        ("da", "danish", "route-secret"),
    ],
)
def test_stt_probe_uses_synthetic_wav_and_accepts_empty_transcript(connection, route, model, token):
    client, _, calls, replies = connection
    replies.append(httpx.Response(200, json={"text": ""}))
    response = post(client, "test", provider="stt", route=route)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls[-1].url.path == "/v1/audio/transcriptions"
    assert calls[-1].headers["authorization"] == f"Bearer {token}"
    body = calls[-1].content
    assert f' name="model"\r\n\r\n{model}\r\n'.encode() in body
    offset = body.index(b"RIFF")
    wav_length = int.from_bytes(body[offset + 4 : offset + 8], "little") + 8
    with wave.open(io.BytesIO(body[offset : offset + wav_length])) as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 16000
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 16000
        assert set(wav.readframes(16000)) == {0}


@pytest.mark.parametrize(
    "provider,reply,message",
    [
        ("llm", httpx.Response(401, text="shared-secret"), "credential"),
        ("stt", httpx.Response(400, text="shared-secret"), "model"),
        ("llm", httpx.Response(404, text="shared-secret"), "model"),
        ("stt", httpx.ReadTimeout("shared-secret"), "timed out"),
        ("llm", httpx.ConnectError("shared-secret"), "reach"),
        ("llm", httpx.Response(200, json={"choices": []}), "invalid"),
        ("llm", httpx.Response(200, json={"choices": [{"message": {"content": None}}]}), "invalid"),
        ("stt", httpx.Response(200, json={"text": 42}), "invalid"),
        ("stt", httpx.Response(200, json={"error": "shared-secret"}), "invalid"),
    ],
)
def test_probe_failure_returns_sanitized_feedback(connection, provider, reply, message):
    client, _, _, replies = connection
    replies.append(reply)
    response = post(client, "test", provider=provider)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert message in response.json()["message"].lower()
    assert response.json()["latency_ms"] >= 0
    assert "shared-secret" not in response.text


def test_probe_uses_unsaved_model_and_keeps_existing_private_file_identical(connection):
    client, settings, calls, replies = connection
    before = client.get("/v1/configuration").json()
    saved = client.patch(
        "/v1/configuration",
        json={
            "revision": before["revision"],
            "changes": {"llm_model": "saved-model"},
        },
    )
    assert saved.status_code == 200
    private_file = settings.data_dir / ".private-settings.json"
    private_bytes = private_file.read_bytes()
    replies.append(httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}))
    response = post(
        client,
        "test",
        changes={
            "llm_model": "draft-model",
            "llm_base_url": "http://draft.local/v1",
            "inference_token": "draft-secret",
        },
    )
    assert response.json()["ok"] is True
    assert calls[-1].url == "http://draft.local/v1/chat/completions"
    assert json.loads(calls[-1].content)["model"] == "draft-model"
    assert calls[-1].headers["authorization"] == "Bearer draft-secret"
    assert client.get("/v1/configuration").json() == saved.json()
    assert private_file.read_bytes() == private_bytes


def test_reasoning_response_proves_the_model_is_available(connection):
    client, _, _, replies = connection
    replies.append(
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": None, "reasoning_content": "Thinking"},
                        "finish_reason": "length",
                    }
                ]
            },
        )
    )
    assert post(client, "test").json()["ok"] is True


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def test_streaming_response_stops_at_budget_and_closes_stream(connection):
    client, _, _, replies = connection
    stream = ChunkStream([b"x" * MAX_RESPONSE_BYTES, b"x", b"must-not-read"])
    replies.append(httpx.Response(200, stream=stream))
    response = post(client)
    assert response.status_code == 502
    assert stream.reads == 2
    assert stream.closed


def test_overall_deadline_interrupts_a_slow_stream(connection):
    client, _, _, replies = connection
    client.app.state.memory.settings.inference_timeout_seconds = 1
    stream = ChunkStream([b'{"data": []}'], delay=2)
    replies.append(httpx.Response(200, stream=stream))
    response = post(client)
    assert response.status_code == 504
    assert stream.closed
