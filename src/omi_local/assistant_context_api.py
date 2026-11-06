"""Read-only, token-bound recent source export for HomeCompute context sync."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .domain import EvidenceChanged
from .service import Memory

PATH = "/v1/integrations/assistant-context"
RESOLVE_PATH = PATH + "/resolve"
HISTORY_DAYS = 30
PAGE_MAX = 20
SNAPSHOT_RECORD_MAX = 1000
RESPONSE_BYTES_MAX = 128 * 1024
SUMMARY_BYTES_MAX = 2400
COMMITMENT_BYTES_MAX = 1000
TOKEN_BYTES_MAX = 512
CURSOR_BYTES_MAX = 2048
PROFILE_ID = re.compile(r"(?:default|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
EVENT_TYPES = {"daythread.summary", "daythread.commitment"}


class ExportConfigurationError(ValueError):
    """Private export configuration is invalid; messages contain no secret values."""


class CursorError(ValueError):
    pass


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_stamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise CursorError("invalid cursor")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CursorError("invalid cursor") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise CursorError("invalid cursor")
    return result.astimezone(UTC)


def _private_token(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        raise ExportConfigurationError("assistant context export configuration invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise ExportConfigurationError("assistant context export configuration invalid") from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > TOKEN_BYTES_MAX
        ):
            raise ExportConfigurationError("assistant context export configuration invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(TOKEN_BYTES_MAX + 1)
    finally:
        os.close(descriptor)
    if len(raw) > TOKEN_BYTES_MAX:
        raise ExportConfigurationError("assistant context export configuration invalid")
    try:
        token = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise ExportConfigurationError("assistant context export configuration invalid") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,256}", token) or token.startswith("replace"):
        raise ExportConfigurationError("assistant context export configuration invalid")
    return token


class ExportConfig:
    __slots__ = ("enabled", "profile_id", "_token")

    def __init__(self, enabled: bool, profile_id: str | None = None, token: str | None = None):
        self.enabled = enabled
        self.profile_id = profile_id
        self._token = token

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> ExportConfig:
        values = os.environ if environment is None else environment
        switch = values.get("OMI_ASSISTANT_CONTEXT_ENABLED", "false").lower()
        if switch not in {"true", "false"}:
            raise ExportConfigurationError("assistant context export configuration invalid")
        if switch == "false":
            return cls(False)
        # Daythread profiles do not encode HomeCompute data domains. Admission
        # therefore requires a separate explicit personal-domain declaration.
        if values.get("OMI_ASSISTANT_CONTEXT_DOMAIN") != "personal":
            raise ExportConfigurationError("assistant context profile is not admitted")
        profile_id = values.get("OMI_ASSISTANT_CONTEXT_PROFILE_ID", "")
        token_path = values.get("OMI_ASSISTANT_CONTEXT_TOKEN_FILE", "")
        if not PROFILE_ID.fullmatch(profile_id) or not token_path:
            raise ExportConfigurationError("assistant context export configuration invalid")
        return cls(True, profile_id, _private_token(token_path))


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    return json.loads(
        raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
    )


def _cursor_encode(config: ExportConfig, cursor: dict[str, Any]) -> str:
    raw = json.dumps(cursor, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(config._token.encode(), encoded, hashlib.sha256).digest()
    return encoded.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def _cursor_decode(config: ExportConfig, value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= CURSOR_BYTES_MAX
        or value.count(".") != 1
    ):
        raise CursorError("invalid cursor")
    encoded, supplied = value.split(".", 1)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", supplied
    ):
        raise CursorError("invalid cursor")
    expected = base64.urlsafe_b64encode(
        hmac.new(config._token.encode(), encoded.encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    if not hmac.compare_digest(expected, supplied.encode("ascii", errors="ignore")):
        raise CursorError("invalid cursor")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = _strict_json(raw)
    except ValueError, UnicodeError, TypeError:
        raise CursorError("invalid cursor") from None
    expected_keys = {
        "schema_version",
        "profile_id",
        "snapshot_at",
        "window_start",
        "after",
        "record_count",
        "limited",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload["schema_version"] != 1
        or type(payload["schema_version"]) is not int
        or payload["profile_id"] != config.profile_id
        or type(payload["record_count"]) is not int
        or not 0 <= payload["record_count"] <= SNAPSHOT_RECORD_MAX
        or type(payload["limited"]) is not bool
    ):
        raise CursorError("invalid cursor")
    _parse_stamp(payload["snapshot_at"])
    _parse_stamp(payload["window_start"])
    after = payload["after"]
    if (
        not isinstance(after, list)
        or len(after) != 4
        or any(not isinstance(part, str) or len(part) > 512 for part in after)
    ):
        raise CursorError("invalid cursor")
    return payload


def _clip(value: Any, max_bytes: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(char for char in value if ord(char) >= 32 or char in "\n\t\r").strip()
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= max_bytes:
        return cleaned
    encoded = encoded[:max_bytes]
    while encoded:
        try:
            return encoded.decode("utf-8").rstrip()
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


def _source_refs(value: Any, *, decision: bool) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if decision:
        content = value
        anchors = []
        if isinstance(content, dict):
            for claim in [
                content.get("choice"),
                content.get("context"),
                content.get("rationale"),
                *(content.get("alternatives") or []),
                *(content.get("contradictions") or []),
            ]:
                if isinstance(claim, dict) and isinstance(claim.get("evidence"), list):
                    anchors.extend(claim["evidence"])
        for anchor in anchors:
            if isinstance(anchor, dict) and isinstance(anchor.get("source_id"), str):
                refs.append(
                    {
                        "id": anchor["source_id"][:200],
                        "version": str(anchor.get("source_version", ""))[:120],
                    }
                )
    elif isinstance(value, list):
        for source in value:
            if isinstance(source, dict) and isinstance(source.get("id"), str):
                refs.append(
                    {
                        "id": source["id"][:200],
                        "version": str(source.get("source_version", ""))[:120],
                    }
                )
    deduplicated = {(row["id"], row["version"]): row for row in refs}
    return [deduplicated[key] for key in sorted(deduplicated)[:20]]


def _diary_items(memory: Memory, db, snapshot_at: str, window_start: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT v.id,v.day,v.fingerprint,v.content,v.created_at FROM diary_versions v "
        "WHERE v.created_at<=? AND v.day>=? AND NOT EXISTS ("
        "SELECT 1 FROM diary_versions newer WHERE newer.day=v.day AND newer.created_at<=? AND "
        "(newer.created_at>v.created_at OR (newer.created_at=v.created_at AND newer.id>v.id))) "
        "ORDER BY v.created_at,v.id LIMIT ?",
        (snapshot_at, window_start[:10], snapshot_at, SNAPSHOT_RECORD_MAX + 1),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            content = json.loads(row["content"])
        except ValueError, TypeError:
            continue
        if not isinstance(content, dict) or not isinstance(content.get("summary"), str):
            continue
        summary = _clip(content["summary"], SUMMARY_BYTES_MAX)
        if not summary:
            continue
        raw_sources = content.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            continue
        if any(
            not isinstance(source, dict)
            or not isinstance(source.get("id"), str)
            or not source.get("id")
            or (
                source.get("reference_type") != "recording_transcription"
                and not isinstance(source.get("source_version"), str)
            )
            for source in raw_sources
        ):
            continue
        try:
            memory.store.verify_sources(db, raw_sources)
        except EvidenceChanged:
            # Historical recap versions can outlive transcript corrections; do
            # not export them once any recorded source is no longer current.
            continue
        sources = _source_refs(raw_sources, decision=False)
        if not sources:
            continue
        timestamp = row["created_at"]
        items.append(
            {
                "object_id": "diary:" + row["day"],
                "revision": 1,
                "event_type": "daythread.summary",
                "source_timestamp": timestamp,
                "modified_at": timestamp,
                "version_ref": f"diary:{row['day']}:{row['id']}:{row['fingerprint']}",
                "source_ref": f"daythread:diary/{row['day']}",
                "payload": {
                    "day": row["day"],
                    "summary": summary,
                    "source_refs": sources,
                    "pending_chunks": int(content.get("pending_chunks") or 0),
                },
            }
        )
    return items


def _decision_items(
    memory: Memory, db, snapshot_at: str, window_start: str
) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT e.decision_id,e.version,e.content,e.created_at,e.metadata "
        "FROM decision_events e WHERE e.created_at<=? AND e.created_at>=? AND NOT EXISTS ("
        "SELECT 1 FROM decision_events newer WHERE newer.decision_id=e.decision_id "
        "AND newer.created_at<=? AND newer.version>e.version) "
        "ORDER BY e.created_at,e.decision_id LIMIT ?",
        (snapshot_at, window_start, snapshot_at, SNAPSHOT_RECORD_MAX + 1),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata"])
            content = json.loads(row["content"]) if row["content"] else None
        except ValueError, TypeError:
            continue
        if (
            not isinstance(metadata, dict)
            or metadata.get("review_status") not in {"proposed", "confirmed"}
            or metadata.get("evidence_condition") == "removed"
            or not isinstance(content, dict)
        ):
            continue
        relationships = content.get("relationships")
        commitments = (
            [
                item
                for item in relationships
                if isinstance(item, dict) and item.get("kind") == "commitment"
            ]
            if isinstance(relationships, list)
            else []
        )
        choice = content.get("choice")
        if (
            not commitments
            or not isinstance(choice, dict)
            or not isinstance(choice.get("text"), str)
        ):
            continue
        refs = _source_refs(content, decision=True)
        if not refs:
            continue
        try:
            if any(not ref["version"] for ref in refs):
                continue
            memory.store.verify_sources(
                db, [{"id": ref["id"], "source_version": ref["version"]} for ref in refs]
            )
        except EvidenceChanged:
            continue
        timestamp = row["created_at"]
        object_id = "decision:" + row["decision_id"]
        items.append(
            {
                "object_id": object_id,
                "revision": int(row["version"]),
                "event_type": "daythread.commitment",
                "source_timestamp": timestamp,
                "modified_at": timestamp,
                "version_ref": f"decision:{row['decision_id']}:v{row['version']}",
                "source_ref": f"daythread:decision/{row['decision_id']}",
                "payload": {
                    "text": _clip(choice["text"], COMMITMENT_BYTES_MAX),
                    "review_status": metadata["review_status"],
                    "state": metadata.get("state", ""),
                    "relationships": [
                        {"kind": "commitment", "label": _clip(item.get("label", ""), 200)}
                        for item in commitments[:8]
                    ],
                    "source_refs": refs,
                },
            }
        )
    return items


def _resolve_saved_object(
    memory: Memory, db, object_id: str, snapshot_at: str
) -> tuple[str, dict[str, Any] | None]:
    """Resolve one known ID directly against its saved source versions."""
    cutoff = datetime.now(UTC) - timedelta(days=HISTORY_DAYS)
    if object_id.startswith("diary:"):
        day = object_id.removeprefix("diary:")
        row = db.execute(
            "SELECT id,day,fingerprint,content,created_at FROM diary_versions WHERE day=? "
            "AND created_at<=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (day, snapshot_at),
        ).fetchone()
        if row is None:
            return "deleted", None
        if _parse_stamp(row["created_at"]) < cutoff:
            return "expired-window", None
        try:
            content = json.loads(row["content"])
            raw_sources = content.get("sources") if isinstance(content, dict) else None
            if (
                not isinstance(content, dict)
                or not isinstance(content.get("summary"), str)
                or not isinstance(raw_sources, list)
                or not raw_sources
                or any(
                    not isinstance(source, dict)
                    or not isinstance(source.get("id"), str)
                    or not source.get("id")
                    or (
                        source.get("reference_type") != "recording_transcription"
                        and not isinstance(source.get("source_version"), str)
                    )
                    for source in raw_sources
                )
            ):
                return "invalidated", None
            memory.store.verify_sources(db, raw_sources)
            sources = _source_refs(raw_sources, decision=False)
            if not sources:
                return "invalidated", None
            timestamp = row["created_at"]
            return "valid", {
                "object_id": object_id,
                "revision": 1,
                "event_type": "daythread.summary",
                "source_timestamp": timestamp,
                "modified_at": timestamp,
                "version_ref": f"diary:{day}:{row['id']}:{row['fingerprint']}",
                "source_ref": f"daythread:diary/{day}",
                "payload": {
                    "day": day,
                    "summary": _clip(content["summary"], SUMMARY_BYTES_MAX),
                    "source_refs": sources,
                    "pending_chunks": int(content.get("pending_chunks") or 0),
                },
            }
        except EvidenceChanged, ValueError, TypeError, KeyError:
            return "invalidated", None
    decision_id = object_id.removeprefix("decision:")
    row = db.execute(
        "SELECT e.decision_id,e.version,e.content,e.created_at,e.metadata FROM decision_events e "
        "WHERE e.decision_id=? AND e.created_at<=? ORDER BY e.version DESC LIMIT 1",
        (decision_id, snapshot_at),
    ).fetchone()
    if row is None:
        return "deleted", None
    if _parse_stamp(row["created_at"]) < cutoff:
        return "expired-window", None
    try:
        metadata = json.loads(row["metadata"])
        content = json.loads(row["content"]) if row["content"] else None
        if (
            not isinstance(metadata, dict)
            or metadata.get("review_status") not in {"proposed", "confirmed"}
            or metadata.get("evidence_condition") == "removed"
            or not isinstance(content, dict)
        ):
            return "invalidated", None
        relationships = content.get("relationships")
        commitments = (
            [
                item
                for item in relationships
                if isinstance(item, dict) and item.get("kind") == "commitment"
            ]
            if isinstance(relationships, list)
            else []
        )
        choice = content.get("choice")
        refs = _source_refs(content, decision=True)
        if (
            not commitments
            or not isinstance(choice, dict)
            or not isinstance(choice.get("text"), str)
            or not refs
        ):
            return "invalidated", None
        if any(not ref["version"] for ref in refs):
            return "invalidated", None
        memory.store.verify_sources(
            db, [{"id": ref["id"], "source_version": ref["version"]} for ref in refs]
        )
        timestamp = row["created_at"]
        return "valid", {
            "object_id": object_id,
            "revision": int(row["version"]),
            "event_type": "daythread.commitment",
            "source_timestamp": timestamp,
            "modified_at": timestamp,
            "version_ref": f"decision:{decision_id}:v{row['version']}",
            "source_ref": f"daythread:decision/{decision_id}",
            "payload": {
                "text": _clip(choice["text"], COMMITMENT_BYTES_MAX),
                "review_status": metadata["review_status"],
                "state": metadata.get("state", ""),
                "relationships": [
                    {"kind": "commitment", "label": _clip(item.get("label", ""), 200)}
                    for item in commitments[:8]
                ],
                "source_refs": refs,
            },
        }
    except EvidenceChanged, ValueError, TypeError, KeyError:
        return "invalidated", None
    return "invalidated", None


def _snapshot(
    memory: Memory, config: ExportConfig, cursor: dict[str, Any] | None, limit: int
) -> dict[str, Any]:
    now = datetime.now(UTC)
    snapshot_at = cursor["snapshot_at"] if cursor else _stamp(now)
    window_start = cursor["window_start"] if cursor else _stamp(now - timedelta(days=HISTORY_DAYS))
    with memory.store.connect() as db:
        db.execute("BEGIN")
        diaries = _diary_items(memory, db, snapshot_at, window_start)
        decisions = _decision_items(memory, db, snapshot_at, window_start)
        concurrent_diary = db.execute(
            "SELECT 1 FROM diary_versions WHERE created_at>? LIMIT 1", (snapshot_at,)
        ).fetchone()
        concurrent_decision = db.execute(
            "SELECT 1 FROM decision_events WHERE created_at>? LIMIT 1", (snapshot_at,)
        ).fetchone()
    all_items = diaries + decisions
    all_items.sort(
        key=lambda item: (
            item["modified_at"],
            item["event_type"],
            item["object_id"],
            item["version_ref"],
        )
    )
    limited = (
        len(all_items) > SNAPSHOT_RECORD_MAX
        or len(diaries) > SNAPSHOT_RECORD_MAX
        or len(decisions) > SNAPSHOT_RECORD_MAX
    )
    if len(all_items) > SNAPSHOT_RECORD_MAX:
        all_items = all_items[-SNAPSHOT_RECORD_MAX:]
    total = len(all_items)
    consistent = not bool(concurrent_diary or concurrent_decision)
    if cursor:
        consistent = consistent and total == cursor["record_count"] and limited == cursor["limited"]
        after = tuple(cursor["after"])
        all_items = [
            item
            for item in all_items
            if (item["modified_at"], item["event_type"], item["object_id"], item["version_ref"])
            > after
        ]
    page = all_items[:limit]
    has_more = len(all_items) > limit
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _cursor_encode(
            config,
            {
                "schema_version": 1,
                "profile_id": config.profile_id,
                "snapshot_at": snapshot_at,
                "window_start": window_start,
                "after": [
                    last["modified_at"],
                    last["event_type"],
                    last["object_id"],
                    last["version_ref"],
                ],
                "record_count": total,
                "limited": limited,
            },
        )
    window_complete = not has_more and not limited and consistent
    return {
        "schema_version": 1,
        "source": "daythread",
        "snapshot_at": snapshot_at,
        "window": {"start": window_start, "end": snapshot_at, "days": HISTORY_DAYS},
        "coverage": {
            "window_complete": window_complete,
            "deletion_complete": False,
            "snapshot_consistent": consistent,
            "record_limit_reached": limited,
            "record_count": total,
            "returned_records": len(page),
        },
        "items": page,
        "next_cursor": next_cursor,
    }


def create_router(get_profiles: Callable[[], Any], config: ExportConfig) -> APIRouter:
    router = APIRouter()

    def response(status: int, body: dict) -> JSONResponse:
        result = JSONResponse(
            status_code=status, content=body, headers={"Cache-Control": "no-store"}
        )
        return result

    @router.get(PATH)
    def export(request: Request) -> JSONResponse:
        if not config.enabled:
            return response(404, {"detail": "Not found"})
        authorization = request.headers.getlist("authorization")
        expected = "Bearer " + config._token
        if len(authorization) != 1 or not hmac.compare_digest(
            authorization[0].encode(), expected.encode()
        ):
            return response(401, {"detail": "Unauthorized"})
        if request.headers.getlist("x-omi-profile") or request.query_params.getlist("profile_id"):
            return response(400, {"detail": "Profile selection is fixed by this credential"})
        allowed_query = {"cursor", "limit"}
        if set(request.query_params.keys()) - allowed_query:
            return response(400, {"detail": "Unsupported query parameter"})
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params.keys()):
            return response(400, {"detail": "Duplicate query parameter"})
        raw_limit = request.query_params.get("limit", "20")
        if (
            len(raw_limit) > 2
            or not re.fullmatch(r"[0-9]+", raw_limit)
            or not 1 <= int(raw_limit) <= PAGE_MAX
        ):
            return response(400, {"detail": "Bounded page size required"})
        raw_cursor = request.query_params.get("cursor")
        try:
            cursor = _cursor_decode(config, raw_cursor)
        except CursorError:
            return response(400, {"detail": "Invalid cursor"})
        profiles = get_profiles()
        runtime = profiles.runtimes.get(config.profile_id)
        if runtime is None:
            return response(404, {"detail": "Not found"})
        try:
            body = _snapshot(runtime.memory, config, cursor, int(raw_limit))
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > RESPONSE_BYTES_MAX:
                return response(503, {"detail": "Export temporarily unavailable"})
            result = response(200, body)
            result.headers["X-Content-Type-Options"] = "nosniff"
            return result
        except Exception:
            # The route never writes provider errors or source text to logs.
            return response(503, {"detail": "Export temporarily unavailable"})

    @router.post(RESOLVE_PATH)
    async def resolve(request: Request) -> JSONResponse:
        """Resolve only IDs already known to an authenticated importer."""
        if not config.enabled:
            return response(404, {"detail": "Not found"})
        authorization = request.headers.getlist("authorization")
        expected = "Bearer " + config._token
        if len(authorization) != 1 or not hmac.compare_digest(
            authorization[0].encode(), expected.encode()
        ):
            return response(401, {"detail": "Unauthorized"})
        if (
            request.headers.getlist("x-omi-profile")
            or request.query_params
            or request.headers.getlist("content-type") != ["application/json"]
        ):
            return response(400, {"detail": "Profile selection and extra parameters are refused"})
        try:
            raw = await request.body()
            if len(raw) > 16_384:
                raise ValueError
            body = _strict_json(raw)
            ids = (
                body.get("object_ids")
                if isinstance(body, dict) and set(body) == {"object_ids"}
                else None
            )
            if (
                not isinstance(ids, list)
                or not 1 <= len(ids) <= PAGE_MAX
                or any(not isinstance(value, str) or not 1 <= len(value) <= 220 for value in ids)
                or len(set(ids)) != len(ids)
                or any(
                    not re.fullmatch(
                        r"(?:diary:\d{4}-\d{2}-\d{2}|decision:[A-Za-z0-9_-]{1,180})", value
                    )
                    for value in ids
                )
            ):
                raise ValueError
        except ValueError, UnicodeError, TypeError:
            return response(400, {"detail": "Bounded known object IDs required"})
        runtime = get_profiles().runtimes.get(config.profile_id)
        if runtime is None:
            return response(404, {"detail": "Not found"})
        try:
            with runtime.memory.store.connect() as db:
                db.execute("BEGIN")
                results = []
                snapshot_at = _stamp(datetime.now(UTC))
                for object_id in ids:
                    status, item = _resolve_saved_object(runtime.memory, db, object_id, snapshot_at)
                    results.append(
                        {
                            "object_id": object_id,
                            "status": status,
                            **({"item": item} if item is not None else {}),
                        }
                    )
            return response(200, {"schema_version": 1, "source": "daythread", "results": results})
        except Exception:
            return response(503, {"detail": "Resolve temporarily unavailable"})

    return router
