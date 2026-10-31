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
CREATE TABLE IF NOT EXISTS notification_sources (
 notification_id TEXT NOT NULL, source_id TEXT NOT NULL,
 PRIMARY KEY(notification_id,source_id)
);
CREATE INDEX IF NOT EXISTS notification_source ON notification_sources(source_id,notification_id);
-- This shared relation may predate initialization of the durable generation worker.
CREATE TABLE IF NOT EXISTS generation_job_sources (
 job_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(job_id,source_id)
);
-- Recover provenance for notices queued before source-scoped invalidation existed.
INSERT OR IGNORE INTO notification_sources(notification_id,source_id)
WITH unbound AS MATERIALIZED (
 SELECT id,kind,target FROM notifications
 WHERE id NOT IN (SELECT notification_id FROM notification_sources)
)
SELECT n.id,s.source_id FROM unbound n JOIN generation_job_sources s
 ON s.job_id=json_extract(n.target,'$.job_id')
UNION
SELECT id,json_extract(target,'$.source_id') FROM unbound
 WHERE json_extract(target,'$.source_id') IS NOT NULL
UNION
SELECT n.id,json_extract(value,'$.id') FROM unbound n
 JOIN diary d ON d.day=coalesce(json_extract(n.target,'$.day'),json_extract(n.target,'$.date'))
 CROSS JOIN json_each(json_extract(d.content,'$.sources'))
 WHERE n.kind='recap' AND json_extract(value,'$.id') IS NOT NULL
UNION
SELECT n.id,json_extract(value,'$.id') FROM unbound n
 JOIN weekly_versions w ON w.id=json_extract(n.target,'$.version_id')
 CROSS JOIN json_each(json_extract(w.content,'$.sources'))
 WHERE n.kind='weekly' AND json_extract(value,'$.id') IS NOT NULL;
CREATE INDEX IF NOT EXISTS notification_due ON notification_deliveries(state,retry_at);
DROP TRIGGER IF EXISTS notification_removed;
CREATE TRIGGER IF NOT EXISTS notification_removed AFTER DELETE ON notifications BEGIN
 DELETE FROM notification_deliveries WHERE notification_id=OLD.id;
 DELETE FROM notification_sources WHERE notification_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS notification_device_removed
AFTER DELETE ON notification_devices BEGIN
 DELETE FROM notification_deliveries WHERE device_id=OLD.id;
END;
DROP TRIGGER IF EXISTS notification_source_removed;
CREATE TRIGGER IF NOT EXISTS notification_source_removed AFTER DELETE ON audio BEGIN
 DELETE FROM notifications WHERE id IN (
 SELECT notification_id FROM notification_sources WHERE source_id=OLD.id);
END;
DROP TRIGGER IF EXISTS notification_source_corrected;
CREATE TRIGGER IF NOT EXISTS notification_source_corrected
AFTER UPDATE OF text,state,transcript_version,source_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version
 OR OLD.source_version IS NOT NEW.source_version BEGIN
 DELETE FROM notifications WHERE id IN (
 SELECT notification_id FROM notification_sources WHERE source_id=OLD.id);
END;
"""


def public_notification(row) -> dict:
    return {key: (json.loads(row[key]) if key == "target" else row[key]) for key in (
        "id", "sequence", "kind", "title", "body", "target", "created_at"
    )}


class NotificationStore:
    @staticmethod
    def _notification_source_ids(db: sqlite3.Connection, kind: str, target: dict) -> set[str]:
        if target.get("job_id"):
            return {row["source_id"] for row in db.execute(
                "SELECT source_id FROM generation_job_sources WHERE job_id=?", (target["job_id"],),
            )}
        if target.get("source_id"):
            return {target["source_id"]}
        row = None
        if kind == "recap" and (day := target.get("day") or target.get("date")):
            row = db.execute("SELECT content FROM diary WHERE day=?", (day,)).fetchone()
        elif kind == "weekly" and target.get("version_id"):
            row = db.execute("SELECT content FROM weekly_versions WHERE id=?",
                             (target["version_id"],)).fetchone()
        return {source["id"] for source in json.loads(row["content"]).get("sources", [])
                if source.get("id")} if row else set()

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
                db.executemany("INSERT OR IGNORE INTO notification_sources VALUES(?,?)", [
                    (row["id"], source_id)
                    for source_id in self._notification_source_ids(db, kind, target)
                ])
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
