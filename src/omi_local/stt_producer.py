"""Exact optional producer contracts shared by ordinary and whole-recording STT."""

import re
from collections.abc import Mapping

import httpx

PRODUCER_HEADERS = {
    "model_sha256": "model-sha256",
    "snapshot": "model-snapshot",
    "runtime": "runtime",
    "runtime_commit": "runtime-commit",
    "homecompute_revision": "homecompute-revision",
    "image_id": "image-id",
    "device": "device",
}
PRODUCER_PATTERNS = {
    "model_sha256": r"[0-9a-f]{64}",
    "snapshot": r"[^\s\x00-\x1f\x7f]{1,200}",
    "runtime": r"[^\s\x00-\x1f\x7f]{1,200}",
    "runtime_commit": r"[0-9a-f]{7,40}",
    "homecompute_revision": r"[0-9a-f]{40}",
    "image_id": r"sha256:[0-9a-f]{64}",
    "device": r"[^\s\x00-\x1f\x7f]{1,200}",
}


def producer_contract(value: object) -> dict[str, str]:
    """An enabled normal contract identifies every qualified producer component."""
    if not isinstance(value, dict) or value and set(value) != set(PRODUCER_HEADERS):
        raise ValueError("Expected producer must be empty or contain all seven producer fields")
    for name, expected in value.items():
        if not isinstance(expected, str) or not re.fullmatch(PRODUCER_PATTERNS[name], expected):
            raise ValueError("Invalid expected STT producer field: " + name)
    return dict(value)


def validate_producer_headers(
    headers: Mapping[str, str], expected: Mapping[str, str],
) -> dict[str, str]:
    """Return only actual headers verified against exact, nonempty expectations."""
    headers = httpx.Headers(headers)
    receipt = {}
    for name, configured in expected.items():
        suffix = PRODUCER_HEADERS[name]
        actual = headers.get("x-homecompute-stt-" + suffix)
        if not configured or actual != configured:
            raise ValueError("provenance_mismatch")
        receipt[suffix] = actual
    return receipt
