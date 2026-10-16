"""Confirmed voice examples and temporary query vectors stay inside each archive."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_meta (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision TEXT NOT NULL
);
INSERT OR IGNORE INTO voice_meta VALUES(1,lower(hex(randomblob(16))));
CREATE TABLE IF NOT EXISTS voice_profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_examples (
 id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, scope_key TEXT NOT NULL,
 speaker_id TEXT NOT NULL, embedding TEXT NOT NULL, model TEXT NOT NULL,
 seconds REAL NOT NULL, source_ids TEXT NOT NULL, source_digests TEXT NOT NULL,
 fingerprint TEXT NOT NULL, turn_signature TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(scope_key,speaker_id)
);
CREATE INDEX IF NOT EXISTS voice_example_profile ON voice_examples(profile_id);
CREATE TABLE IF NOT EXISTS voice_example_sources (
 example_id TEXT NOT NULL, audio_id TEXT NOT NULL, PRIMARY KEY(example_id,audio_id)
);
CREATE INDEX IF NOT EXISTS voice_source_audio ON voice_example_sources(audio_id);
CREATE TABLE IF NOT EXISTS voice_vectors (
 scope_key TEXT NOT NULL, speaker_id TEXT NOT NULL, model TEXT NOT NULL,
 embedding TEXT NOT NULL, quality TEXT NOT NULL, PRIMARY KEY(scope_key,speaker_id)
);
CREATE TABLE IF NOT EXISTS voice_events (
 id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, example_id TEXT,
 action TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS voice_profile_insert AFTER INSERT ON voice_profiles BEGIN
 UPDATE voice_meta SET revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS voice_profile_delete AFTER DELETE ON voice_profiles BEGIN
 DELETE FROM voice_examples WHERE profile_id=OLD.id;
 DELETE FROM voice_events WHERE profile_id=OLD.id;
 UPDATE voice_meta SET revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS voice_example_insert AFTER INSERT ON voice_examples BEGIN
 UPDATE voice_meta SET revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS voice_example_delete AFTER DELETE ON voice_examples BEGIN
 DELETE FROM voice_example_sources WHERE example_id=OLD.id;
 DELETE FROM voice_events WHERE example_id=OLD.id;
 UPDATE voice_meta SET revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS voice_scope_delete AFTER DELETE ON speaker_scopes BEGIN
 DELETE FROM voice_examples WHERE scope_key=OLD.scope_key;
 DELETE FROM voice_vectors WHERE scope_key=OLD.scope_key;
END;
CREATE TRIGGER IF NOT EXISTS voice_source_delete AFTER DELETE ON audio BEGIN
 DELETE FROM voice_examples WHERE id IN
   (SELECT example_id FROM voice_example_sources WHERE audio_id=OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS voice_source_change
AFTER UPDATE OF text,segments,state,source_version,transcript_version,audio_available ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.segments IS NOT NEW.segments
 OR OLD.state IS NOT NEW.state OR OLD.source_version IS NOT NEW.source_version
 OR OLD.transcript_version IS NOT NEW.transcript_version
 OR (OLD.audio_available=1 AND NEW.audio_available=0) BEGIN
 DELETE FROM voice_examples WHERE id IN
   (SELECT example_id FROM voice_example_sources WHERE audio_id=NEW.id);
 DELETE FROM voice_vectors WHERE scope_key IN
   (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
END;
"""
