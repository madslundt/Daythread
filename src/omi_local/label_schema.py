"""Label projections and source lifecycle guards, executed in the source transaction."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS label_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision TEXT NOT NULL,
 archive_revision TEXT NOT NULL
);
INSERT OR IGNORE INTO label_state VALUES(1,lower(hex(randomblob(16))),lower(hex(randomblob(16))));
CREATE TABLE IF NOT EXISTS entities (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('person','project')),
 name TEXT NOT NULL, description TEXT NOT NULL, aliases TEXT NOT NULL,
 merged_into TEXT
);
CREATE TABLE IF NOT EXISTS entity_links (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, source_id TEXT NOT NULL,
 source_revision TEXT NOT NULL, relation TEXT NOT NULL,
 UNIQUE(entity_id,source_id,relation)
);
CREATE INDEX IF NOT EXISTS entity_links_source ON entity_links(source_id);
CREATE INDEX IF NOT EXISTS entity_links_entity ON entity_links(entity_id);
CREATE TABLE IF NOT EXISTS label_notes (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_sources (
 note_id TEXT NOT NULL, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
 PRIMARY KEY(note_id,source_id)
);
CREATE INDEX IF NOT EXISTS note_sources_source ON note_sources(source_id);
CREATE TABLE IF NOT EXISTS note_entities (
 note_id TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY(note_id,entity_id)
);
CREATE TABLE IF NOT EXISTS entity_decisions (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, decision_id TEXT NOT NULL,
 decision_version INTEGER NOT NULL, UNIQUE(entity_id,decision_id)
);
CREATE TRIGGER IF NOT EXISTS labels_decision_changed AFTER UPDATE ON decisions
WHEN EXISTS(SELECT 1 FROM entity_decisions WHERE decision_id=NEW.id) BEGIN
 UPDATE label_state SET revision=lower(hex(randomblob(16)));
 DELETE FROM entity_decisions WHERE decision_id=NEW.id AND NEW.content IS NULL;
END;
CREATE TRIGGER IF NOT EXISTS labels_decision_deleted BEFORE DELETE ON decisions BEGIN
 DELETE FROM entity_decisions WHERE decision_id=OLD.id;
 UPDATE label_state SET revision=lower(hex(randomblob(16)));
END;
CREATE TABLE IF NOT EXISTS label_events (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS label_receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_deletions (source_id TEXT PRIMARY KEY);
CREATE TRIGGER IF NOT EXISTS labels_note_deleted AFTER DELETE ON label_notes BEGIN
 DELETE FROM note_sources WHERE note_id=OLD.id;
 DELETE FROM note_entities WHERE note_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS labels_source_text AFTER UPDATE OF text,segments ON audio BEGIN
 UPDATE audio SET source_version=lower(hex(randomblob(16)))
 WHERE id=NEW.id AND source_version=OLD.source_version;
 UPDATE label_state SET revision=lower(hex(randomblob(16)))
 WHERE EXISTS(SELECT 1 FROM entity_links WHERE source_id=NEW.id)
 OR EXISTS(SELECT 1 FROM note_sources WHERE source_id=NEW.id);
 UPDATE label_state SET archive_revision=lower(hex(randomblob(16))) WHERE OLD.text IS NOT NULL;
END;
CREATE TRIGGER IF NOT EXISTS labels_source_audio AFTER UPDATE OF audio_available ON audio BEGIN
 UPDATE label_state SET revision=lower(hex(randomblob(16))),
 archive_revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS labels_source_expired AFTER UPDATE OF text ON audio
WHEN NEW.text IS NULL BEGIN
 DELETE FROM label_notes WHERE id IN (SELECT note_id FROM note_sources WHERE source_id=NEW.id);
 DELETE FROM entity_links WHERE source_id=NEW.id;
 DELETE FROM label_events;
 UPDATE label_state SET revision=lower(hex(randomblob(16)));
END;
CREATE TRIGGER IF NOT EXISTS labels_source_deleted BEFORE DELETE ON audio BEGIN
 INSERT OR IGNORE INTO import_deletions SELECT OLD.source_id WHERE OLD.source_kind='omi_api';
 DELETE FROM label_notes WHERE id IN (SELECT note_id FROM note_sources WHERE source_id=OLD.id);
 DELETE FROM entity_links WHERE source_id=OLD.id;
 DELETE FROM label_events;
 UPDATE label_state SET revision=lower(hex(randomblob(16))),
 archive_revision=lower(hex(randomblob(16)));
END;
"""


