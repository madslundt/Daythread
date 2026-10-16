"""Decision event history, optimistic review, and evidence dependency projection."""

import hashlib
import json
import sqlite3
from datetime import datetime
from uuid import uuid4

from .decisions import DecisionContent, DecisionCreate, DecisionEvent
from .domain import EvidenceChanged, stamp, utc_now

DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, version INTEGER NOT NULL,
 state TEXT NOT NULL, review_status TEXT NOT NULL, evidence_condition TEXT NOT NULL,
 content TEXT, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, predecessor_id TEXT, successor_id TEXT,
 dedup_key TEXT UNIQUE, run_id TEXT
);
CREATE TABLE IF NOT EXISTS decision_events (
 decision_id TEXT NOT NULL, version INTEGER NOT NULL, event TEXT NOT NULL,
 content TEXT, created_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(decision_id,version)
);
CREATE TABLE IF NOT EXISTS decision_dedup_keys (
 key TEXT PRIMARY KEY, decision_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_commands (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, decision_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_dependencies (
 decision_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version TEXT NOT NULL,
 PRIMARY KEY(decision_id,source_id,source_version)
);
CREATE INDEX IF NOT EXISTS decision_source ON decision_dependencies(source_id);
CREATE TABLE IF NOT EXISTS decision_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_suppressions (anchor_key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS decision_source_suppressions (source_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS decision_extractions (
 id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
 snapshot TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
 pending_chunks INTEGER NOT NULL, gaps INTEGER NOT NULL, created_at TEXT NOT NULL,
 text_offset INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS extraction_requests (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fingerprint TEXT NOT NULL, cursor INTEGER
);
"""


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def choice_anchor_key(content: DecisionContent) -> str:
    """An exact set of source-version/character anchors; generated wording is irrelevant."""
    return fingerprint(
        sorted(
            {(str(a.source_id), a.source_version, a.start, a.end) for a in content.choice.evidence}
        )
    )


def view(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["content"] = json.loads(row["content"]) if row["content"] else None
    result["display_state"] = "revised" if row["successor_id"] else row["state"]
    result.pop("dedup_key", None)
    result.pop("run_id", None)
    return result


def validate_content(db: sqlite3.Connection, content: DecisionContent) -> tuple[str, str]:
    """Validate Unicode code point anchors, never infer support from source membership."""
    starts, ends = [], []
    choice_ids = {str(a.source_id) for a in content.choice.evidence}
    for anchor in content.anchors():
        row = db.execute("SELECT * FROM audio WHERE id=?", (str(anchor.source_id),)).fetchone()
        if not row or row["source_version"] != anchor.source_version or row["state"] != "done":
            raise EvidenceChanged("Source was removed or corrected; refresh evidence")
        text = row["text"] or ""
        if text[anchor.start : anchor.end] != anchor.quote or anchor.end > len(text):
            raise ValueError("Evidence quotation does not match its exact source offsets")
        if row["id"] in choice_ids:
            starts.append(row["started_at"])
            ends.append(row["ended_at"])
    return min(starts), max(ends)


class DecisionStore:
    def save_decision_timezone(self, zone: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO decision_metadata VALUES('timezone',?)", (zone,))

    def _decision(self, db: sqlite3.Connection, key: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM decisions WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError("Decision not found")
        return row

    def _record_event(self, db: sqlite3.Connection, key: str, event: str) -> dict:
        row = self._decision(db, key)
        metadata = {
            k: row[k]
            for k in (
                "state",
                "review_status",
                "evidence_condition",
                "predecessor_id",
                "successor_id",
                "thread_id",
            )
        }
        db.execute(
            "INSERT INTO decision_events VALUES(?,?,?,?,?,?)",
            (key, row["version"], event, row["content"], row["updated_at"], json.dumps(metadata)),
        )
        # Files are disposable projections and must never retain a superseded decision export.
        for path in self.diary_dir.glob("*.md"):
            path.unlink(missing_ok=True)
        return self._detail(db, key)

    def _dependencies(self, db: sqlite3.Connection, key: str, sources: list[dict]) -> None:
        db.executemany(
            "INSERT OR IGNORE INTO decision_dependencies VALUES(?,?,?)",
            [(key, r["source_id"], r["source_version"]) for r in sources],
        )

    def _remember_choice(self, db: sqlite3.Connection, key: str, content: DecisionContent) -> None:
        db.execute(
            "INSERT OR IGNORE INTO decision_dedup_keys VALUES(?,?)",
            (choice_anchor_key(content), key),
        )

    def index_choice_anchors(self, db: sqlite3.Connection) -> None:
        """Upgrade wording-dependent keys using retained content, without rewriting history."""
        if db.execute("SELECT 1 FROM decision_metadata WHERE key='anchor_dedup_v2'").fetchone():
            return
        for row in db.execute(
            "SELECT id,content FROM decisions WHERE content IS NOT NULL "
            "UNION ALL SELECT decision_id,content FROM decision_events "
            "WHERE content IS NOT NULL"
        ).fetchall():
            self._remember_choice(
                db, row["id"], DecisionContent.model_validate_json(row["content"])
            )
        db.execute("INSERT INTO decision_metadata VALUES('anchor_dedup_v2','1')")

    def _create_decision(
        self,
        db: sqlite3.Connection,
        body: DecisionCreate,
        dependencies: list[dict] | None = None,
        run_id: str | None = None,
        dedup_key: str | None = None,
    ) -> dict:
        start, end = validate_content(db, body.content)
        key, now = str(uuid4()), stamp(utc_now())
        db.execute(
            "INSERT INTO decisions VALUES(?,?,1,?,'proposed',?,?,?,?,?,?,NULL,NULL,?,?)",
            (
                key,
                key,
                body.state,
                "conflicted" if body.content.contradictions else "current",
                body.content.model_dump_json(),
                start,
                end,
                now,
                now,
                dedup_key,
                run_id,
            ),
        )
        self._dependencies(db, key, [a.model_dump(mode="json") for a in body.content.anchors()])
        self._dependencies(db, key, dependencies or [])
        self._remember_choice(db, key, body.content)
        return self._record_event(db, key, "created")

    def _command(self, db: sqlite3.Connection, command_id: str, payload: dict) -> dict | None:
        existing = db.execute(
            "SELECT * FROM decision_commands WHERE id=?", (command_id,)
        ).fetchone()
        if existing:
            signatures = {fingerprint(payload)}
            content = payload.get("content")
            if isinstance(content, dict) and content.get("contradictions") == []:
                # Adding an optional field must not invalidate a pre-upgrade command retry.
                legacy_content = {k: v for k, v in content.items() if k != "contradictions"}
                signatures.add(fingerprint(payload | {"content": legacy_content}))
            if existing["fingerprint"] not in signatures:
                raise EvidenceChanged("Command ID already used with different content")
            return self._detail(db, existing["decision_id"])
        return None

    def create_decision(self, body: DecisionCreate) -> dict:
        payload = body.model_dump(mode="json")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if replay := self._command(db, str(body.command_id), payload):
                return replay
            result = self._create_decision(db, body)
            db.execute(
                "INSERT INTO decision_commands VALUES(?,?,?)",
                (str(body.command_id), fingerprint(payload), result["id"]),
            )
            return result

    def decisions(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        query: str = "",
        include_dismissed: bool = False,
        review_status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        clauses, args = ["evidence_condition!='removed'"], []
        if not include_dismissed:
            clauses.append("review_status!='dismissed'")
        if review_status:
            clauses.append("review_status=?")
            args.append(review_status)
        if start and end:
            clauses.extend(["started_at<?", "ended_at>?"])
            args.extend([stamp(end), stamp(start)])
        if query:
            clauses.append("content LIKE ? ESCAPE '\\'")
            args.append(
                "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
        where = " AND ".join(clauses)
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM decisions WHERE " + where, args).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM decisions WHERE "
                + where
                + " ORDER BY started_at DESC,id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            )
            return {"items": [view(row) for row in rows], "total": total}

    def _detail(self, db: sqlite3.Connection, key: str) -> dict:
        result = view(self._decision(db, key))
        result["history"] = [
            dict(row)
            | {
                "content": json.loads(row["content"]) if row["content"] else None,
                "metadata": json.loads(row["metadata"]),
            }
            for row in db.execute(
                "SELECT * FROM decision_events WHERE decision_id=? ORDER BY version", (key,)
            )
        ]
        return result

    def decision(self, key: str) -> dict:
        with self.connect() as db:
            return self._detail(db, key)

    def decision_event(self, key: str, body: DecisionEvent) -> dict:
        payload = body.model_dump(mode="json") | {"decision_id": key}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if replay := self._command(db, str(body.command_id), payload):
                return replay
            old = self._decision(db, key)
            if old["version"] != body.expected_version or old["evidence_condition"] == "removed":
                raise EvidenceChanged("Decision changed; reload before editing")
            now = stamp(utc_now())
            updates = {"version": old["version"] + 1, "updated_at": now}
            if body.type == "edit":
                if old["successor_id"] and body.state and body.state != old["state"]:
                    raise ValueError("A revised choice cannot change its historical state")
                start, end = validate_content(db, body.content)
                if (
                    old["predecessor_id"]
                    and start <= self._decision(db, old["predecessor_id"])["started_at"]
                ):
                    raise ValueError("Edited evidence must remain after the predecessor")
                if (
                    old["successor_id"]
                    and start >= self._decision(db, old["successor_id"])["started_at"]
                ):
                    raise ValueError("Edited evidence must remain before the successor")
                updates.update(
                    content=body.content.model_dump_json(),
                    started_at=start,
                    ended_at=end,
                    review_status="proposed",
                    evidence_condition="conflicted" if body.content.contradictions else "current",
                    state=body.state or old["state"],
                )
                self._dependencies(
                    db, key, [a.model_dump(mode="json") for a in body.content.anchors()]
                )
                self._remember_choice(db, key, body.content)
            elif body.type in ("confirm", "resolve"):
                if old["evidence_condition"] == "stale":
                    raise EvidenceChanged("Re-anchor corrected evidence before confirmation")
                if body.type == "confirm" and old["evidence_condition"] != "current":
                    raise EvidenceChanged("Resolve contradictory evidence before confirmation")
                validate_content(db, DecisionContent.model_validate_json(old["content"]))
                updates.update(evidence_condition="current")
                if body.type == "confirm":
                    updates["review_status"] = "confirmed"
                if body.predecessor_id:
                    predecessor = self._decision(db, str(body.predecessor_id))
                    if (
                        predecessor["id"] == key
                        or predecessor["version"] != body.predecessor_version
                        or predecessor["successor_id"]
                        or old["predecessor_id"]
                        or old["successor_id"]
                        or predecessor["review_status"] != "confirmed"
                        or predecessor["evidence_condition"] != "current"
                    ):
                        raise EvidenceChanged(
                            "Predecessor must be current, confirmed, and unrevised"
                        )
                    if old["started_at"] <= predecessor["started_at"]:
                        raise ValueError("A revision must follow its predecessor in capture time")
                    updates.update(
                        thread_id=predecessor["thread_id"], predecessor_id=predecessor["id"]
                    )
                    db.execute(
                        "UPDATE decisions SET successor_id=?,version=version+1,updated_at=? "
                        "WHERE id=?",
                        (key, now, predecessor["id"]),
                    )
                    self._record_event(db, predecessor["id"], "revised")
                    # Linkage is not semantic evidence: a successor must cite its own sources.
            elif body.type == "dismiss":
                if old["predecessor_id"] or old["successor_id"]:
                    raise ValueError(
                        "Linked records cannot be dismissed; delete or flag a conflict"
                    )
                updates["review_status"] = "dismissed"
            elif body.type == "conflict":
                updates["evidence_condition"] = "conflicted"
            db.execute(
                "UPDATE decisions SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?",
                (*updates.values(), key),
            )
            result = self._record_event(db, key, body.type)
            db.execute(
                "INSERT INTO decision_commands VALUES(?,?,?)",
                (str(body.command_id), fingerprint(payload), key),
            )
            return result

    def _purge_decision(self, db: sqlite3.Connection, key: str, event: str) -> None:
        # Privacy deletion is an explicit exception to retaining content in event history.
        self.index_choice_anchors(db)
        previous = self._decision(db, key)
        if previous["content"]:
            anchor_key = choice_anchor_key(DecisionContent.model_validate_json(previous["content"]))
            db.execute("INSERT OR IGNORE INTO decision_suppressions VALUES(?)", (anchor_key,))
        db.execute(
            "UPDATE decisions SET content=NULL,evidence_condition='removed',"
            "review_status='dismissed',version=version+1,updated_at=? WHERE id=?",
            (stamp(utc_now()), key),
        )
        db.execute("UPDATE decision_events SET content=NULL WHERE decision_id=?", (key,))
        self._record_event(db, key, event)
        # Fingerprints preserve idempotency without retaining any transcript payload.

    def delete_decision(self, key: str, expected_version: int) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._decision(db, key)
            if old["evidence_condition"] == "removed":
                return view(old)
            if old["version"] != expected_version:
                raise EvidenceChanged("Decision changed; reload before deleting")
            self._purge_decision(db, key, "deleted")
            return view(self._decision(db, key))

    def invalidate_decision_sources(
        self, db: sqlite3.Connection, keys: list[str], purge: bool
    ) -> None:
        if purge:
            db.executemany(
                "INSERT OR IGNORE INTO decision_source_suppressions VALUES(?)",
                [(key,) for key in keys],
            )
        affected = set()
        for key in keys:
            affected.update(
                r[0]
                for r in db.execute(
                    "SELECT decision_id FROM decision_dependencies WHERE source_id=?", (key,)
                )
            )
        # Runs contain IDs/versions only, but their results must never resume against old evidence.
        for row in db.execute("SELECT * FROM decision_extractions").fetchall():
            if any(s["source_id"] in keys for s in json.loads(row["snapshot"])):
                db.execute(
                    "UPDATE decision_extractions SET status=? WHERE id=?",
                    ("removed" if purge else "stale", row["id"]),
                )
        for key in affected:
            old = self._decision(db, key)
            if old["evidence_condition"] == "removed":
                continue
            if purge:
                self._purge_decision(db, key, "source_removed")
            else:
                db.execute(
                    "UPDATE decisions SET evidence_condition='stale',version=version+1,"
                    "updated_at=? WHERE id=?",
                    (stamp(utc_now()), key),
                )
                self._record_event(db, key, "source_corrected")
