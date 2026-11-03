"""JSON contracts at the real provider HTTP boundary, without contacting a model."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from test_extended import audio_data

from omi_local.chat_context import bounded_generation
from omi_local.inference import Inference
from omi_local.inference_errors import InferenceTruncated
from omi_local.reprocessing import RedoRequest, Reprocessing
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
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
def test_summary_reasoning_exhaustion_preserves_thinking_and_raises(
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
            with pytest.raises(InferenceTruncated):
                if structured:
                    await inference.generate_structured_json(
                        "Write in Danish", "Original evidence", Analysis.model_json_schema(),
                    )
                else:
                    await inference.generate("Write in Danish", "Original evidence")
            assert inference.settings.llm_enable_thinking is True

    asyncio.run(run())
    budgets = [body["max_tokens"] for body, _ in requests]
    assert budgets == [65536]
    assert [body["chat_template_kwargs"]["enable_thinking"] for body, _ in requests] == [
        True,
    ]
    assert requests[0][1] == "Bearer test-token"


@pytest.mark.parametrize("task, thinking, expected", [
    ("summary", True, 1), ("summary", False, 1), ("summary", None, 1), ("chat", True, 3),
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


def test_summary_truncation_with_context_limit_does_not_disable_thinking(tmp_path):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length", "message": {"content": "partial"},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=True, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with bounded_generation(4096):
                with pytest.raises(InferenceTruncated):
                    await Inference(settings, client, task="summary").generate(
                        "Task", "Evidence",
                    )

    asyncio.run(run())
    budgets = [body["max_tokens"] for body in requests]
    assert len(budgets) == 1
    assert budgets[0] < 4000
    assert requests[0]["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.parametrize("mode", ["plain", "json", "structured"])
def test_summary_requests_64k_from_the_first_attempt_with_sufficient_timeout(tmp_path, mode):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((body, request.extensions["timeout"]["read"]))
        enough = body["max_tokens"] == 65536
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop" if enough else "length",
            "message": {"content": '{"ok":true}' if enough else ""},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, inference_timeout_seconds=120, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(settings, client, task="summary")
            if mode == "structured":
                result = await inference.generate_structured_json("Task", "Evidence", {})
            elif mode == "json":
                result = await inference.generate_json("Task", "Evidence")
            else:
                result = await inference.generate("Task", "Evidence")
            assert result == '{"ok":true}'

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0][0]["max_tokens"] == 65536
    assert requests[0][1] >= 3600


@pytest.mark.parametrize("length, accepted", [(70000, True), (1048576, True), (1048577, False)])
def test_large_summary_content_has_a_bounded_response_guard(tmp_path, length, accepted):
    text = "ø" * length

    def handler(request):
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": text},
        }]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            if accepted:
                assert await inference.generate("Task", "Evidence") == text
            else:
                from omi_local.inference_errors import InferenceError
                with pytest.raises(InferenceError, match="Invalid LLM response"):
                    await inference.generate("Task", "Evidence")

    asyncio.run(run())


def test_real_redo_completes_when_provider_requires_64k_output_capacity(tmp_path):
    text = "We agreed to send the revised delivery plan on Friday."
    key = str(uuid4())
    budgets = []

    def provider(request):
        body = json.loads(request.content)
        budgets.append(body["max_tokens"])
        if body["max_tokens"] != 65536:
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "length", "message": {"content": ""},
            }]})
        task = json.loads(body["messages"][1]["content"])["task"]
        if "response_format" in body:
            result = json.dumps({"title": "Delivery plan", "continues_previous": False,
                                 "sections": [{"title": "Delivery plan", "start_quote": text}]})
        elif task.startswith("Edit one completed summary"):
            result = json.loads(body["messages"][1]["content"])["evidence"]
            result = json.loads(result)["completed_draft"]
        elif task.startswith(("Organize useful recorded information",
                              "Review the complete daily topic plan")):
            result = json.dumps({"topics": [{"title": "Delivery plan", "scope": "Friday delivery",
                                             "items": ["n1"]}]})
        else:
            result = f"The revised delivery plan is due Friday. [{key}]"
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": result},
        }]})

    async def run():
        settings = Settings(data_dir=tmp_path, timezone="UTC", summary_language="en",
                            _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            memory = Memory(Store(tmp_path), settings, Inference(settings, client))
            memory.store.ingest(key, "phone", datetime(2026, 10, 15, 12, tzinfo=UTC),
                                audio_data(), 1, source_kind="manual_upload")
            memory.store.complete(key, text)
            jobs = Reprocessing(memory)
            jobs.enqueue(RedoRequest(kind="transcripts"))
            memory.store.complete(key, text)
            assert await jobs.process_one()
            status = jobs.observation()["job"]
            assert status["state"] == "completed"
            assert status["transcript_completed"] == status["summary_completed"] == 1
            assert "delivery plan" in memory.cached_recap(datetime(2026, 10, 15).date())["summary"]

    asyncio.run(run())
    assert budgets and set(budgets) == {65536}
