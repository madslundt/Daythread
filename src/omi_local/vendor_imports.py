"""Shared interface and archive policy for read-only vendor transcript adapters."""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .domain import stamp, utc_now

if TYPE_CHECKING:
    from .settings import Settings
    from .store import Store

TRANSCRIPT_IMPORT_SQL = "(source_kind GLOB '*_api' OR source_kind GLOB '*_export')"


def transcript_import(source_kind: str) -> bool:
    return source_kind.endswith(("_api", "_export"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS vendor_import_deletions (
 source_kind TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(source_kind,source_id)
);
CREATE TRIGGER IF NOT EXISTS vendor_import_deleted BEFORE DELETE ON audio
WHEN OLD.source_kind GLOB '*_api' OR OLD.source_kind GLOB '*_export' BEGIN
 INSERT OR IGNORE INTO vendor_import_deletions VALUES(OLD.source_kind,OLD.source_id);
END;
"""


class VendorError(Exception):
    def __init__(self, detail: str, status: int = 502):
        self.detail, self.status = detail, status
        super().__init__(detail)


class VendorImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: AwareDatetime | None = None
    end_date: AwareDatetime | None = None
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def date_range(self):
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValueError("Import start must be before end")
        return self


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(max_length=20000)
    speaker_name: str | None = Field(default=None, max_length=200)
    speaker_id: int | str | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Invalid segment interval")
        return self


class VendorRecording(BaseModel):
    """Only source speech belongs here; vendor summaries are never evidence."""

    source_id: str = Field(min_length=1, max_length=200)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    text: str = Field(min_length=1, max_length=200000)
    segments: list[TranscriptSegment] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def valid_recording(self):
        if not self.started_at < self.ended_at <= utc_now() + timedelta(seconds=30):
            raise ValueError("Invalid recording interval")
        duration = (self.ended_at - self.started_at).total_seconds()
        if duration > 7 * 86400 or any(s.end > duration + 1 for s in self.segments):
            raise ValueError("Transcript exceeds recording interval")
        if not self.text.strip():
            raise ValueError("Empty transcript")
        return self


@dataclass(frozen=True)
class VendorPage:
    recordings: list[VendorRecording | None]
    has_more: bool = False
    next_offset: int = 0
    next_cursor: str | None = None


class TranscriptAdapter:
    """Implement fetch_page; all archive behavior remains shared across brands."""

    vendor_id: str
    label: str
    endpoint: str
    key_setting: str
    identity_namespace: str
    source_kind: str = ""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, store: Store):
        self.key = getattr(settings, self.key_setting).get_secret_value().strip()
        self.client, self.store = client, store

    def status(self) -> dict:
        return {
            "configured": bool(self.key),
            "mode": "developer-api",
            "endpoint": self.endpoint,
            "imports_audio": False,
        }

    async def fetch_page(self, request: VendorImportRequest) -> VendorPage:
        raise NotImplementedError

    async def import_page(self, request: VendorImportRequest) -> dict:
        if not self.key:
            raise VendorError(
                f"Save your {self.label} API key in server settings to enable import", 503
            )
        try:
            page = await self.fetch_page(request)
            if len(page.recordings) > request.limit:
                raise ValueError("Too many recordings")
            if page.has_more and not (
                request.offset < page.next_offset <= 1_000_000
                or (page.next_cursor and page.next_cursor != request.cursor)
            ):
                raise ValueError("Import pagination did not advance")
            return self.persist_page(page)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise VendorError(
                f"{self.label} returned invalid recording data; nothing was imported"
            ) from exc

    def persist_page(self, page: VendorPage) -> dict:
        return TranscriptArchive(
            self.store, self.vendor_id, self.identity_namespace, self.source_kind
        ).persist_page(page)

    async def get_json(
        self, url: str, *, params: dict | None = None
    ) -> tuple[object, httpx.Headers]:
        """Bounded credentialed reads. Never follow redirects or expose remote error bodies."""
        try:
            async with self.client.stream(
                "GET",
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.key}"},
                follow_redirects=False,
                timeout=60,
            ) as response:
                if response.status_code in (401, 403):
                    raise VendorError(
                        f"{self.label} rejected the API key; check its read permission"
                    )
                if response.status_code == 429:
                    raise VendorError(
                        f"{self.label} API rate limit reached; wait before retrying", 503
                    )
                if response.status_code != 200:
                    raise VendorError(f"{self.label} API could not return recordings; retry later")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 16_000_000:
                        raise VendorError(
                            f"{self.label} response is too large; import fewer recordings"
                        )
                return json.loads(data), response.headers
        except httpx.HTTPError as exc:
            raise VendorError(
                f"{self.label} API is unavailable; check connectivity and retry"
            ) from exc


class TranscriptArchive:
    """One archive policy for both cloud adapters and offline transcript exports."""

    def __init__(
        self, store: Store, vendor_id: str, identity_namespace: str, source_kind: str = ""
    ):
        self.store = store
        self.vendor_id = vendor_id
        self.identity_namespace = identity_namespace
        self.source_kind = source_kind

    def persist_page(self, page: VendorPage) -> dict:
        from .service import public_source

        sources = [
            VendorRecording.model_validate(s.model_dump()) if s else None for s in page.recordings
        ]
        source_kind = self.source_kind or f"{self.vendor_id}_api"
        imported, existing, skipped, items = 0, 0, 0, []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for source in sources:
                if source is None:
                    skipped += 1
                    continue
                deleted = db.execute(
                    "SELECT 1 FROM vendor_import_deletions WHERE source_kind=? AND source_id=?",
                    (source_kind, source.source_id),
                ).fetchone()
                if self.vendor_id == "omi":
                    deleted = (
                        deleted
                        or db.execute(
                            "SELECT 1 FROM import_deletions WHERE source_id=?",
                            (source.source_id,),
                        ).fetchone()
                    )
                if deleted:
                    skipped += 1
                    continue
                key = str(uuid5(NAMESPACE_URL, f"{self.identity_namespace}/{source.source_id}"))
                old = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
                if old:
                    existing += 1
                    items.append(public_source(dict(old)))
                    continue
                db.execute(
                    "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text,"
                    "segments,audio_available,source_kind,source_id,source_metadata) "
                    "VALUES(?,?,?,?,?,?,'done',?,?,0,?,?,?)",
                    (
                        key,
                        source_kind.replace("_", "-"),
                        stamp(source.started_at),
                        stamp(source.ended_at),
                        hashlib.sha256(source.text.encode()).hexdigest(),
                        str(self.store.audio_dir / f"{key}.wav"),
                        source.text,
                        json.dumps([s.model_dump() for s in source.segments]),
                        source_kind,
                        source.source_id,
                        json.dumps({"vendor_id": self.vendor_id}),
                    ),
                )
                db.execute("INSERT INTO transcript_search VALUES(?,?)", (key, source.text))
                self.store.refresh_source_matches(db, key)
                row = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
                items.append(public_source(dict(row)))
                imported += 1
        return {
            "imported": imported,
            "existing": existing,
            "skipped": skipped,
            "items": items,
            "has_more": page.has_more,
            "next_offset": page.next_offset,
            "next_cursor": page.next_cursor,
        }

