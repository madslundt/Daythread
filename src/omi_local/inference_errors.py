from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .inference import Inference


class InferenceError(Exception):
    """Provider returned an invalid response; never forward its body to the caller."""


class InferenceTruncated(InferenceError):
    """The provider explicitly stopped at its output token limit."""


class InferenceDisabled(InferenceError):
    """The account intentionally paused this task; no provider request was made."""


class InferenceUnavailable(InferenceDisabled):
    """The trusted gateway rejected admission before starting model work."""

    retry_after_seconds = 30


async def check_inference_response(
    inference: Inference, provider: str, response: httpx.Response,
) -> None:
    """Translate only the trusted managed gateway's explicit task policy response."""
    if (provider == "llm" and inference.settings.llm_busy_retry_enabled
            and response.status_code == 503 and response.headers.get("retry-after") == "30"):
        route = inference.settings.llm_route(inference.task)
        if str(response.request.url) == route.base_url + "/chat/completions":
            await response.aread()
            try:
                body = response.json()
            except ValueError:
                body = None
            if (isinstance(body, dict) and isinstance(body.get("error"), dict)
                    and body["error"].get("type") == "service_unavailable"):
                raise InferenceUnavailable("The local model is temporarily busy; work stays queued")
    if inference.settings.managed_hosting and response.status_code == 409:
        await response.aread()
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("detail") == "This task is turned off.":
            setattr(inference.settings, provider + "_enabled", False)
            inference.health[provider] = {"state": "disabled"}
            inference.settings.require_inference(provider)
    response.raise_for_status()
