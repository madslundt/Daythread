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
CREATE TRIGGER IF NOT EXISTS speakers_delete AFTER DELETE ON audio BEGIN
 DELETE FROM speaker_events WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
 DELETE FROM speaker_scopes WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
 DELETE FROM speaker_dependencies WHERE scope_key NOT IN (SELECT scope_key FROM speaker_scopes);
END;
CREATE TRIGGER IF NOT EXISTS speakers_expire AFTER UPDATE OF state ON audio
WHEN NEW.state='expired' BEGIN
 DELETE FROM speaker_events WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
 DELETE FROM speaker_scopes WHERE scope_key IN
  (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
 DELETE FROM speaker_dependencies WHERE scope_key NOT IN (SELECT scope_key FROM speaker_scopes);
END;
"""
