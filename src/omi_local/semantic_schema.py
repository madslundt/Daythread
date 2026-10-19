"""Portable, additive migrations for the rebuildable embedding index."""

STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_staging_passages (
 id TEXT PRIMARY KEY, audio_id TEXT NOT NULL, set_id TEXT NOT NULL, version TEXT NOT NULL,
 ordinal INTEGER NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
 vector BLOB, content_hash TEXT NOT NULL DEFAULT '', topic_id TEXT,
 metadata TEXT NOT NULL DEFAULT '{}', UNIQUE(audio_id,set_id,ordinal)
);
CREATE INDEX IF NOT EXISTS staging_passage_set ON embedding_staging_passages(set_id,audio_id);
CREATE INDEX IF NOT EXISTS passage_content ON retrieval_passages(set_id,audio_id,content_hash);
CREATE TRIGGER IF NOT EXISTS audio_embedding_staging_update
AFTER UPDATE OF text,state ON audio
WHEN OLD.text IS NOT NEW.text OR (OLD.state IS NOT NEW.state AND NEW.state='done') BEGIN
 DELETE FROM embedding_staging_passages WHERE audio_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS audio_embedding_staging_delete AFTER DELETE ON audio BEGIN
 DELETE FROM embedding_staging_passages WHERE audio_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS audio_embedding_topics_update
AFTER UPDATE OF topic_details ON audio
WHEN json_extract(OLD.topic_details,'$.sections')
 IS NOT json_extract(NEW.topic_details,'$.sections') BEGIN
 DELETE FROM embedding_staging_passages WHERE audio_id=NEW.id;
 INSERT OR REPLACE INTO embedding_jobs(audio_id,set_id,version)
 SELECT NEW.id,id,(SELECT transcript_version FROM audio WHERE id=NEW.id)
 FROM embedding_sets WHERE active=1 AND NEW.state='done' AND length(trim(NEW.text))>0;
END;
CREATE TRIGGER IF NOT EXISTS audio_embedding_provenance_update
AFTER UPDATE OF segments,source_version ON audio
WHEN OLD.segments IS NOT NEW.segments OR OLD.source_version IS NOT NEW.source_version BEGIN
 DELETE FROM embedding_staging_passages WHERE audio_id=NEW.id;
 INSERT OR REPLACE INTO embedding_jobs(audio_id,set_id,version)
 SELECT NEW.id,id,(SELECT transcript_version FROM audio WHERE id=NEW.id)
 FROM embedding_sets WHERE active=1 AND NEW.state='done' AND length(trim(NEW.text))>0;
END;
"""


def migrate_semantic_schema(db) -> None:
    """Old snapshots and both database engines use the same migration path."""
    additions = {
        "embedding_jobs": {
            "prepared": "INTEGER NOT NULL DEFAULT 0",
            "lease_token": "TEXT NOT NULL DEFAULT ''",
            "lease_until": "TEXT NOT NULL DEFAULT ''",
            "force_reembed": "INTEGER NOT NULL DEFAULT 0",
        },
        "retrieval_passages": {
            "content_hash": "TEXT NOT NULL DEFAULT ''",
            "topic_id": "TEXT",
            "metadata": "TEXT NOT NULL DEFAULT '{}'",
        },
    }
    upgraded = False
    for table, columns in additions.items():
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                upgraded = True
    db.executescript(STAGING_SCHEMA)
    if upgraded:
        # Preserve readable legacy vectors while refreshing their provenance. Exact
        # unchanged content is reused during preparation without a provider request.
        db.execute(
            "UPDATE embedding_jobs SET state='pending',prepared=0,lease_token='',lease_until=''"
        )
