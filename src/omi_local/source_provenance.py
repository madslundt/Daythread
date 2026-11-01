"""Derive pendant-copy disclosure from the immutable, cross-client ring identity."""

import hashlib
import re
from uuid import UUID

RING_FIELDS = {"provenance_kind", "timestamp_precision_seconds", "device_copy_retained"}
MAX_U64 = 2**64 - 1


def ring_metadata(row: dict) -> dict:
    source = row.get("source_id")
    if row.get("source_kind") != "manual_upload" or not isinstance(source, str):
        return {}
    if not source.startswith("omi-ring:"):
        return {}
    parts = source[len("omi-ring:"):].rsplit(":", 2)
    if len(parts) != 3:
        return {}
    device, ring_text, sequence_text = parts
    if not device or device != row.get("device_id"):
        return {}
    # Swift UInt64/Java unsigned strings emit canonical decimal values.
    if not all(re.fullmatch(r"0|[1-9][0-9]{0,19}", value)
               for value in (ring_text, sequence_text)):
        return {}
    ring, sequence = int(ring_text), int(sequence_text)
    if not 0 < ring <= MAX_U64 or sequence > MAX_U64:
        return {}
    try:
        digest = bytearray(hashlib.sha256(source.encode("utf-8")).digest()[:16])
    except UnicodeEncodeError:
        return {}
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    try:
        if UUID(str(row["id"])) != UUID(bytes=bytes(digest)):
            return {}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}
    return {"provenance_kind": "omi_ring", "timestamp_precision_seconds": 1,
            "device_copy_retained": True}


def public_metadata(row: dict, metadata: dict) -> dict:
    # Imported metadata alone must never claim verified ring-copy provenance.
    clean = {key: value for key, value in metadata.items() if key not in RING_FIELDS}
    return clean | ring_metadata(row)
