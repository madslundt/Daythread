"""Durable acceptance of Omi audio webhooks, independent of inference."""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

from .domain import stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS omi_audio_receipts (
 delivery_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, audio_id TEXT NOT NULL,
 accepted_at TEXT NOT NULL, duration REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS omi_audio_receipts_time ON omi_audio_receipts(accepted_at);
"""


class ForwardingStore:
    def accept_forwarded_audio(
        self,
        uid: str,
        sample_rate: int,
        pcm: bytes,
        wav: bytes,
        received_at: datetime,
        delivery_key: str | None,
    ) -> dict:
        # UID identifies an upstream account, never a verified pendant or speaker.
        device = "omi-webhook-" + hashlib.sha256(uid.encode()).hexdigest()[:24]
        delivery_id = (
            hashlib.sha256(json.dumps([uid, delivery_key]).encode()).hexdigest()
            if delivery_key
            else str(uuid4())
        )
        fingerprint = hashlib.sha256(str(sample_rate).encode() + b":" + pcm).hexdigest()
        duration = len(pcm) / (sample_rate * 2)
        key = str(uuid4())
        path = self.audio_storage.location(key)
        temporary = None
        try:
            with self.connect() as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(
                    "SELECT * FROM omi_audio_receipts WHERE delivery_id=?", (delivery_id,)
                ).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise ValueError(
                            "Delivery key already accepted with different audio or rate"
                        )
                    # Tombstones keep retries from resurrecting deleted/expired sources.
                    return {
                        "id": previous["audio_id"],
                        "accepted_at": previous["accepted_at"],
                        "duplicate": True,
                        "state": "accepted",
                    }
                if self.audio_storage.is_remote:
                    self.audio_storage.write(key, wav)
                else:
                    descriptor, temporary = tempfile.mkstemp(
                        prefix=".omi-audio-", dir=self.audio_dir,
                    )
                    with os.fdopen(descriptor, "wb") as target:
                        target.write(wav)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(temporary, path)
                    temporary = None
                    directory = os.open(self.audio_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                metadata = {
                    "input_sample_rate": sample_rate,
                    "archive_sample_rate": 16000,
                    "input_bytes": len(pcm),
                    "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                    "delivery_key_provided": delivery_key is not None,
                }
                db.execute(
                    "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,received_at,"
                    "source_kind,source_id,source_metadata,storage_bytes) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        key,
                        device,
                        stamp(received_at - timedelta(seconds=duration)),
                        stamp(received_at),
                        hashlib.sha256(wav).hexdigest(),
                        str(path),
                        stamp(received_at),
                        "omi_audio_webhook",
                        uid,
                        json.dumps(metadata),
                        len(wav),
                    ),
                )
                db.execute(
                    "INSERT INTO omi_audio_receipts VALUES(?,?,?,?,?)",
                    (delivery_id, fingerprint, key, stamp(received_at), duration),
                )
            # The transaction has committed before acknowledgement, including the STT queue row.
            return {
                "id": key,
                "accepted_at": stamp(received_at),
                "duplicate": False,
                "state": "accepted",
            }
        except Exception:
            if not self.audio_storage.is_remote:
                self.audio_storage.delete(key)
            raise
        finally:
            if temporary is not None:
                os.unlink(temporary)

    def forwarding_status(self) -> dict:
        with self.connect() as db:
            receipts = dict(
                db.execute(
                    "SELECT count(*) AS accepted_requests,max(accepted_at) AS last_received_at "
                    "FROM omi_audio_receipts"
                ).fetchone()
            )
            latest = db.execute(
                "SELECT audio_id FROM omi_audio_receipts ORDER BY accepted_at DESC LIMIT 1"
            ).fetchone()
        return receipts | {"last_source_id": latest["audio_id"] if latest else None}
