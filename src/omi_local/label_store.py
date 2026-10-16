"""Transactional, manual labels and revision-bound annotations."""

import hashlib
import json
import unicodedata
from uuid import uuid4

from . import label_decisions
from .domain import stamp, utc_now
from .label_models import LabelError, LabelMutation


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def entity_value(row) -> dict:
    return dict(row) | {"aliases": json.loads(row["aliases"])}


class LabelStore:
    def label_revision(self) -> str:
        with self.connect() as db:
            return db.execute("SELECT revision FROM label_state").fetchone()[0]

    def archive_revision(self) -> str:
        with self.connect() as db:
            return db.execute("SELECT archive_revision FROM label_state").fetchone()[0]

    @staticmethod
    def _entity(db, key: str):
        row = db.execute("SELECT * FROM entities WHERE id=?", (key,)).fetchone()
        if not row:
            raise LabelError("Label not found", 404)
        if row["merged_into"]:
            raise LabelError("Label was merged; reload the directory")
        return row

    @staticmethod
    def _source(db, ref: dict):
        row = db.execute("SELECT * FROM audio WHERE id=?", (ref["id"],)).fetchone()
        if not row or row["text"] is None or row["source_version"] != ref["revision"]:
            raise LabelError("Recorded evidence changed or expired; reload and review it")
        return row

    def label_mutate(self, command: LabelMutation) -> dict:
        value = command.model_dump(mode="json")
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT digest FROM label_receipts WHERE id=?", (value["mutation_id"],)
            ).fetchone()
            if old:
                if old[0] != digest:
                    raise LabelError("Mutation ID already used for another edit")
                return {
                    "applied": True,
                    "revision": db.execute("SELECT revision FROM label_state").fetchone()[0],
                }
            revision = db.execute("SELECT revision FROM label_state").fetchone()[0]
            if revision != value["expected_revision"]:
                raise LabelError("Archive changed; reload and review before saving your draft")
            action, key = value["action"], value["id"]
            if action == "entity.save":
                self._save_entity(db, key, value["value"], value["create"])
            elif action == "link.save":
                entity = self._entity(db, value["entity_id"])
                self._source(db, value["source"])
                project = entity["kind"] == "project"
                if project != (value["relation"] == "about_project"):
                    raise LabelError("Choose an association meaning valid for this label", 422)
                previous = db.execute("SELECT * FROM entity_links WHERE id=?", (key,)).fetchone()
                self._check_existence(previous, value["create"])
                if previous and (
                    previous["entity_id"] != value["entity_id"]
                    or previous["source_id"] != value["source"]["id"]
                ):
                    raise LabelError("An association cannot be moved through editing")
                duplicate = db.execute(
                    "SELECT id FROM entity_links WHERE entity_id=? AND source_id=? AND relation=?",
                    (value["entity_id"], value["source"]["id"], value["relation"]),
                ).fetchone()
                if duplicate and duplicate[0] != key:
                    raise LabelError("This association already exists")
                db.execute(
                    "INSERT OR REPLACE INTO entity_links VALUES(?,?,?,?,?)",
                    (
                        key,
                        value["entity_id"],
                        value["source"]["id"],
                        value["source"]["revision"],
                        value["relation"],
                    ),
                )
            elif action == "decision_link.save":
                self._entity(db, value["entity_id"])
                label_decisions.save_decision_link(db, value)
            elif action == "note.save":
                previous = db.execute("SELECT id FROM label_notes WHERE id=?", (key,)).fetchone()
                self._check_existence(previous, value["create"])
                for ref in value["sources"]:
                    self._source(db, ref)
                for entity_id in value["entity_ids"]:
                    self._entity(db, entity_id)
                db.execute("DELETE FROM label_notes WHERE id=?", (key,))
                db.execute(
                    "INSERT INTO label_notes VALUES(?,?,?,?)",
                    (
                        key,
                        value["kind"],
                        value["title"],
                        value["body"],
                    ),
                )
                db.executemany(
                    "INSERT INTO note_sources VALUES(?,?,?)",
                    [(key, ref["id"], ref["revision"]) for ref in value["sources"]],
                )
                db.executemany(
                    "INSERT INTO note_entities VALUES(?,?)",
                    [(key, entity_id) for entity_id in value["entity_ids"]],
                )
            elif action.endswith(".delete"):
                self._delete_label_record(db, action, key)
            elif action == "entity.merge":
                self._merge(db, key, value["target_id"])
            elif action == "entity.split":
                self._split(db, key, value)
            # Events contain the complete command to replay user edits. Source/label deletion
            # conservatively purges all such history; anonymous receipts contain only hashes.
            db.execute(
                "INSERT INTO label_events VALUES(?,?,?)",
                (
                    value["mutation_id"],
                    stamp(utc_now()),
                    serialized,
                ),
            )
            if action.endswith(".delete"):
                db.execute("DELETE FROM label_events")
            db.execute("INSERT INTO label_receipts VALUES(?,?)", (value["mutation_id"], digest))
            revision = uuid4().hex
            db.execute("UPDATE label_state SET revision=?", (revision,))
            return {"applied": True, "revision": revision}

    @staticmethod
    def _check_existence(old, create: bool) -> None:
        if create and old:
            raise LabelError("This ID already exists; reload before creating another record")
        if not create and not old:
            raise LabelError("Record was removed; reload before editing")

    def _save_entity(self, db, key: str, value: dict, create: bool = True) -> None:
        old = db.execute("SELECT * FROM entities WHERE id=?", (key,)).fetchone()
        self._check_existence(old, create)
        if old and (old["kind"] != value["kind"] or old["merged_into"]):
            raise LabelError("Label kind and merged identities cannot be changed")
        aliases = list({normalized(alias): alias for alias in value["aliases"]}.values())
        db.execute(
            "INSERT INTO entities VALUES(?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name,description=excluded.description,aliases=excluded.aliases",
            (key, value["kind"], value["name"], value["description"], json.dumps(aliases)),
        )

    def _delete_label_record(self, db, action: str, key: str) -> None:
        if action == "entity.delete":
            self._entity(db, key)
            # Notes can contain label names. Conservatively remove their content too.
            db.execute(
                "DELETE FROM label_notes WHERE id IN "
                "(SELECT note_id FROM note_entities WHERE entity_id=?)",
                (key,),
            )
            db.execute("DELETE FROM entity_links WHERE entity_id=?", (key,))
            db.execute("DELETE FROM entity_decisions WHERE entity_id=?", (key,))
            db.execute("DELETE FROM entities WHERE merged_into=? OR id=?", (key, key))
        else:
            table = {
                "link.delete": "entity_links",
                "note.delete": "label_notes",
                "decision_link.delete": "entity_decisions",
            }[action]
            if not db.execute(f"DELETE FROM {table} WHERE id=?", (key,)).rowcount:
                raise LabelError("Record not found", 404)

    def _merge(self, db, key: str, target_id: str) -> None:
        source, target = self._entity(db, key), self._entity(db, target_id)
        if key == target_id or source["kind"] != target["kind"]:
            raise LabelError("Choose another label of the same kind", 422)
        aliases = json.loads(target["aliases"]) + [source["name"]] + json.loads(source["aliases"])
        # Avoid hidden data loss from an overflowing alias list.
        aliases = list({normalized(a): a for a in aliases}.values())
        if len(aliases) > 30:
            raise LabelError("Merge exceeds 30 aliases; edit aliases first", 422)
        db.execute("UPDATE entities SET aliases=? WHERE id=?", (json.dumps(aliases), target_id))
        for link in db.execute("SELECT * FROM entity_links WHERE entity_id=?", (key,)).fetchall():
            duplicate = db.execute(
                "SELECT * FROM entity_links WHERE entity_id=? AND source_id=? AND relation=?",
                (target_id, link["source_id"], link["relation"]),
            ).fetchone()
            if duplicate:
                # Any stale confirmation stays stale after deduplication.
                if link["source_revision"] != duplicate["source_revision"]:
                    db.execute(
                        "UPDATE entity_links SET source_revision=? WHERE id=?",
                        ("0" * 32, duplicate["id"]),
                    )
                db.execute("DELETE FROM entity_links WHERE id=?", (link["id"],))
            else:
                db.execute(
                    "UPDATE entity_links SET entity_id=? WHERE id=?", (target_id, link["id"])
                )
        for link in db.execute(
            "SELECT * FROM entity_decisions WHERE entity_id=?", (key,)
        ).fetchall():
            duplicate = db.execute(
                "SELECT * FROM entity_decisions WHERE entity_id=? AND decision_id=?",
                (target_id, link["decision_id"]),
            ).fetchone()
            if duplicate:
                db.execute(
                    "UPDATE entity_decisions SET decision_version=? WHERE id=?",
                    (min(link["decision_version"], duplicate["decision_version"]), duplicate["id"]),
                )
                db.execute("DELETE FROM entity_decisions WHERE id=?", (link["id"],))
            else:
                db.execute(
                    "UPDATE entity_decisions SET entity_id=? WHERE id=?", (target_id, link["id"])
                )
        db.execute(
            "INSERT OR IGNORE INTO note_entities SELECT note_id,? FROM note_entities "
            "WHERE entity_id=?",
            (target_id, key),
        )
        db.execute("DELETE FROM note_entities WHERE entity_id=?", (key,))
        db.execute(
            "UPDATE entities SET merged_into=? WHERE id=? OR merged_into=?", (target_id, key, key)
        )

    def _split(self, db, key: str, value: dict) -> None:
        entity = self._entity(db, key)
        if entity["kind"] != value["value"]["kind"] or key == value["new_id"]:
            raise LabelError("A split must create a label of the same kind", 422)
        if db.execute("SELECT 1 FROM entities WHERE id=?", (value["new_id"],)).fetchone():
            raise LabelError("Split destination already exists")
        self._save_entity(db, value["new_id"], value["value"])
        for link_id in value["link_ids"]:
            if not db.execute(
                "UPDATE entity_links SET entity_id=? WHERE id=? AND entity_id=?",
                (value["new_id"], link_id, key),
            ).rowcount:
                raise LabelError("Selected association changed")
        for link_id in value["decision_link_ids"]:
            if not db.execute(
                "UPDATE entity_decisions SET entity_id=? WHERE id=? AND entity_id=?",
                (value["new_id"], link_id, key),
            ).rowcount:
                raise LabelError("Selected decision association changed")
        for note_id in value["note_ids"]:
            if not db.execute(
                "DELETE FROM note_entities WHERE note_id=? AND entity_id=?", (note_id, key)
            ).rowcount:
                raise LabelError("Selected note changed")
            db.execute("INSERT INTO note_entities VALUES(?,?)", (note_id, value["new_id"]))

    def label_directory(self, query: str = "", kind: str | None = None) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            revision = db.execute("SELECT revision FROM label_state").fetchone()[0]
            values = [
                entity_value(row)
                for row in db.execute(
                    "SELECT * FROM entities WHERE merged_into IS NULL ORDER BY name,id"
                )
            ]
            words = normalized(query)
            values = [
                v
                for v in values
                if (not kind or v["kind"] == kind)
                and (not words or any(words in normalized(s) for s in [v["name"], *v["aliases"]]))
            ]
            return {"revision": revision, "items": values}

    def label_source(self, key: str) -> dict:
        from .service import public_source

        with self.connect() as db:
            db.execute("BEGIN")
            source = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
            if not source:
                raise LabelError("Source not found", 404)
            return {
                "revision": db.execute("SELECT revision FROM label_state").fetchone()[0],
                "source": public_source(dict(source)),
                "links": [
                    dict(row) | {"needs_review": row["source_revision"] != source["source_version"]}
                    for row in db.execute("SELECT * FROM entity_links WHERE source_id=?", (key,))
                ],
            }

    def label_timeline(self, key: str, start=None, end=None) -> dict:
        from .service import public_source

        with self.connect() as db:
            db.execute("BEGIN")
            raw = db.execute("SELECT * FROM entities WHERE id=?", (key,)).fetchone()
            if raw and raw["merged_into"]:
                key = raw["merged_into"]
            entity = entity_value(self._entity(db, key))
            links = [
                dict(row)
                for row in db.execute("SELECT * FROM entity_links WHERE entity_id=?", (key,))
            ]
            notes = [
                dict(row)
                for row in db.execute(
                    "SELECT n.* FROM label_notes n JOIN note_entities e ON e.note_id=n.id "
                    "WHERE e.entity_id=?",
                    (key,),
                )
            ]
            for note in notes:
                note["source_refs"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT source_id AS id,source_revision AS revision FROM note_sources "
                        "WHERE note_id=?",
                        (note["id"],),
                    )
                ]
                note["entity_ids"] = [
                    r[0]
                    for r in db.execute(
                        "SELECT entity_id FROM note_entities WHERE note_id=?", (note["id"],)
                    )
                ]
            # One read transaction gives a consistent evidence/revision manifest.
            sources = {
                r["id"]: public_source(dict(r))
                for r in db.execute(
                    "SELECT a.* FROM audio a WHERE a.id IN "
                    "(SELECT source_id FROM entity_links WHERE entity_id=? UNION "
                    "SELECT s.source_id FROM note_sources s JOIN note_entities e "
                    "ON e.note_id=s.note_id WHERE e.entity_id=?)",
                    (key, key),
                )
            }
            entries = []
            for link in links:
                source = sources[link["source_id"]]
                entries.append(
                    link
                    | {
                        "entry_type": "source",
                        "sources": [source],
                        "started_at": source["started_at"],
                        "ended_at": source["ended_at"],
                        "needs_review": link["source_revision"] != source["source_version"],
                    }
                )
            for note in notes:
                evidence = [sources[ref["id"]] for ref in note["source_refs"]]
                entries.append(
                    note
                    | {
                        "entry_type": "note",
                        "sources": evidence,
                        "started_at": min(s["started_at"] for s in evidence),
                        "ended_at": max(s["ended_at"] for s in evidence),
                        "needs_review": any(
                            sources[r["id"]]["source_version"] != r["revision"]
                            for r in note["source_refs"]
                        ),
                    }
                )
            entries.extend(label_decisions.entries(db, key))
            entries = [
                v
                for v in entries
                if any(
                    (not start or s["ended_at"] > stamp(start))
                    and (not end or s["started_at"] < stamp(end))
                    for s in v["sources"]
                )
            ]
            entries.sort(key=lambda e: (e["started_at"], e["id"]), reverse=True)
            return {
                "entity": entity,
                "items": entries,
                "revision": db.execute("SELECT revision FROM label_state").fetchone()[0],
            }
