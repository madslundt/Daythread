"""Private request diagnostics without changing speech-model request fields."""

import hashlib
import json
import re
from email import policy
from email.parser import BytesParser

MAX_REQUEST_BYTES = 4 * 1024 * 1024 + 65536
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
VISIBLE_FIELDS = {"model", "response_format", "language", "temperature", "temperature_inc",
                  "translate", "beam_size", "best_of", "no_context", "max_context"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_evidence(content: bytes, content_type: str) -> dict:
    """Fingerprint actual multipart audio and hash all unrecognized text fields."""
    if len(content) > MAX_REQUEST_BYTES or len(content_type) > 500:
        raise ValueError("Benchmark request exceeds evidence bounds")
    message = BytesParser(policy=policy.default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("ascii")
        + b"\r\n\r\n" + content,
    )
    if message.get_content_type() != "multipart/form-data":
        raise ValueError("Expected a multipart transcription request")
    fields, audio = {}, None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        value = part.get_payload(decode=True)
        if not isinstance(name, str) or not isinstance(value, bytes):
            raise ValueError("Invalid multipart evidence field")
        if name == "file":
            if audio is not None:
                raise ValueError("Multiple audio files in transcription request")
            audio = value
        else:
            if name in fields or len(fields) >= 32 or len(name) > 100:
                raise ValueError("Invalid or duplicate request fields")
            if name in VISIBLE_FIELDS and len(value) <= 200:
                fields[name] = value.decode("utf-8")
            else:
                fields[name] = {"sha256": sha256(value), "bytes": len(value)}
    if audio is None:
        raise ValueError("Missing transcription request audio")
    semantic = {"audio_sha256": sha256(audio), "fields": fields}
    return {
        **semantic, "audio_bytes": len(audio), "body_sha256": sha256(content),
        "semantic_sha256": sha256(json.dumps(semantic, sort_keys=True).encode()),
    }


def response_evidence(content: bytes) -> dict:
    """Keep successful transcription text; do not copy arbitrary provider metadata."""
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Benchmark response exceeds evidence bounds")
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise ValueError("Missing transcription response text")
    text = payload["text"]
    if len(text) > 20000:
        raise ValueError("Transcription response text exceeds evidence bounds")
    language = payload.get("language")
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z_-]{2,32}", language):
        language = None
    return {
        "body_sha256": sha256(content), "text": text,
        "exact_text_sha256": sha256(text.encode()), "reported_language": language,
    }
