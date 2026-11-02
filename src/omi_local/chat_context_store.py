"""Disposable conversational notes; original chat messages are always retained separately."""

import hashlib
import json
from typing import TYPE_CHECKING

from .domain import EvidenceChanged

if TYPE_CHECKING:
    from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_context (
 session_id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_context_sources (
 session_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(session_id,source_id)
);
CREATE INDEX IF NOT EXISTS chat_context_source ON chat_context_sources(source_id,session_id);
CREATE TRIGGER IF NOT EXISTS chat_context_cache_removed AFTER DELETE ON chat_context BEGIN
 DELETE FROM chat_context_sources WHERE session_id=OLD.session_id;
END;
CREATE TRIGGER IF NOT EXISTS chat_context_session_removed AFTER DELETE ON chat_sessions BEGIN
 DELETE FROM chat_context WHERE session_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS chat_context_message_removed AFTER DELETE ON chat_messages BEGIN
 DELETE FROM chat_context WHERE session_id=OLD.session_id;
END;
DROP TRIGGER IF EXISTS chat_context_source_removed;
CREATE TRIGGER IF NOT EXISTS chat_context_source_removed AFTER DELETE ON audio BEGIN
 DELETE FROM chat_context WHERE session_id IN (
 SELECT session_id FROM chat_context_sources WHERE source_id=OLD.id);
END;
DROP TRIGGER IF EXISTS chat_context_source_changed;
CREATE TRIGGER IF NOT EXISTS chat_context_source_changed
AFTER UPDATE OF text,state,transcript_version,source_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.transcript_version IS NOT NEW.transcript_version
 OR OLD.source_version IS NOT NEW.source_version BEGIN
 DELETE FROM chat_context WHERE session_id IN (
 SELECT session_id FROM chat_context_sources WHERE source_id=OLD.id);
END;
"""


def digest(rows: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(
            [{key: row.get(key) for key in ("id", "role", "content", "sources")} for row in rows],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def load_context(
    store: Store, session_id: str, exclude_id: str | None = None,
) -> tuple[list[dict], dict]:
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at,id", (session_id,)
        ).fetchall()
        cached = db.execute(
            "SELECT payload FROM chat_context WHERE session_id=?", (session_id,)
        ).fetchone()
    history = [
        dict(row) | {"sources": json.loads(row["sources"])}
        for row in rows
        if row["id"] != exclude_id
    ]
    history = [
        row
        for row in history
        if row["role"] != "assistant" or not store.sources_stale(row["sources"])
    ]
    state = json.loads(cached["payload"]) if cached else {}
    covered = state.get("covered_ids", [])
    if [row["id"] for row in history[: len(covered)]] != covered or digest(
        history[: len(covered)]
    ) != state.get("history_digest"):
        state = {}
    return history, state


def save_context(store: Store, session_id: str, history: list[dict], state: dict) -> None:
    if "metadata" not in state:
        return
    covered = state.get("covered_ids", [])
    prefix = history[: len(covered)]
    state = state | {"history_digest": digest(prefix)}
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone():
            raise EvidenceChanged("Chat was removed while the answer was generated")
        current = {
            row["id"]: dict(row) | {"sources": json.loads(row["sources"])}
            for row in db.execute("SELECT * FROM chat_messages WHERE session_id=?", (session_id,))
        }
        if digest([current.get(key, {}) for key in covered]) != digest(prefix):
            raise EvidenceChanged("Conversation changed while its context was compacted")
        for row in prefix:
            store.verify_sources(db, row["sources"])
        db.execute(
            "INSERT OR REPLACE INTO chat_context VALUES(?,?)",
            (session_id, json.dumps(state, ensure_ascii=False)),
        )
        db.execute("DELETE FROM chat_context_sources WHERE session_id=?", (session_id,))
        db.executemany(
            "INSERT OR IGNORE INTO chat_context_sources VALUES(?,?)",
            [
                (session_id, source_id)
                for source_id in {source["id"] for row in prefix for source in row["sources"]}
            ],
        )


def context_metadata(store: Store, session_id: str) -> dict:
    with store.connect() as db:
        row = db.execute(
            "SELECT payload FROM chat_context WHERE session_id=?", (session_id,)
        ).fetchone()
    return (
        json.loads(row["payload"]).get("metadata", {})
        if row
        else {
            "compaction_count": 0,
            "compacted_at": None,
            "context_tokens": 0,
            "context_limit": 0,
            "limit_source": "fallback",
        }
    )
