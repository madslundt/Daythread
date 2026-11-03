"""Large summaries buffer provider SSE privately without losing usage or completion checks."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from omi_local.completion_stream import MAX_SUMMARY_STREAM_BYTES, stream_data
from omi_local.inference import Inference
from omi_local.inference_errors import InferenceError, InferenceTruncated
from omi_local.settings import Settings


def event(value):
    return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"


def choice(content=None, reason=None, **extra):
    return {"choices": [{"index": 0, "delta": {"content": content, **extra},
                         "finish_reason": reason}]}


def test_large_summary_buffers_sse_and_preserves_usage_billing_and_request_contract(tmp_path):
    requests, usage = [], []
    operation = str(uuid4())

    def handler(request):
        body = json.loads(request.content)
        requests.append((body, dict(request.headers)))
        return httpx.Response(200, headers={
            "Content-Type": "text/event-stream", "X-Daythread-Billing": "credits",
            "X-Daythread-Operation-Id": operation, "X-Daythread-Credits-Used": "0.012",
        }, text=(event(choice(reasoning_content="Private model reasoning"))
                 + event(choice("Full ")) + event(choice("summary ø", "stop"))
                 + event({"choices": [], "usage": {"prompt_tokens": 27, "completion_tokens": 19}})
                 + "data: [DONE]\n\n"))

    async def run():
        settings = Settings(data_dir=tmp_path, llm_api_key="test-token", _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(settings, client)
            inference.usage_recorder = usage.append
            result = await inference.generate("Write in Danish", "Original evidence")
            assert result == "Full summary ø"

    asyncio.run(run())
    body, headers = requests[0]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 65536
    assert headers["authorization"] == "Bearer test-token"
    assert float(headers["x-litellm-timeout"]) >= 4096
    assert float(headers["x-litellm-stream-timeout"]) >= 4096
    assert json.loads(body["messages"][1]["content"]) == {
        "task": "Write in Danish", "evidence": "Original evidence",
    }
    assert len(usage) == 2
    assert usage[0]["outcome"] == "pending"
    assert usage[-1]["outcome"] == "succeeded"
    assert usage[-1]["input_tokens"] == 27
    assert usage[-1]["output_tokens"] == 19
    assert usage[-1]["operation_id"] == operation
    assert usage[-1]["credit_units"] == 12


@pytest.mark.parametrize("failure", ["eof", "error", "error_after_stop", "malformed", "length"])
def test_incomplete_or_failed_summary_stream_never_returns_partial_content(tmp_path, failure):
    usage = []

    def handler(request):
        ending = {
            "eof": "data: [DONE]\n\n",
            "error": event({"error": {"message": "Provider failure"}}),
            "error_after_stop": event(choice(reason="stop")) + event({"error": "Provider failure"}),
            "malformed": "data: {invalid\n\n",
            "length": event(choice(reason="length")) + "data: [DONE]\n\n",
        }[failure]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=event(choice("Partial summary")) + ending)

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=False, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(settings, client)
            inference.usage_recorder = usage.append
            with pytest.raises(InferenceTruncated if failure == "length" else InferenceError):
                await inference.generate("Task", "Evidence")

    asyncio.run(run())
    assert len(usage) == 2


def test_summary_sse_length_preserves_thinking_and_does_not_return_partial_content(tmp_path):
    requests, usage = [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        thinking = body["chat_template_kwargs"]["enable_thinking"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=(
            event(choice(reasoning_content="Private reasoning"))
            + event(choice(None if thinking else "Complete summary",
                           "length" if thinking else "stop"))
            + event({"choices": [], "usage": {"prompt_tokens": 17,
                                              "completion_tokens": 65536 if thinking else 9}})
            + "data: [DONE]\n\n"
        ))

    async def run():
        settings = Settings(data_dir=tmp_path, llm_enable_thinking=True, _env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(settings, client)
            inference.usage_recorder = usage.append
            with pytest.raises(InferenceTruncated):
                await inference.generate_json("Task", "Evidence")
            assert settings.llm_enable_thinking is True

    asyncio.run(run())
    assert [body["max_tokens"] for body in requests] == [65536]
    assert [body["chat_template_kwargs"]["enable_thinking"] for body in requests] == [True]
    assert [row["outcome"] for row in usage] == ["pending", "succeeded"]
    assert usage[-1]["output_tokens"] == 65536


def test_explicit_translation_keeps_nonstreaming_transport_and_gateway_headers(tmp_path):
    requests = []

    def handler(request):
        requests.append((json.loads(request.content), dict(request.headers)))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                     "message": {"content": "Oversættelse"}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            assert await inference.translate("Transcript", "da") == "Oversættelse"

    asyncio.run(run())
    body, headers = requests[0]
    assert "stream" not in body
    assert "x-litellm-timeout" not in headers
    assert "x-litellm-stream-timeout" not in headers


def test_large_summary_stream_allows_event_overhead_above_chat_response_bound(tmp_path):
    def handler(request):
        # Invisible reasoning and event metadata count toward the raw stream budget,
        # while the complete visible summary has its independent character bound.
        wire = event(choice(reasoning_content="r" * 40000)) * 30
        wire += event(choice("Visible summary", "stop")) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=wire)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            assert await inference.generate("Task", "Evidence") == "Visible summary"

    asyncio.run(run())


class CancelledStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield event(choice("Partial summary")).encode()
        raise asyncio.CancelledError

    async def aclose(self):
        self.closed = True


def test_cancelled_summary_stream_closes_transport_and_keeps_attempt_unknown(tmp_path):
    stream, usage = CancelledStream(), []

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            inference.usage_recorder = usage.append
            with pytest.raises(asyncio.CancelledError):
                await inference.generate("Task", "Evidence")

    asyncio.run(run())
    assert stream.closed
    assert len(usage) == 2
    assert usage[-1]["outcome"] == "unknown"


def test_long_summary_raw_stream_has_a_separate_32_mib_bound(tmp_path, monkeypatch):
    assert MAX_SUMMARY_STREAM_BYTES == 32 * 1024 * 1024
    monkeypatch.setattr("omi_local.completion_stream.MAX_SUMMARY_STREAM_BYTES", 1024)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=event(choice(reasoning_content="r" * 1025)))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
            with pytest.raises(InferenceError, match="stream exceeds response budget"):
                await inference.generate("Task", "Evidence")

    asyncio.run(run())


def test_chat_sse_decoder_retains_its_original_one_mb_raw_bound():
    async def run():
        response = httpx.Response(200, content=b" " * 1_000_001)
        with pytest.raises(InferenceError, match="stream exceeds response budget"):
            async for _ in stream_data(response):
                pytest.fail("An over-budget chat event must not be delivered")

    asyncio.run(run())
