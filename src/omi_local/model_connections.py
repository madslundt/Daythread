"""Read-only model discovery and synthetic connection checks for configuration drafts."""

import asyncio
import io
import json
import re
import time
import wave
from collections.abc import Callable
from typing import Literal, cast, get_args

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .semantic_store import normalized
from .settings import LLMTask, ModelRoute, Settings

MAX_RESPONSE_BYTES = 262_144
CONNECTION_FIELDS = {
    "stt_base_url",
    "stt_model",
    "stt_routes",
    "llm_base_url",
    "llm_model",
    "llm_routes",
    "llm_enable_thinking",
    "inference_token",
    "stt_api_key",
    "llm_api_key",
    "embedding_base_url",
    "embedding_model",
    "embedding_token",
    "embedding_dimension",
    "embedding_routes",
}


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["stt", "llm", "embedding"]
    route: str | None = Field(default=None, max_length=32)
    changes: dict = Field(default_factory=dict)

    def resolve(self, settings: Settings, *, require_model: bool) -> ModelRoute:
        if self.route is not None:
            if self.provider == "stt":
                if not re.fullmatch(r"[A-Za-z-]{2,32}", self.route):
                    raise ValueError("Invalid transcription route")
                route = settings.stt_route(self.route)
            elif self.provider == "llm":
                if self.route not in get_args(LLMTask):
                    raise ValueError("Invalid language model route")
                route = settings.llm_route(cast(LLMTask, self.route))
            else:
                if not re.fullmatch(r"[A-Za-z]{2,3}", self.route):
                    raise ValueError("Invalid embedding language route")
                effective = settings.embedding_route_settings(self.route)
                route = ModelRoute(
                    model=effective.embedding_model,
                    base_url=effective.embedding_base_url,
                    token=effective.embedding_token,
                )
        else:
            # Defaults must remain independent of the auto/chat override rows.
            token = (
                settings.embedding_token if self.provider == "embedding"
                else settings.model_api_key(self.provider)
            )
            route = ModelRoute(
                model=getattr(settings, f"{self.provider}_model"),
                base_url=getattr(settings, f"{self.provider}_base_url"),
                token=token,
            )
        if require_model and (not route.model.strip() or len(route.model) > 200):
            raise ValueError("Invalid model name")
        return route


