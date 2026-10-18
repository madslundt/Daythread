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


async def check_inference_response(
    inference: Inference, provider: str, response: httpx.Response,
) -> None:
    """Translate only the trusted managed gateway's explicit task policy response."""
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
