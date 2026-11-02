"""Conversation-scoped speaker metadata, independent of canonical transcript text."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS speaker_scopes (
 scope_key TEXT PRIMARY KEY, version TEXT NOT NULL, fingerprint TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS speaker_dependencies (
 scope_key TEXT NOT NULL, audio_id TEXT NOT NULL, PRIMARY KEY(scope_key,audio_id)
);
CREATE INDEX IF NOT EXISTS speaker_dependency_audio ON speaker_dependencies(audio_id);
CREATE TABLE IF NOT EXISTS speaker_events (
 id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, action TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS speaker_jobs (
 scope_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 retry_at TEXT, error TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS speaker_jobs_due ON speaker_jobs(state,retry_at);
CREATE TRIGGER IF NOT EXISTS speakers_delete AFTER DELETE ON audio BEGIN
 DELETE FROM speaker_jobs WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
 DELETE FROM speaker_events WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
 DELETE FROM speaker_scopes WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
 DELETE FROM speaker_dependencies WHERE scope_key NOT IN (SELECT scope_key FROM speaker_scopes)
  AND scope_key NOT IN (SELECT scope_key FROM speaker_jobs);
END;
CREATE TRIGGER IF NOT EXISTS speakers_expire AFTER UPDATE OF state ON audio
WHEN NEW.state='expired' BEGIN
 DELETE FROM speaker_jobs WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
 DELETE FROM speaker_events WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
 DELETE FROM speaker_scopes WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
 DELETE FROM speaker_dependencies WHERE scope_key NOT IN (SELECT scope_key FROM speaker_scopes)
  AND scope_key NOT IN (SELECT scope_key FROM speaker_jobs);
END;
"""


def migrate_speaker_triggers(db) -> None:
    """Replace legacy trigger bodies when a live archive is opened."""
    db.executescript("""
        DROP TRIGGER IF EXISTS speakers_delete;
        DROP TRIGGER IF EXISTS speakers_expire;
        CREATE TRIGGER speakers_delete AFTER DELETE ON audio BEGIN
          DELETE FROM speaker_jobs WHERE scope_key IN
            (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
          DELETE FROM speaker_events WHERE scope_key IN
            (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
          DELETE FROM speaker_scopes WHERE scope_key IN
            (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
          DELETE FROM speaker_dependencies WHERE scope_key NOT IN
            (SELECT scope_key FROM speaker_scopes)
            AND scope_key NOT IN (SELECT scope_key FROM speaker_jobs);
        END;
        CREATE TRIGGER speakers_expire AFTER UPDATE OF state ON audio
        WHEN NEW.state='expired' BEGIN
          DELETE FROM speaker_jobs WHERE scope_key IN
            (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
          DELETE FROM speaker_events WHERE scope_key IN
            (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
          DELETE FROM speaker_scopes WHERE scope_key IN
            (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
          DELETE FROM speaker_dependencies WHERE scope_key NOT IN
            (SELECT scope_key FROM speaker_scopes)
            AND scope_key NOT IN (SELECT scope_key FROM speaker_jobs);
        END;
    """)