class ConnectionFailure(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def synthetic_audio() -> bytes:
    """One second of mono silence; no recording or transcript leaves the archive."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0" * 32000)
    return buffer.getvalue()


class ModelConnection:
    def __init__(self, settings: Settings, route: ModelRoute, client: httpx.AsyncClient):
        self.settings = settings
        self.route = route
        self.client = client

    async def request(self, method: str, path: str, **kwargs) -> dict:
        token = self.route.token.get_secret_value()
        timeout = min(self.settings.inference_timeout_seconds, 15 if method == "GET" else 30)
        try:
            async with asyncio.timeout(timeout):
                async with self.client.stream(
                    method,
                    f"{self.route.base_url}/{path}",
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                    timeout=httpx.Timeout(timeout, connect=min(timeout, 5)),
                    follow_redirects=False,
                    **kwargs,
                ) as response:
                    self.check_status(response.status_code, discovery=method == "GET")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ConnectionFailure("The server response is too large.")
                        data.extend(chunk)
                    result = json.loads(data)
                    if not isinstance(result, dict):
                        raise ConnectionFailure("The server returned an invalid response.")
                    return result
        except TimeoutError, httpx.TimeoutException:
            raise ConnectionFailure(
                "The connection timed out. Check that the server and model are ready.", 504
            ) from None
        except httpx.HTTPError, httpx.InvalidURL:
            raise ConnectionFailure(
                "Could not reach the server. Check its address and that it is running."
            ) from None
        except ValueError, RecursionError:
            raise ConnectionFailure("The server returned an invalid response.") from None

    @staticmethod
    def check_status(status: int, discovery: bool) -> None:
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            message = "The server rejected the credential. Check the connection key."
        elif 300 <= status < 400:
            message = "The server returned a redirect. Enter the final server address."
        elif discovery and status in {404, 405, 501}:
            message = "This server does not support model discovery."
        elif status in {400, 404, 422}:
            message = (
                "The server rejected this model or request. Check the model and server address."
            )
        elif status == 429:
            message = "The server is busy or rate limited. Try again shortly."
        else:
            message = "The server could not complete the request. Check that the model is ready."
        raise ConnectionFailure(message)

    async def models(self) -> dict:
        payload = await self.request("GET", "models")
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise ConnectionFailure("The server returned an invalid model list.")
        models = set()
        for entry in entries:
            model = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(model, str) or not model.strip() or len(model) > 200:
                raise ConnectionFailure("The server returned an invalid model list.")
            models.add(model)
        return {"models": sorted(models)}

    async def test(self, request: ConnectionRequest) -> dict:
        started = time.monotonic()
        try:
            if request.provider == "llm":
                options = (
                    {}
                    if self.settings.llm_enable_thinking is None
                    else {
                        "chat_template_kwargs": {
                            "enable_thinking": self.settings.llm_enable_thinking
                        }
                    }
                )
                payload = await self.request(
                    "POST",
                    "chat/completions",
                    json={
                        "model": self.route.model,
                        "max_tokens": 32,
                        "stream": False,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        **options,
                    },
                )
                choices = payload.get("choices")
                first = choices[0] if isinstance(choices, list) and choices else None
                message = first.get("message") if isinstance(first, dict) else None
                valid = isinstance(message, dict) and any(
                    isinstance(message.get(key), str) for key in ("content", "reasoning_content")
                )
            elif request.provider == "stt":
                fields = {"model": self.route.model, "response_format": "json"}
                if request.route and re.fullmatch(r"[a-z]{2,3}", request.route.lower()):
                    fields["language"] = request.route.lower()
                payload = await self.request(
                    "POST",
                    "audio/transcriptions",
                    data=fields,
                    files={"file": ("connection-test.wav", synthetic_audio(), "audio/wav")},
                )
                valid = isinstance(payload.get("text"), str)
            else:
                payload = await self.request(
                    "POST", "embeddings",
                    json={"model": self.route.model, "input": ["connection check"],
                          "encoding_format": "float"},
                )
                entries = payload.get("data")
                valid = False
                if isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict):
                    try:
                        dimension = self.settings.embedding_route_settings(
                            request.route
                        ).embedding_dimension
                        normalized(entries[0]["embedding"], dimension)
                        valid = True
                    except (ValueError, KeyError, TypeError, OverflowError):
                        valid = False
            if not valid:
                raise ConnectionFailure("The server returned an invalid model response.")
            result = {
                "ok": True,
                "message": "Connected. The selected model responded successfully.",
            }
        except ConnectionFailure as exc:
            result = {"ok": False, "message": exc.message}
        return {**result, "latency_ms": round((time.monotonic() - started) * 1000)}


def model_connection_routes(
    preview: Callable[[dict], Settings], client: Callable[[], httpx.AsyncClient]
) -> APIRouter:
    router = APIRouter(prefix="/v1/configuration")

    async def prepare(
        request: Request, *, require_model: bool
    ) -> tuple[ConnectionRequest, ModelConnection]:
        try:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError("Request too large")
                body.extend(chunk)
            parameters = ConnectionRequest.model_validate(json.loads(body))
            if parameters.changes.keys() - CONNECTION_FIELDS:
                raise ValueError("Only connection fields can be previewed")
            settings = preview(parameters.changes)
            route = parameters.resolve(settings, require_model=require_model)
        except ValueError, TypeError, AttributeError, RecursionError, ValidationError:
            # Validation errors can include submitted write-only keys; never echo them.
            raise HTTPException(422, "Invalid connection settings or model route.") from None
        return parameters, ModelConnection(settings, route, client())

    @router.post("/models")
    async def models(request: Request) -> dict:
        _, connection = await prepare(request, require_model=False)
        try:
            return await connection.models()
        except ConnectionFailure as exc:
            raise HTTPException(exc.status, exc.message) from None

    @router.post("/test")
    async def test(request: Request) -> dict:
        parameters, connection = await prepare(request, require_model=True)
        return await connection.test(parameters)

    return router