def validate_labels(db) -> None:
    """Reject dangling projections and invalid merge graphs before restoring a backup."""
    import json
    from uuid import UUID

    from pydantic import TypeAdapter

    from .label_models import Description, EntityDraft, LabelError, Name

    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='entities'").fetchone():
        return  # Supported backups predating labels migrate on open.
    entities = {row["id"]: dict(row) for row in db.execute("SELECT * FROM entities")}
    sources = {row["id"]: row for row in db.execute("SELECT * FROM audio")}
    notes = {row["id"]: row for row in db.execute("SELECT * FROM label_notes")}

    def valid_revision(value):
        return (
            isinstance(value, str)
            and len(value) == 32
            and all(char in "0123456789abcdef" for char in value)
        )

    try:
        state = db.execute("SELECT * FROM label_state").fetchall()
        if len(state) != 1 or not all(
            valid_revision(state[0][key]) for key in ("revision", "archive_revision")
        ):
            raise ValueError("Invalid archive metadata revision")
        for source in sources.values():
            if not valid_revision(source["source_version"]):
                raise ValueError("Invalid source incarnation/revision")
        for key, entity in entities.items():
            if str(UUID(key)) != key:
                raise ValueError("Invalid label ID")
            EntityDraft.model_validate(
                {k: entity[k] for k in ("kind", "name", "description")}
                | {"aliases": json.loads(entity["aliases"])}
            )
            target = entity["merged_into"]
            if target and (
                target not in entities
                or entities[target]["merged_into"]
                or entities[target]["kind"] != entity["kind"]
                or target == key
            ):
                raise ValueError("Invalid label merge")
        for row in db.execute("SELECT * FROM entity_links"):
            if row["entity_id"] not in entities or row["source_id"] not in sources:
                raise ValueError("Dangling label association")
            if (
                entities[row["entity_id"]]["merged_into"]
                or sources[row["source_id"]]["text"] is None
            ):
                raise ValueError("Invalid label evidence")
            allowed = (
                ("about_project",)
                if entities[row["entity_id"]]["kind"] == "project"
                else ("related", "mentioned", "participant")
            )
            if row["relation"] not in allowed or not valid_revision(row["source_revision"]):
                raise ValueError("Invalid association meaning or revision")
        for row in db.execute("SELECT * FROM note_sources"):
            if row["note_id"] not in notes or row["source_id"] not in sources:
                raise ValueError("Dangling note evidence")
            if sources[row["source_id"]]["text"] is None or not valid_revision(
                row["source_revision"]
            ):
                raise ValueError("Invalid note evidence")
        for row in db.execute("SELECT * FROM note_entities"):
            if row["note_id"] not in notes or row["entity_id"] not in entities:
                raise ValueError("Dangling note label")
            if entities[row["entity_id"]]["merged_into"]:
                raise ValueError("Note refers to merged label")
        for row in db.execute("SELECT * FROM entity_decisions"):
            UUID(row["id"])
            entity = entities.get(row["entity_id"])
            decision = db.execute(
                "SELECT * FROM decisions WHERE id=?", (row["decision_id"],)
            ).fetchone()
            if (
                not entity
                or entity["merged_into"]
                or not decision
                or not decision["content"]
                or row["decision_version"] < 1
                or row["decision_version"] > decision["version"]
            ):
                raise ValueError("Dangling decision association")
        for key, note in notes.items():
            UUID(key)
            TypeAdapter(Name).validate_python(note["title"])
            TypeAdapter(Description).validate_python(note["body"])
            if note["kind"] not in ("conversation", "decision", "open_question"):
                raise ValueError("Invalid note kind")
            if not db.execute("SELECT 1 FROM note_sources WHERE note_id=?", (key,)).fetchone():
                raise ValueError("Note without evidence")
            if not db.execute("SELECT 1 FROM note_entities WHERE note_id=?", (key,)).fetchone():
                raise ValueError("Note without a label")
    except (ValueError, TypeError, KeyError, LabelError) as exc:
        raise ValueError("Archive has invalid label data") from exc
