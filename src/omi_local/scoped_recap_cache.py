"""Bounded disposable filtered recap/note cache, separate from saved diary history."""

import json

from .domain import stamp, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_recap_cache (
 fingerprint TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS scoped_recap_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM scoped_recap_cache;
END;
CREATE TRIGGER IF NOT EXISTS scoped_recap_source_change
AFTER UPDATE OF text,state,source_version,transcript_version ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state
 OR OLD.source_version IS NOT NEW.source_version
 OR OLD.transcript_version IS NOT NEW.transcript_version BEGIN
 DELETE FROM scoped_recap_cache;
END;
"""


class ScopedRecapCache:
    def __init__(self, store):
        self.store = store

    def get(self, key, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self.get(key, connection)
        row = db.execute(
            "SELECT content FROM scoped_recap_cache WHERE fingerprint=?", (key,)
        ).fetchone()
        return json.loads(row["content"]) if row else None

    def save(self, key, value, sources, guard):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            guard(db)
            self.store.verify_sources(db, sources)
            db.execute("INSERT OR REPLACE INTO scoped_recap_cache VALUES(?,?,?)",
                       (key, json.dumps(value), stamp(utc_now())))
            db.execute(
                "DELETE FROM scoped_recap_cache WHERE fingerprint!=? AND fingerprint NOT IN "
                "(SELECT fingerprint FROM scoped_recap_cache WHERE fingerprint!=? "
                "ORDER BY created_at DESC,fingerprint LIMIT 63)", (key, key),
            )
