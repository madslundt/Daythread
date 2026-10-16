"""Inbox persistence and archive lifecycle integration, including direct imports."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_sources (
 source_id TEXT PRIMARY KEY, revision TEXT NOT NULL, automatic INTEGER NOT NULL DEFAULT 0,
 scanned_revision TEXT NOT NULL DEFAULT '', changed_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS inbox_tombstones (source_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS inbox_items (
 id TEXT PRIMARY KEY, proposal TEXT NOT NULL, overrides TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'pending', reviewed_at TEXT, accepted_deadline TEXT,
 health TEXT NOT NULL DEFAULT 'current',
 version INTEGER NOT NULL DEFAULT 1, duplicate_of TEXT, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox_jobs (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, inputs TEXT NOT NULL,
 timezone TEXT NOT NULL, model TEXT NOT NULL DEFAULT 'unspecified',
 prompt_version TEXT NOT NULL DEFAULT 'inbox-1',
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 retry_at TEXT NOT NULL DEFAULT '', claim TEXT, claimed_at TEXT, error TEXT,
 created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS inbox_dependencies (
 job_id TEXT NOT NULL, source_id TEXT NOT NULL, revision TEXT NOT NULL,
 PRIMARY KEY(job_id,source_id)
);
CREATE INDEX IF NOT EXISTS inbox_dependency_source ON inbox_dependencies(source_id);
CREATE TABLE IF NOT EXISTS inbox_occurrences (
 job_id TEXT NOT NULL, item_id TEXT NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY(job_id,item_id)
);
CREATE INDEX IF NOT EXISTS inbox_occurrence_item ON inbox_occurrences(item_id);
CREATE TABLE IF NOT EXISTS inbox_events (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL, kind TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inbox_event_item ON inbox_events(item_id,created_at);
CREATE TABLE IF NOT EXISTS inbox_operations (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL
);
INSERT OR IGNORE INTO inbox_sources(source_id,revision)
 SELECT id,lower(hex(randomblob(16))) FROM audio WHERE text IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS inbox_new_source AFTER INSERT ON audio WHEN NEW.text IS NOT NULL
BEGIN
 INSERT OR REPLACE INTO inbox_sources(source_id,revision,automatic,changed_at)
 VALUES(NEW.id,lower(hex(randomblob(16))),1,strftime('%Y-%m-%dT%H:%M:%f000+00:00','now'));
END;
CREATE TRIGGER IF NOT EXISTS inbox_changed_source AFTER UPDATE OF text ON audio
WHEN NEW.text IS NOT OLD.text
BEGIN
 INSERT OR REPLACE INTO inbox_sources(source_id,revision,automatic,changed_at)
 VALUES(NEW.id,lower(hex(randomblob(16))),1,strftime('%Y-%m-%dT%H:%M:%f000+00:00','now'));
 INSERT INTO inbox_events(id,item_id,kind,payload,created_at)
 SELECT lower(hex(randomblob(16))),item_id,'source_changed',
 json_object('source_id',NEW.id),strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
 FROM inbox_occurrences WHERE job_id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=NEW.id) GROUP BY item_id;
 UPDATE inbox_items SET health='needs_review',version=version+1,
 updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE id IN
 (SELECT item_id FROM inbox_occurrences WHERE job_id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=NEW.id));
 UPDATE inbox_jobs SET state='superseded',claim=NULL WHERE id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=NEW.id);

END;
"""

# Removing a context source must also remove derivatives that consumed that context.
PURGE = """
 INSERT OR IGNORE INTO inbox_tombstones VALUES(OLD.id);
 DELETE FROM inbox_operations WHERE item_id IN
 (SELECT item_id FROM inbox_occurrences WHERE job_id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=OLD.id));
 DELETE FROM inbox_events WHERE item_id IN
 (SELECT item_id FROM inbox_occurrences WHERE job_id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=OLD.id));
 DELETE FROM inbox_items WHERE id IN
 (SELECT item_id FROM inbox_occurrences WHERE job_id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=OLD.id));
 DELETE FROM inbox_occurrences WHERE item_id NOT IN (SELECT id FROM inbox_items);
 DELETE FROM inbox_jobs WHERE id IN
 (SELECT job_id FROM inbox_dependencies WHERE source_id=OLD.id);
 DELETE FROM inbox_dependencies WHERE job_id NOT IN (SELECT id FROM inbox_jobs);
 DELETE FROM inbox_sources WHERE source_id=OLD.id;
"""
# Expiry runs before the normal correction trigger can unlink evidence.
SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS inbox_deleted_source BEFORE DELETE ON audio BEGIN {PURGE} END;
CREATE TRIGGER IF NOT EXISTS inbox_expired_source BEFORE UPDATE OF text ON audio
WHEN NEW.text IS NULL AND OLD.text IS NOT NULL BEGIN {PURGE} END;
"""
