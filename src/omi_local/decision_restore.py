"""Validate the decision graph and evidence when restoring an untrusted archive."""

import json
import sqlite3
from uuid import UUID

from .decision_store import validate_content
from .decisions import DecisionContent
from .domain import EvidenceChanged


def validate_decisions(db: sqlite3.Connection) -> None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='decisions'").fetchone():
        return  # Older archives are migrated after validation.
    records = {row["id"]: row for row in db.execute("SELECT * FROM decisions")}
    for key, row in records.items():
        if str(UUID(key)) != key or str(UUID(row["thread_id"])) != row["thread_id"]:
            raise ValueError("Invalid decision ID in archive")
        if row["version"] < 1 or row["state"] not in ("tentative", "final"):
            raise ValueError("Invalid decision version/state in archive")
        if row["review_status"] not in ("proposed", "confirmed", "dismissed") or row[
            "evidence_condition"
        ] not in ("current", "stale", "conflicted", "removed"):
            raise ValueError("Invalid decision status in archive")
        if row["evidence_condition"] == "removed":
            if row["content"] is not None:
                raise ValueError("Removed decision retains content")
        else:
            content = DecisionContent.model_validate_json(row["content"])
            if row["evidence_condition"] in ("current", "conflicted"):
                try:
                    validate_content(db, content)
                except EvidenceChanged as exc:
                    raise ValueError("Decision archive contains stale current evidence") from exc
        events = list(
            db.execute("SELECT * FROM decision_events WHERE decision_id=? ORDER BY version", (key,))
        )
        if [e["version"] for e in events] != list(range(1, row["version"] + 1)):
            raise ValueError("Decision event history is incomplete")
        for event in events:
            if row["evidence_condition"] == "removed" and event["content"] is not None:
                raise ValueError("Removed decision history retains content")
            if event["content"] is not None:
                DecisionContent.model_validate_json(event["content"])
        for direction, reverse in [
            ("predecessor_id", "successor_id"),
            ("successor_id", "predecessor_id"),
        ]:
            target = records.get(row[direction])
            if row[direction] and (
                not target or target[reverse] != key or target["thread_id"] != row["thread_id"]
            ):
                raise ValueError("Decision revision graph is inconsistent")
        if (
            row["predecessor_id"]
            and records[row["predecessor_id"]]["started_at"] >= row["started_at"]
        ):
            raise ValueError("Decision revision graph has invalid temporal order")
    for table in (
        "decision_events",
        "decision_dependencies",
        "decision_commands",
        "decision_dedup_keys",
    ):
        if any(row[0] not in records for row in db.execute(f"SELECT decision_id FROM {table}")):
            raise ValueError("Decision archive has orphaned references")
    for run in db.execute("SELECT * FROM decision_extractions"):
        snapshot = json.loads(run["snapshot"])
        if run["status"] not in ("active", "complete", "stale", "removed") or not 0 <= run[
            "cursor"
        ] <= len(snapshot):
            raise ValueError("Invalid decision extraction coverage")
        for ref in snapshot:
            UUID(ref["source_id"])
            if set(ref) != {"source_id", "source_version"}:
                raise ValueError("Extraction snapshot contains unexpected content")

    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='decision_source_suppressions'"
    ).fetchone():
        for row in db.execute("SELECT source_id FROM decision_source_suppressions"):
            if str(UUID(row["source_id"])) != row["source_id"]:
                raise ValueError("Invalid suppressed decision source ID in archive")
