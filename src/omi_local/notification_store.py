"""Durable completion feed and per-installation push outbox."""

import json
import sqlite3
import time
from collections.abc import Callable
from uuid import uuid4

from .domain import stamp, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 event_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_devices (
 id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, environment TEXT NOT NULL,
 active_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
 notification_id TEXT NOT NULL, device_id TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL,
 PRIMARY KEY(notification_id,device_id)
);
CREATE INDEX IF NOT EXISTS notification_due ON notification_deliveries(state,retry_at);
CREATE TRIGGER IF NOT EXISTS notification_removed AFTER DELETE ON notifications BEGIN
 DELETE FROM notification_deliveries WHERE notification_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS notification_device_removed
AFTER DELETE ON notification_devices BEGIN
 DELETE FROM notification_deliveries WHERE device_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS notification_source_removed AFTER DELETE ON audio BEGIN
 DELETE FROM notifications;
END;
CREATE TRIGGER IF NOT EXISTS notification_source_corrected
AFTER UPDATE OF text,state,transcript_version ON audio
WHEN OLD.text IS NOT NULL AND (OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version) BEGIN
 DELETE FROM notifications;
END;
"""


def public_notification(row) -> dict:
    return {key: (json.loads(row[key]) if key == "target" else row[key]) for key in (
        "id", "sequence", "kind", "title", "body", "target", "created_at"
    )}


class NotificationStore:
    def publish_notification(
        self, kind: str, title: str, body: str, target: dict, event_key: str,
        *, guard: Callable[[sqlite3.Connection], None] | None = None,
    ) -> dict:
        target_json = json.dumps(target, ensure_ascii=True, separators=(",", ":"))
        if len(target_json) > 1200:
            raise ValueError("Notification target exceeds 1200 bytes")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if guard is not None:
                guard(db)
            inserted = db.execute(
                "INSERT OR IGNORE INTO notifications "
                "(id,event_key,kind,title,body,target,created_at) VALUES (?,?,?,?,?,?,?)",
                (str(uuid4()), event_key, kind[:80], title[:120], body[:600], target_json,
                 stamp(utc_now())),
            )
            row = db.execute(
                "SELECT * FROM notifications WHERE event_key=?", (event_key,)
            ).fetchone()
            if inserted.rowcount:
                db.execute(
                    "INSERT INTO notification_deliveries(notification_id,device_id,expires_at) "
                    "SELECT ?,id,? FROM notification_devices", (row["id"], time.time() + 86400),
                )
            return public_notification(row)

    def notifications_after(self, after: int, limit: int = 100) -> dict:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM notifications WHERE sequence>? ORDER BY sequence LIMIT ?",
                (after, limit),
            ).fetchall()
        return {"notifications": [public_notification(row) for row in rows],
                "cursor": rows[-1]["sequence"] if rows else after}

    def register_notification_device(
        self, installation_id: str, token: str, environment: str, active: bool,
    ) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM notification_devices WHERE token=? AND id<>?",
                       (token, installation_id))
            db.execute(
                "INSERT INTO notification_devices VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE "
                "SET token=excluded.token,environment=excluded.environment,"
                "active_until=excluded.active_until",
                (installation_id, token, environment, time.time() + 60 if active else 0),
            )

    def unregister_notification_device(self, installation_id: str, token: str = "") -> None:
        with self.connect() as db:
            db.execute("DELETE FROM notification_devices WHERE id=? OR token=?",
                       (installation_id, token))

    def notification_presence(self, installation_id: str, active: bool) -> bool:
        with self.connect() as db:
            return bool(db.execute(
                "UPDATE notification_devices SET active_until=? WHERE id=?",
                (time.time() + 60 if active else 0, installation_id),
            ).rowcount)

    def retire_notification_token(self, installation_id: str, token: str) -> None:
        # A token may rotate while its previous APNs request is in flight.
        with self.connect() as db:
            db.execute("DELETE FROM notification_devices WHERE id=? AND token=?",
                       (installation_id, token))

    def acknowledge_notifications(self, installation_id: str, ids: list[str]) -> None:
        with self.connect() as db:
            db.executemany(
                "UPDATE notification_deliveries SET state='acknowledged' "
                "WHERE notification_id=? AND device_id=?",
                [(notification_id, installation_id) for notification_id in ids],
            )

    def pending_notifications(self) -> list[dict]:
        now = time.time()
        with self.connect() as db:
            db.execute("UPDATE notification_deliveries SET state='expired' "
                       "WHERE state='pending' AND expires_at<=?", (now,))
            return [dict(row) for row in db.execute(
                "SELECT n.*,d.id AS device_id,d.token,d.environment,q.attempts,q.expires_at "
                "FROM notification_deliveries q JOIN notifications n ON n.id=q.notification_id "
                "JOIN notification_devices d ON d.id=q.device_id "
                "WHERE q.state='pending' AND q.retry_at<=? AND d.active_until<=? "
                "ORDER BY n.sequence LIMIT 100", (now, now),
            )]

    def notification_still_pending(self, notification_id: str, device_id: str) -> bool:
        with self.connect() as db:
            return db.execute(
                "SELECT 1 FROM notification_deliveries q "
                "JOIN notification_devices d ON d.id=q.device_id "
                "WHERE q.notification_id=? AND q.device_id=? AND q.state='pending' "
                "AND d.active_until<=?", (notification_id, device_id, time.time()),
            ).fetchone() is not None

    def finish_notification_delivery(
        self, notification_id: str, device_id: str, state: str, attempts: int,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE notification_deliveries SET state=?,attempts=?,retry_at=? "
                "WHERE notification_id=? AND device_id=? AND state='pending'",
                (state, attempts, time.time() + min(3600, 5 * 2 ** min(attempts, 10)),
                 notification_id, device_id),
            )
