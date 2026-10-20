"""Content-addressed manual upload plans, scoped to one archive."""

import hashlib
import json
import math
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from .domain import MAX_RECORDING_PARTS, MAX_RECORDING_SECONDS, stamp, utc_now
from .source_matching import MANUAL_UPLOAD_SQL, UploadGrouping
from .vendor_catalog import file_vendor

SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_uploads (fingerprint TEXT PRIMARY KEY, plan TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recording_upload_files (
 digest TEXT PRIMARY KEY, fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recording_upload_chunks (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, part_index INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS recording_upload_deleted AFTER DELETE ON audio BEGIN
 DELETE FROM recording_upload_files WHERE fingerprint IN
   (SELECT fingerprint FROM recording_upload_chunks WHERE id=OLD.id);
 DELETE FROM recording_uploads WHERE fingerprint IN
   (SELECT fingerprint FROM recording_upload_chunks WHERE id=OLD.id);
 DELETE FROM recording_upload_chunks WHERE fingerprint NOT IN
   (SELECT fingerprint FROM recording_uploads);
END;
"""


class FileCheck(BaseModel):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrepareUpload(BaseModel):
    vendor_id: str = Field(default="manual", max_length=80)
    file_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    digests: list[str] = Field(min_length=1, max_length=MAX_RECORDING_PARTS)
    sample_count: int = Field(ge=1, le=16000 * MAX_RECORDING_SECONDS)
    source_id: str = Field(min_length=1, max_length=255)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    conversation_source_id: UUID | None = None

    @field_validator("vendor_id")
    @classmethod
    def valid_vendor(cls, value: str) -> str:
        return file_vendor(value)

    @model_validator(mode="after")
    def valid_audio(self):
        if any(len(d) != 64 or any(c not in "0123456789abcdef" for c in d)
               for d in self.digests):
            raise ValueError("Invalid audio fingerprint")
        if len(self.digests) != math.ceil(self.sample_count / (16000 * 120)):
            raise ValueError("Audio parts do not match the recording length")
        if abs((self.ended_at - self.started_at).total_seconds()
               - self.sample_count / 16000) > 0.001:
            raise ValueError("Capture bounds do not match the recording length")
        if self.ended_at.timestamp() > utc_now().timestamp() + 30:
            raise ValueError("Capture time is in the future")
        return self


def available(db, plan):
    from .blob_storage import audio_is_present

    result = []
    for part in plan["parts"]:
        row = db.execute("SELECT * FROM audio WHERE id=?", (part["id"],)).fetchone()
        result.append(bool(row and row["digest"] == part["digest"]
                           and row["audio_available"] and row["state"] != "expired"
                           and audio_is_present(row)))
    return result


def legacy_match(db, digests):
    """Match the entire ordered recording, never just a shared first chunk."""
    for first in db.execute(
        f"SELECT * FROM audio WHERE digest=? AND {MANUAL_UPLOAD_SQL}", (digests[0],),
    ).fetchall():
        metadata = json.loads(first["source_metadata"])
        recording = metadata.get("recording_id")
        if recording:
            if metadata.get("part_index") != 0 or metadata.get("part_count") != len(digests):
                continue
            rows = db.execute(
                "SELECT * FROM audio WHERE json_extract(source_metadata,'$.recording_id')=? "
                "ORDER BY json_extract(source_metadata,'$.part_index')", (recording,),
            ).fetchall()
        else:
            rows = [first] if len(digests) == 1 else []
        if [row["digest"] for row in rows] != digests:
            continue
        plan = {"recording_id": recording or str(uuid4()), "source_id": first["source_id"],
                "started_at": first["started_at"], "ended_at": rows[-1]["ended_at"],
                "conversation_source_id": metadata.get("conversation_source_id"),
                "legacy": True,
                "parts": [{"id": row["id"], "digest": row["digest"]} for row in rows]}
        if all(available(db, plan)):
            return plan
    return None


class RecordingUploadStore:
    def check_recording_file(self, digest: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT plan FROM recording_uploads JOIN recording_upload_files USING(fingerprint) "
                "WHERE digest=?", (digest,),
            ).fetchone()
            if row:
                plan = json.loads(row["plan"])
                if all(available(db, plan)):
                    return {"duplicate": True, "source_id": plan["source_id"]}
        return {"duplicate": False}

    def prepare_recording_upload(self, body: PrepareUpload) -> dict:
        fingerprint = hashlib.sha256("".join(body.digests).encode()).hexdigest()
        target = str(body.conversation_source_id) if body.conversation_source_id else None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT plan FROM recording_uploads WHERE fingerprint=?",
                             (fingerprint,)).fetchone()
            plan = json.loads(row["plan"]) if row else None
            if plan:
                # Deleted/expired audio may be imported again. Interrupted uploads keep
                # their original IDs, times and provenance across dialogs and clients.
                invalid = bool(plan.get("legacy") and not all(available(db, plan)))
                for index, part in enumerate(plan["parts"]):
                    audio = db.execute("SELECT * FROM audio WHERE id=?", (part["id"],)).fetchone()
                    received = db.execute(
                        "SELECT 1 FROM upload_parts WHERE recording_id=? AND part_index=?",
                        (plan["recording_id"], index),
                    ).fetchone()
                    invalid |= bool((audio and not audio["audio_available"])
                                    or (audio is None and received))
                if invalid:
                    plan = None
            if plan is None:
                plan = legacy_match(db, body.digests)
            if plan is None:
                if target and not db.execute(
                    "SELECT 1 FROM audio WHERE id=? AND state!='expired'",
                    (target,),
                ).fetchone():
                    raise ValueError("The selected conversation source is no longer available")
                plan = {"recording_id": str(uuid4()), "source_id": body.source_id,
                        "vendor_id": body.vendor_id,
                        "started_at": stamp(body.started_at), "ended_at": stamp(body.ended_at),
                        "sample_count": body.sample_count,
                        "conversation_source_id": target,
                        "parts": [{"id": str(uuid4()), "digest": digest}
                                  for digest in body.digests]}
            if plan.get("sample_count", body.sample_count) != body.sample_count:
                raise ValueError("Audio fingerprint has a conflicting recording length")
            db.execute("INSERT OR REPLACE INTO recording_uploads VALUES(?,?)",
                       (fingerprint, json.dumps(plan)))
            db.execute("INSERT OR REPLACE INTO recording_upload_files VALUES(?,?)",
                       (body.file_digest, fingerprint))
            db.execute("DELETE FROM recording_upload_chunks WHERE fingerprint=?", (fingerprint,))
            db.executemany("INSERT INTO recording_upload_chunks VALUES(?,?,?)",
                           [(part["id"], fingerprint, index)
                            for index, part in enumerate(plan["parts"])])
            uploaded = available(db, plan)
            if all(uploaded):
                return {"status": "duplicate", "source_id": plan["source_id"]}
            return {**plan, "status": "upload", "parts": [
                {**part, "uploaded": done} for part, done in zip(plan["parts"], uploaded)
            ]}

    @staticmethod
    def validate_recording_upload(db, key, digest, device, source_kind, source_id, metadata, at):
        row = db.execute(
            "SELECT plan,part_index FROM recording_upload_chunks "
            "JOIN recording_uploads USING(fingerprint) WHERE id=?", (key,),
        ).fetchone()
        if row is None:
            return
        plan, index = json.loads(row["plan"]), row["part_index"]
        if plan.get("legacy"):
            return  # Older clients keep their original receipt and metadata contract.
        expected = {"recording_id": plan["recording_id"], "part_index": index,
                    "part_count": len(plan["parts"]), "recording_started_at": plan["started_at"],
                    "recording_ended_at": plan["ended_at"]}
        if plan["conversation_source_id"]:
            expected["conversation_source_id"] = plan["conversation_source_id"]
        if plan.get("vendor_id", "manual") != "manual":
            expected["vendor_id"] = plan["vendor_id"]
        expected = UploadGrouping.model_validate(expected).model_dump(
            exclude_none=True, mode="json",
        )
        part_start = datetime.fromisoformat(plan["started_at"]) + timedelta(seconds=index * 120)
        if (digest != plan["parts"][index]["digest"] or device != "manual-web"
                or source_kind != "manual_upload" or source_id != plan["source_id"]
                or metadata != expected or datetime.fromisoformat(at) != part_start):
            raise ValueError("Audio part does not match its upload plan")


def recording_upload_routes(memory):
    router = APIRouter(prefix="/v1/recording-uploads")

    @router.post("/check")
    def check(body: FileCheck) -> dict:
        return memory().store.check_recording_file(body.digest)

    @router.post("/prepare")
    def prepare(body: PrepareUpload) -> dict:
        try:
            return memory().store.prepare_recording_upload(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
