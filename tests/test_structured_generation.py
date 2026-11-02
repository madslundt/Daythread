"""JSON contracts at the real provider HTTP boundary, without contacting a model."""

import asyncio
import json

import httpx
import pytest

from omi_local.chat_context import bounded_generation
from omi_local.inference import Inference
from omi_local.inference_errors import InferenceTruncated
from omi_local.settings import Settings
from omi_local.topic_analysis import Analysis


def test_structured_generation_sends_the_schema_and_preserves_plain_json_calls(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            assert await inference.generate_structured_json(
                "Task", "Evidence", Analysis.model_json_schema(),
            ) == '{"ok":true}'
            await inference.generate_json("Task", "Evidence")

    asyncio.run(run())
    schema = requests[0]["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert set(schema["schema"]["required"]) == {"title", "sections", "continues_previous"}
    assert schema["schema"]["$defs"]["Topic"]["properties"]["start_quote"]["maxLength"] == 300
    assert "response_format" not in requests[1]


def test_provider_without_schema_support_uses_the_existing_json_transport(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            for _ in range(2):
                assert await inference.generate_structured_json(
                    "Task", "Evidence", Analysis.model_json_schema(),
                ) == '{"ok":true}'

    asyncio.run(run())
    assert ["response_format" in body for body in requests] == [True, False, False]


@pytest.mark.parametrize("status, message", [(401, "Unauthorized"), (400, "Unknown model")])
def test_other_provider_errors_are_preserved(tmp_path, status, message):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": message}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            with pytest.raises(httpx.HTTPStatusError):
                await inference.generate_structured_json("Task", "Evidence", {})

    asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize("structured", [False, True])
def test_summary_reasoning_exhaustion_uses_one_request_only_nonthinking_fallback(
    tmp_path, structured,
):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((body, request.headers.get("authorization")))
        thinking = body["chat_template_kwargs"]["enable_thinking"]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length" if thinking else "stop",
            "message": {"content": "" if thinking else '{"ok":true}',
                        "reasoning_content": "Unfinished reasoning" if thinking else ""},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=True,
                            llm_api_key="test-token", _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(settings, client, task="summary")
            if structured:
                result = await inference.generate_structured_json(
                    "Write in Danish", "Original evidence", Analysis.model_json_schema(),
                )
            else:
                result = await inference.generate("Write in Danish", "Original evidence")
            assert result == '{"ok":true}'
            assert inference.settings.llm_enable_thinking is True

    asyncio.run(run())
    budgets = [body["max_tokens"] for body, _ in requests]
    assert budgets == ([2000, 4000, 8000, 8000] if structured
                       else [4000, 8000, 16000, 16000])
    assert [body["chat_template_kwargs"]["enable_thinking"] for body, _ in requests] == [
        True, True, True, False,
    ]
    # Authentication, response schema, language, task, evidence and model all survive the retry.
    original, original_auth = requests[0]
    final, final_auth = requests[-1]
    assert original_auth == final_auth == "Bearer test-token"
    assert {key: value for key, value in original.items()
            if key not in {"max_tokens", "chat_template_kwargs"}} == {
                key: value for key, value in final.items()
                if key not in {"max_tokens", "chat_template_kwargs"}}


@pytest.mark.parametrize("task, thinking, expected", [
    ("summary", True, 4), ("summary", False, 3), ("summary", None, 3), ("chat", True, 3),
])
def test_truncated_fallback_is_bounded_and_never_returns_partial_content(
    tmp_path, task, thinking, expected,
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length", "message": {"content": "partial result"},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=thinking, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(InferenceTruncated):
                await Inference(settings, client, task=task).generate("Task", "Evidence")

    asyncio.run(run())
    assert len(requests) == expected


def test_successful_summary_preserves_enabled_thinking_without_fallback(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": "Complete result"},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=True, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await Inference(settings, client, task="summary").generate(
                "Task", "Evidence",
            ) == "Complete result"

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_nonthinking_summary_fallback_preserves_the_effective_context_budget(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        thinking = body["chat_template_kwargs"]["enable_thinking"]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length" if thinking else "stop",
            "message": {"content": "" if thinking else "Complete answer"},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=True, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with bounded_generation(4096):
                assert await Inference(settings, client, task="summary").generate(
                    "Task", "Evidence",
                ) == "Complete answer"

    asyncio.run(run())
    budgets = [body["max_tokens"] for body in requests]
    assert len(budgets) == 4
    assert budgets[0] < 4000
    assert budgets[-1] == budgets[-2] == budgets[0]
    assert requests[-1]["chat_template_kwargs"]["enable_thinking"] is False
