"""Optional local model template controls at the HTTP inference boundary."""

import io
import json
import wave

import httpx
import pytest

from omi_local.inference import Inference
from omi_local.settings import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("thinking", [None, False, True])
@pytest.mark.parametrize("generation", ["normal", "json", "stream"])
async def test_generation_sends_thinking_control_only_when_configured(
    tmp_path, thinking, generation
):
    settings = Settings(data_dir=tmp_path, llm_enable_thinking=thinking, _env_file=None)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if generation == "stream":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n'
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"answer":"Answer"}'}}]}
        )

    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http)
        if generation == "stream":
            assert await inference.generate_stream("Task", "Evidence", on_delta) == "Answer"
            assert deltas == ["Answer"]
        elif generation == "json":
            assert await inference.generate_json("Task", "Evidence") == '{"answer":"Answer"}'
        else:
            assert await inference.generate("Task", "Evidence") == '{"answer":"Answer"}'

    assert len(requests) == 1
    if thinking is None:
        assert "chat_template_kwargs" not in requests[0]
    else:
        assert requests[0].get("chat_template_kwargs") == {"enable_thinking": thinking}


@pytest.mark.parametrize("value, expected", [(None, None), ("false", False), ("true", True)])
def test_thinking_control_loads_from_environment(tmp_path, monkeypatch, value, expected):
    monkeypatch.delenv("OMI_LLM_ENABLE_THINKING", raising=False)
    if value is not None:
        monkeypatch.setenv("OMI_LLM_ENABLE_THINKING", value)
    assert Settings(data_dir=tmp_path, _env_file=None).llm_enable_thinking is expected


@pytest.mark.anyio
@pytest.mark.parametrize("thinking", [False, True])
async def test_thinking_control_never_changes_transcription_requests(tmp_path, thinking):
    settings = Settings(data_dir=tmp_path, llm_enable_thinking=thinking, _env_file=None)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\0" * 16000)

    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b"chat_template_kwargs" not in request.content
        assert b"enable_thinking" not in request.content
        return httpx.Response(200, json={"text": "Speech"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await Inference(settings, http).transcribe(audio.getvalue()) == "Speech"
