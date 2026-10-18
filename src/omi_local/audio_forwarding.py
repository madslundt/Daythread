"""Optional binary webhook adapter; no capture clock or device ID is supplied upstream."""

import asyncio
import io
import re
import secrets
import sqlite3
import sys
import wave
from array import array
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, HTTPException, Request, Response

from .domain import stamp, utc_now

PATH = "/v1/integrations/omi/audio"
SAMPLE_RATES = (8000, 16000)
MAX_BYTES = 4_000_000
MAX_SECONDS = 120


class RedactForwardingToken:
    """Remove query credentials before routing and uvicorn's request access logging.

    Proxies ahead of this application must independently redact query strings.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(PATH):
            pairs = parse_qsl(
                scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True
            )
            tokens = [value for key, value in pairs if key == "token"]
            scope.setdefault("state", {})["forwarding_token"] = (
                tokens[0] if len(tokens) == 1 else ""
            )
            scope["query_string"] = urlencode([(k, v) for k, v in pairs if k != "token"]).encode()
        await self.app(scope, receive, send)


def pcm_wav(pcm: bytes, sample_rate: int) -> bytes:
    if sample_rate == 8000:
        # Exact 2x linear upsampling; preserve duration, signed values and LE byte order.
        samples = array("h", pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        expanded = array("h")
        for index, value in enumerate(samples):
            following = samples[min(index + 1, len(samples) - 1)]
            expanded.extend((value, (value + following) // 2))
        if sys.byteorder != "little":
            expanded.byteswap()
        pcm = expanded.tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(pcm)
    return output.getvalue()


def forwarding_routes(memory) -> APIRouter:
    router = APIRouter()
    last_failure = None  # Sanitized diagnostics for this process; never an upstream response body.
    # Bound buffered bodies and disk acceptance work across simultaneous senders.
    slots = asyncio.Semaphore(4)

    def fail(status: int, detail: str):
        nonlocal last_failure
        last_failure = {"at": stamp(utc_now()), "status": status, "detail": detail}
        raise HTTPException(status, detail)

    @router.get(PATH + "/status")
    def status(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        current = memory()
        enabled = current.settings.audio_forwarding_enabled
        configured = bool(current.settings.audio_forwarding_token.get_secret_value())
        return {
            "enabled": enabled,
            "configured": configured,
            "ready": enabled and configured,
            "worker_enabled": current.settings.worker_enabled,
            "sample_rates": SAMPLE_RATES,
            "max_bytes": MAX_BYTES,
            "max_seconds": MAX_SECONDS,
            "timestamp_basis": "receipt_end_estimate",
            "last_failure": last_failure,
            **current.store.forwarding_status(),
        }

    @router.post(PATH)
    async def receive_audio(request: Request) -> dict:
        current = memory()
        if not current.settings.audio_forwarding_enabled:
            fail(404, "Official audio receiver is disabled in Settings → Omi connection")
        secret = current.settings.audio_forwarding_token.get_secret_value()
        if not secret:
            fail(503, "Set OMI_AUDIO_FORWARDING_TOKEN on the server and restart")
        supplied = getattr(request.state, "forwarding_token", "")
        if not secrets.compare_digest(supplied.encode(), secret.encode()):
            # Unauthenticated traffic must not overwrite operator diagnostics.
            raise HTTPException(401, "Invalid audio receiver token")
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != ("application/octet-stream")
            or request.headers.get("content-encoding", "identity").lower() != "identity"
        ):
            fail(415, "Send uncompressed application/octet-stream PCM16 little-endian mono")
        params = request.query_params
        if len(params.getlist("uid")) != 1 or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", params.get("uid", "")
        ):
            fail(422, "Supply one uid (1–128 letters, digits, underscore or hyphen)")
        if len(params.getlist("sample_rate")) != 1 or params.get("sample_rate") not in {
            str(rate) for rate in SAMPLE_RATES
        }:
            fail(422, "Supported sample_rate values: 8000, 16000")
        rate = int(params["sample_rate"])
        delivery_keys = request.headers.getlist("idempotency-key")
        delivery_key = delivery_keys[0] if delivery_keys else None
        if len(delivery_keys) > 1 or (
            delivery_key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", delivery_key)
        ):
            fail(422, "Idempotency-Key must contain 1–128 letters, digits, underscore or hyphen")
        limit = min(MAX_BYTES, rate * 2 * MAX_SECONDS)
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                fail(422, "Invalid Content-Length")
            if len(length) > 10 or int(length) > limit:
                fail(413, "Maximum PCM payload is 120 seconds and 4 MB")
        if slots.locked():
            fail(429, "Audio receiver is busy; retry this delivery with the same Idempotency-Key")
        async with slots:
            data = bytearray()

            async def read_body():
                async for part in request.stream():
                    if len(data) + len(part) > limit:
                        fail(413, "Maximum PCM payload is 120 seconds and 4 MB")
                    data.extend(part)

            try:
                await asyncio.wait_for(read_body(), timeout=15)
            except TimeoutError:
                fail(408, "Audio body timed out; retry the same delivery")
            if not data or len(data) % 2:
                fail(422, "PCM payload must be nonempty and aligned to 16-bit samples")
            if length is not None and int(length) != len(data):
                fail(422, "Content-Length does not match PCM payload")
            received_at = utc_now()

            def accept():
                pcm = bytes(data)
                return current.store.accept_forwarded_audio(
                    params["uid"], rate, pcm, pcm_wav(pcm, rate), received_at, delivery_key
                )

            try:
                result = await asyncio.to_thread(accept)
            except ValueError:
                fail(409, "Delivery key already accepted with different audio or rate")
            except OSError, sqlite3.Error:
                fail(503, "Audio could not be persisted; check disk space/permissions and retry")
        return result | {"timestamp_basis": "receipt_end_estimate"}

    return router
