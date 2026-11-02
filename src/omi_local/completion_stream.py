"""Buffer long provider streams privately into one complete, usage-bearing response."""

import codecs
import json
from collections.abc import AsyncIterator

import httpx

from .inference_errors import InferenceError
from .inference_usage import reported_tokens

MAX_SUMMARY_CHARACTERS = 1_048_576
MAX_SUMMARY_STREAM_BYTES = 32 * 1024 * 1024


async def stream_data(
    response: httpx.Response, *, maximum_bytes: int = 1_000_000, maximum_event: int = 64000,
) -> AsyncIterator[str]:
    """Decode SSE across arbitrary byte boundaries with bounded line/response memory."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending, data, size = "", [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > maximum_bytes:
            raise InferenceError("Model stream exceeds response budget")
        pending += decoder.decode(chunk)
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.removesuffix("\r")
            if len(line) > maximum_event:
                raise InferenceError("Model stream event exceeds budget")
            if not line and data:
                yield "\n".join(data)
                data = []
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            if sum(map(len, data)) > maximum_event:
                raise InferenceError("Model stream event exceeds budget")
        if len(pending) > maximum_event:
            raise InferenceError("Model stream line exceeds budget")
    pending += decoder.decode(b"", final=True)
    if pending.startswith("data:"):
        data.append(pending[5:].removeprefix(" ").rstrip("\r"))
    if data:
        yield "\n".join(data)


def completion_response(response: httpx.Response, payload: object) -> httpx.Response:
    # Preserve gateway billing headers, replacing wire metadata belonging to the SSE body.
    headers = {key: value for key, value in response.headers.items() if key not in {
        "content-type", "content-length", "content-encoding", "transfer-encoding",
    }}
    return httpx.Response(response.status_code, headers=headers, json=payload,
                          request=response.request)


async def buffered_completion(response: httpx.Response, token_usage: dict) -> httpx.Response:
    """Only a terminal completion reaches callers; reasoning and partial text stay private."""
    try:
        if "application/json" in response.headers.get("content-type", ""):
            # Compatibility gateways and test transports may ignore stream=True.
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_SUMMARY_STREAM_BYTES:
                    raise InferenceError("Model stream exceeds response budget")
            payload = json.loads(body)
            token_usage.update(reported_tokens(payload))
            return completion_response(response, payload)
        pieces, size, finished = [], 0, None
        async for raw in stream_data(
            response, maximum_bytes=MAX_SUMMARY_STREAM_BYTES,
            maximum_event=MAX_SUMMARY_STREAM_BYTES,
        ):
            if raw == "[DONE]":
                break
            event = json.loads(raw)
            token_usage.update(reported_tokens(event))
            if not isinstance(event, dict) or "error" in event:
                raise ValueError("Provider stream error")
            choices = event.get("choices", [])
            if not isinstance(choices, list):
                raise ValueError("Invalid choices")
            for choice in choices:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {}).get("content")
                if delta is not None:
                    if not isinstance(delta, str) or finished is not None:
                        raise ValueError("Invalid stream delta")
                    size += len(delta)
                    if size > MAX_SUMMARY_CHARACTERS:
                        raise InferenceError("Invalid LLM response")
                    pieces.append(delta)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if finished is not None or reason not in {"stop", "length"}:
                        raise ValueError("Invalid finish reason")
                    finished = reason
        if finished is None:
            raise ValueError("Incomplete model stream")
        # A length finish can contain only reasoning; _generate owns its bounded retry.
        payload = {"choices": [{"index": 0, "finish_reason": finished,
                                "message": {"role": "assistant", "content": "".join(pieces)}}],
                   "usage": token_usage}
        return completion_response(response, payload)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise InferenceError("Invalid LLM response") from exc
