"""Explicit links to the existing decision register; never copy its review state."""

from .decision_store import view
from .decisions import DecisionContent
from .label_models import LabelError


def save_decision_link(db, value: dict) -> None:
    decision = db.execute("SELECT * FROM decisions WHERE id=?", (value["decision_id"],)).fetchone()
    if (
        not decision
        or decision["content"] is None
        or decision["version"] != value["decision_version"]
    ):
        raise LabelError("Decision changed or was removed; refresh and review the register")
    existing = db.execute("SELECT * FROM entity_decisions WHERE id=?", (value["id"],)).fetchone()
    if existing and (
        existing["decision_id"] != value["decision_id"]
        or existing["entity_id"] != value["entity_id"]
    ):
        raise LabelError("A decision association cannot be moved through editing")
    duplicate = db.execute(
        "SELECT id FROM entity_decisions WHERE entity_id=? AND decision_id=?",
        (value["entity_id"], value["decision_id"]),
    ).fetchone()
    if duplicate and duplicate[0] != value["id"]:
        raise LabelError("This decision is already associated")
    db.execute(
        "INSERT OR REPLACE INTO entity_decisions VALUES(?,?,?,?)",
        (
            value["id"],
            value["entity_id"],
            value["decision_id"],
            value["decision_version"],
        ),
    )


def entries(db, entity_id: str) -> list[dict]:
    from .service import public_source

    result = []
    for link in db.execute("SELECT * FROM entity_decisions WHERE entity_id=?", (entity_id,)):
        decision = db.execute(
            "SELECT * FROM decisions WHERE id=?", (link["decision_id"],)
        ).fetchone()
        if not decision or not decision["content"]:
            continue
        content = DecisionContent.model_validate_json(decision["content"])
        source_ids = list(dict.fromkeys(str(a.source_id) for a in content.anchors()))
        sources = [
            public_source(dict(row))
            for key in source_ids
            if (row := db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone())
        ]
        result.append(
            dict(link)
            | {
                "entry_type": "decision",
                "title": content.choice.text,
                "sources": sources,
                "started_at": decision["started_at"],
                "ended_at": decision["ended_at"],
                "needs_review": (
                    decision["version"] != link["decision_version"]
                    or decision["evidence_condition"] != "current"
                ),
                "decision": view(decision),
            }
        )
    return result
