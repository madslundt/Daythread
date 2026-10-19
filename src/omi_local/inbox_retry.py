"""Retire failed attempts only when current committed work covers their complete evidence."""

import json


def covered(part: dict, intervals: list[tuple[int, int]]) -> bool:
    cursor = part["start"]
    for low, high in sorted(intervals):
        if low > cursor:
            break
        cursor = max(cursor, high)
        if cursor >= part["end"]:
            return True
    return False


def retire_replaced_failures(db, key: str, prompt_version: str) -> None:
    """Run in the successful publisher/retry transaction; retain old diagnostics and records."""
    success = db.execute("SELECT * FROM inbox_jobs WHERE id=?", (key,)).fetchone()
    if not success or success["state"] != "done" or success["prompt_version"] != prompt_version:
        return
    source_ids = {part["id"] for part in json.loads(success["inputs"])}
    if not source_ids:
        return
    marks = ",".join("?" for _ in source_ids)
    failures = db.execute(
        "SELECT DISTINCT j.id,j.inputs FROM inbox_jobs j "
        "JOIN inbox_dependencies d ON d.job_id=j.id "
        "WHERE j.state='failed' AND j.timezone=? AND d.source_id IN (" + marks + ")",
        (success["timezone"], *source_ids),
    ).fetchall()
    if not failures:
        return
    inputs = {row["id"]: json.loads(row["inputs"]) for row in failures}
    source_ids = {part["id"] for parts in inputs.values() for part in parts}
    if not source_ids:
        return
    marks = ",".join("?" for _ in source_ids)
    revisions = dict(
        db.execute(
            "SELECT source_id,revision FROM inbox_sources WHERE source_id IN (" + marks + ")",
            tuple(source_ids),
        ).fetchall()
    )
    completed = db.execute(
        "SELECT DISTINCT j.inputs FROM inbox_jobs j "
        "JOIN inbox_dependencies d ON d.job_id=j.id "
        "WHERE j.state='done' AND j.timezone=? AND j.model=? AND j.prompt_version=? "
        "AND d.source_id IN (" + marks + ")",
        (success["timezone"], success["model"], prompt_version, *source_ids),
    ).fetchall()
    coverage: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for row in completed:
        for part in json.loads(row["inputs"]):
            coverage.setdefault((part["id"], part["revision"]), []).append(
                (part["start"], part["end"])
            )
    for failed_id, parts in inputs.items():
        if parts and all(
            revisions.get(part["id"]) == part["revision"]
            and covered(part, coverage.get((part["id"], part["revision"]), []))
            for part in parts
        ):
            db.execute(
                "UPDATE inbox_jobs SET state='superseded',claim=NULL WHERE id=? AND state='failed'",
                (failed_id,),
            )
