"""User-reviewed facts are immutable, source-bound annotation versions."""

from datetime import datetime
from uuid import UUID

from .domain import stamp, utc_now
from .label_models import LabelError

SCHEMA = """
CREATE TABLE IF NOT EXISTS label_facts (
 note_id TEXT PRIMARY KEY, family_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('confirmed','rejected','superseded')),
 superseded_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS label_facts_family ON label_facts(family_id);
CREATE TABLE IF NOT EXISTS label_fact_sources (
 family_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(family_id,source_id)
);
CREATE INDEX IF NOT EXISTS label_fact_sources_source ON label_fact_sources(source_id);
CREATE TRIGGER IF NOT EXISTS label_facts_note_removed AFTER DELETE ON label_notes BEGIN
 DELETE FROM label_facts WHERE note_id=OLD.id;
 DELETE FROM label_fact_sources WHERE family_id NOT IN (SELECT family_id FROM label_facts);
END;
CREATE TRIGGER IF NOT EXISTS label_facts_source_removed BEFORE DELETE ON audio BEGIN
 DELETE FROM label_notes WHERE id IN (SELECT note_id FROM label_facts WHERE family_id IN
 (SELECT family_id FROM label_fact_sources WHERE source_id=OLD.id));
 DELETE FROM label_fact_sources WHERE family_id NOT IN (SELECT family_id FROM label_facts);
END;
CREATE TRIGGER IF NOT EXISTS label_facts_source_expired AFTER UPDATE OF text ON audio
WHEN NEW.text IS NULL BEGIN
 DELETE FROM label_notes WHERE id IN (SELECT note_id FROM label_facts WHERE family_id IN
 (SELECT family_id FROM label_fact_sources WHERE source_id=NEW.id));
 DELETE FROM label_fact_sources WHERE family_id NOT IN (SELECT family_id FROM label_facts);
END;
"""


def save(store, db, value: dict) -> None:
    key, previous = value["id"], value.get("supersedes")
    if db.execute("SELECT 1 FROM label_notes WHERE id=?", (key,)).fetchone():
        raise LabelError("This ID already exists; reload before creating another record")
    for ref in value["sources"]:
        store._source(db, ref)
    store._entity(db, value["entity_ids"][0])
    family = key
    if previous:
        old = db.execute("SELECT * FROM label_facts WHERE note_id=?", (previous,)).fetchone()
        if not old or old["status"] == "superseded":
            raise LabelError("Fact was replaced or removed; reload before reviewing")
        owners = [row[0] for row in db.execute(
            "SELECT entity_id FROM note_entities WHERE note_id=?", (previous,),
        )]
        if owners != value["entity_ids"]:
            raise LabelError("A replacement fact must belong to the same person or project")
        family = old["family_id"]
    now = stamp(utc_now())
    db.execute("INSERT INTO label_notes VALUES(?,?,?,?)",
               (key, "fact", value["title"], value["body"]))
    db.executemany("INSERT INTO note_sources VALUES(?,?,?)",
                   [(key, ref["id"], ref["revision"]) for ref in value["sources"]])
    db.execute("INSERT INTO note_entities VALUES(?,?)", (key, value["entity_ids"][0]))
    db.execute("INSERT INTO label_facts VALUES(?,?,'confirmed',NULL,?,?)", (key, family, now, now))
    db.executemany("INSERT OR IGNORE INTO label_fact_sources VALUES(?,?)",
                   [(family, ref["id"]) for ref in value["sources"]])
    if previous:
        db.execute("UPDATE label_facts SET status='superseded',superseded_by=?,updated_at=? "
                   "WHERE note_id=?", (key, now, previous))


def reject(db, key: str) -> None:
    fact = db.execute("SELECT * FROM label_facts WHERE note_id=?", (key,)).fetchone()
    if not fact or fact["status"] != "confirmed":
        raise LabelError("Only a current fact can be rejected; reload before reviewing")
    db.execute("UPDATE label_facts SET status='rejected',updated_at=? WHERE note_id=?",
               (stamp(utc_now()), key))


def delete_family(db, key: str) -> bool:
    fact = db.execute("SELECT family_id FROM label_facts WHERE note_id=?", (key,)).fetchone()
    if not fact:
        return False
    db.execute("DELETE FROM label_notes WHERE id IN "
               "(SELECT note_id FROM label_facts WHERE family_id=?)", (fact[0],))
    return True


def view(db, key: str) -> dict:
    fact = db.execute("SELECT * FROM label_facts WHERE note_id=?", (key,)).fetchone()
    return {"fact": dict(fact)} if fact else {}


def split_family_ids(db, key: str) -> list[str]:
    return [row[0] for row in db.execute(
        "SELECT note_id FROM label_facts WHERE family_id IN "
        "(SELECT family_id FROM label_facts WHERE note_id=?)", (key,),
    )] or [key]


def validate(db, notes: dict) -> None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='label_facts'").fetchone():
        if any(note["kind"] == "fact" for note in notes.values()):
            raise ValueError("Fact metadata is missing")
        return
    facts = {row["note_id"]: dict(row) for row in db.execute("SELECT * FROM label_facts")}
    if set(facts) != {key for key, note in notes.items() if note["kind"] == "fact"}:
        raise ValueError("Invalid fact annotations")
    families = {}
    for key, fact in facts.items():
        UUID(fact["family_id"])
        if fact["status"] not in {"confirmed", "rejected", "superseded"}:
            raise ValueError("Invalid fact state")
        for name in ("created_at", "updated_at"):
            parsed = datetime.fromisoformat(fact[name])
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Invalid fact timestamp")
        members = families.setdefault(fact["family_id"], [])
        members.append(key)
        target = facts.get(fact["superseded_by"])
        if fact["status"] == "superseded":
            if not target or target["family_id"] != fact["family_id"] or key == target["note_id"]:
                raise ValueError("Invalid fact replacement")
        elif fact["superseded_by"] is not None:
            raise ValueError("Invalid fact replacement")
    for family, members in families.items():
        if (family not in members
                or sum(facts[key]["status"] != "superseded" for key in members) != 1):
            raise ValueError("Invalid fact history")
        owners, sources = set(), set()
        for key in members:
            owner = [r[0] for r in db.execute(
                "SELECT entity_id FROM note_entities WHERE note_id=?", (key,),
            )]
            if len(owner) != 1:
                raise ValueError("Invalid fact owner")
            owners.update(owner)
            sources.update(r[0] for r in db.execute(
                "SELECT source_id FROM note_sources WHERE note_id=?", (key,),
            ))
            visited, cursor = set(), key
            while facts[cursor]["superseded_by"]:
                if cursor in visited:
                    raise ValueError("Cyclic fact history")
                visited.add(cursor)
                cursor = facts[cursor]["superseded_by"]
        recorded = {r[0] for r in db.execute(
            "SELECT source_id FROM label_fact_sources WHERE family_id=?", (family,),
        )}
        if len(owners) != 1 or recorded != sources:
            raise ValueError("Invalid fact provenance")
    if any(row[0] not in families for row in db.execute(
        "SELECT family_id FROM label_fact_sources",
    )):
        raise ValueError("Dangling fact provenance")
