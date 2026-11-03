"""Optional local model template controls at the HTTP inference boundary."""

import io
import json
import wave

import httpx
import pytest

from omi_local.chat_context import bounded_generation, request_tokens, safety
from omi_local.inference import SUMMARY_SYSTEM, Inference
from omi_local.inference_errors import InferenceTruncated
from omi_local.settings import Settings
from omi_local.structured_generation import SUMMARY_OUTPUT_TOKENS


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("thinking", [None, False, True])
@pytest.mark.parametrize("generation", ["normal", "json", "stream"])
async def test_generation_sends_thinking_control_only_when_configured(
    tmp_path, thinking, generation
):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=thinking, _env_file=None, stt_filter_speech=False,
    )
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


@pytest.mark.parametrize("value, expected", [(None, None), ("false", False), ("true", True)])
def test_summary_thinking_override_loads_from_environment(tmp_path, monkeypatch, value, expected):
    monkeypatch.delenv("OMI_LLM_SUMMARY_ENABLE_THINKING", raising=False)
    if value is not None:
        monkeypatch.setenv("OMI_LLM_SUMMARY_ENABLE_THINKING", value)
    assert Settings(data_dir=tmp_path, _env_file=None).llm_summary_enable_thinking is expected


@pytest.mark.anyio
@pytest.mark.parametrize("thinking", [False, True])
async def test_thinking_control_never_changes_transcription_requests(tmp_path, thinking):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=thinking, _env_file=None, stt_filter_speech=False,
    )
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


def _summary_stream(content, finish="stop"):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=(
            f'data: {{"choices":[{{"delta":{{"content":{json.dumps(content)}}}}}]}}\n\n'
            f'data: {{"choices":[{{"delta":{{}},"finish_reason":"{finish}"}}]}}\n\n'
            "data: [DONE]\n\n"
        ),
    )


@pytest.mark.anyio
async def test_summary_thinking_budget_is_opt_in_and_preserves_enabled_thinking(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=True,
        llm_summary_thinking_token_budget=8192, _env_file=None,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _summary_stream("A useful answer")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await Inference(settings, http).generate("Task", "Evidence") == "A useful answer"

    assert len(requests) == 1
    assert requests[0]["max_tokens"] == SUMMARY_OUTPUT_TOKENS
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert requests[0]["thinking_token_budget"] == 8192


@pytest.mark.anyio
async def test_summary_override_enables_summary_only_when_global_thinking_is_off(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=False,
        llm_summary_enable_thinking=True, llm_summary_thinking_token_budget=8192,
        _env_file=None,
    )
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return (_summary_stream("A useful answer") if body.get("stream") else httpx.Response(
            200, json={"choices": [{"message": {"content": "A useful answer"}}]},
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http)
        assert await inference.generate("Summary task", "Evidence") == "A useful answer"
        chat = inference.for_task("chat")
        assert await chat.generate("Chat task", "Evidence") == "A useful answer"
        assert await inference.for_task("extraction").generate(
            "Extract task", "Evidence",
        ) == "A useful answer"

    assert len(requests) == 3
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert requests[0]["thinking_token_budget"] == 8192
    assert requests[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[2]["chat_template_kwargs"] == {"enable_thinking": False}
    assert all("thinking_token_budget" not in request for request in requests[1:])


@pytest.mark.anyio
async def test_summary_override_can_disable_global_thinking_without_changing_chat(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=True,
        llm_summary_enable_thinking=False, llm_summary_thinking_token_budget=8192,
        _env_file=None,
    )
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return (_summary_stream("A useful answer") if body.get("stream") else httpx.Response(
            200, json={"choices": [{"message": {"content": "A useful answer"}}]},
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http)
        assert await inference.generate("Summary task", "Evidence") == "A useful answer"
        chat = inference.for_task("chat")
        assert await chat.generate("Chat task", "Evidence") == "A useful answer"

    assert requests[0].get("chat_template_kwargs") == {"enable_thinking": False}
    assert "thinking_token_budget" not in requests[0]
    assert requests[1]["chat_template_kwargs"] == {"enable_thinking": True}


def test_summary_thinking_override_is_nullable_and_inherits_shared_setting(tmp_path):
    assert Settings(data_dir=tmp_path, _env_file=None).effective_summary_thinking is None
    assert Settings(
        data_dir=tmp_path, llm_enable_thinking=True, _env_file=None,
    ).effective_summary_thinking is True
    assert Settings(
        data_dir=tmp_path, llm_enable_thinking=True,
        llm_summary_enable_thinking=False, _env_file=None,
    ).effective_summary_thinking is False


@pytest.mark.anyio
@pytest.mark.parametrize("thinking,budget", [(None, 8192), (False, 8192), (True, None)])
async def test_summary_budget_is_not_sent_without_explicit_enabled_budget(
    tmp_path, thinking, budget,
):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=thinking,
        llm_summary_thinking_token_budget=budget, _env_file=None,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _summary_stream("A useful answer")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await Inference(settings, http).generate("Task", "Evidence") == "A useful answer"
    assert len(requests) == 1
    assert "thinking_token_budget" not in requests[0]


@pytest.mark.anyio
async def test_thinking_budget_does_not_leak_to_other_llm_tasks(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=True,
        llm_summary_thinking_token_budget=8192, _env_file=None,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http).for_task("chat")
        assert await inference.generate("Task", "Evidence") == "answer"

    assert len(requests) == 1
    assert "thinking_token_budget" not in requests[0]
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}


@pytest.mark.anyio
async def test_summary_truncation_does_not_retry_with_thinking_disabled(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=True,
        llm_summary_thinking_token_budget=8192, _env_file=None,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _summary_stream("incomplete", finish="length")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(InferenceTruncated):
            await Inference(settings, http).generate("Task", "Evidence")

    assert len(requests) == 1
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert requests[0]["thinking_token_budget"] == 8192
    assert requests[0]["max_tokens"] == SUMMARY_OUTPUT_TOKENS


@pytest.mark.anyio
async def test_context_limited_summary_reserves_half_for_final_answer(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=True,
        llm_summary_thinking_token_budget=8192, _env_file=None,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _summary_stream("A useful answer")

    task, evidence = "Task", "Evidence"
    request_size = request_tokens(task, evidence, SUMMARY_SYSTEM)
    context_limit = request_size + 8000
    effective_output = context_limit - safety(context_limit) - request_size
    assert 0 < effective_output < SUMMARY_OUTPUT_TOKENS

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http)
        with bounded_generation(context_limit):
            assert await inference.generate(task, evidence) == "A useful answer"

    assert len(requests) == 1
    assert requests[0]["max_tokens"] == effective_output
    assert requests[0]["thinking_token_budget"] == min(8192, effective_output // 2)


@pytest.mark.anyio
async def test_context_clamp_uses_summary_override_when_shared_thinking_is_off(tmp_path):
    settings = Settings(
        data_dir=tmp_path, llm_enable_thinking=False, llm_summary_enable_thinking=True,
        llm_summary_thinking_token_budget=8192, _env_file=None,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return _summary_stream("A useful answer")

    task, evidence = "Task", "Evidence"
    request_size = request_tokens(task, evidence, SUMMARY_SYSTEM)
    context_limit = request_size + 7000
    effective_output = context_limit - safety(context_limit) - request_size
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with bounded_generation(context_limit):
            assert await Inference(settings, http).generate(task, evidence) == "A useful answer"

    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert requests[0]["max_tokens"] == effective_output
    assert requests[0]["thinking_token_budget"] == min(8192, effective_output // 2)
