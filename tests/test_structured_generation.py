"""JSON contracts at the real provider HTTP boundary, without contacting a model."""

import asyncio
import json

import httpx
import pytest

from omi_local.inference import Inference
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
