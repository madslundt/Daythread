"""Filtered generation identity and polling, without publishing full-day diary notices."""

import json
from datetime import date
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from .domain import EvidenceChanged, utc_now
from .scoped_recaps import ScopedRecaps


def scoped(parameters):
    return bool(parameters.get("tag_id") or parameters.get("zone_id"))


def selection(memory, parameters):
    return ScopedRecaps(memory, parameters.get("tag_id"), parameters.get("zone_id"))


def fingerprint(memory, parameters, db):
    return selection(memory, parameters).snapshot(
        date.fromisoformat(parameters["day"]), utc_now(), db,
    )["key"]


def validate_result(memory, parameters, result, db):
    expected = parameters.get("scope_fingerprint")
    if (result.get("scope_fingerprint") != expected
            or fingerprint(memory, parameters, db) != expected):
        raise EvidenceChanged("Recorded evidence changed; generate again")


def invalidate_changed(memory, row, db):
    """A saved durable result must not bypass membership or filter-definition invalidation."""
    if row["kind"] != "recap" or row["state"] != "completed" or not row["result"]:
        return row
    parameters = json.loads(row["parameters"])
    if not scoped(parameters):
        return row
    try:
        validate_result(memory, parameters, json.loads(row["result"]), db)
    except (EvidenceChanged, HTTPException):
        db.execute(
            "UPDATE generation_jobs SET result=NULL,state='failed',notified=1,error=? WHERE id=?",
            ("Recorded evidence changed; generate again", row["id"]),
        )
        return db.execute("SELECT * FROM generation_jobs WHERE id=?", (row["id"],)).fetchone()
    return row


def with_generation(jobs, result):
    """Auto-enqueue once per exact scope snapshot; explicit retry IDs take precedence."""
    key = result["scope_fingerprint"]
    with jobs.store.connect() as db:
        row = db.execute(
            "SELECT id FROM generation_jobs WHERE kind='recap' "
            "AND json_extract(parameters,'$.scope_fingerprint')=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1", (key,),
        ).fetchone()
    job = jobs.get(row["id"]) if row else None
    if job is None and result["stale"] and jobs.memory.settings.llm_enabled:
        from .generation_jobs import GenerationRequest
        job = jobs.enqueue(GenerationRequest(
            id=uuid5(NAMESPACE_URL, "daythread:filtered-recap:" + key), kind="recap",
            parameters={"day": result["day"], **{k: v for k, v in result["scope"].items() if v}},
        ))
    if job and job["parameters"].get("scope_fingerprint") != key:
        job = None  # Membership changed between reading the view and enqueueing. Poll again.
    if job and job["state"] == "completed" and result["stale"] and job["result"]:
        # Disposable entries can be evicted or cleared by unrelated audio changes. get()
        # revalidates the durable result against current originals and filter membership.
        result = job["result"]
    return result | {
        "generation_job_id": job["id"] if job else None,
        "generation_state": job["state"] if job else None,
        "generation_error": job["error"] if job else None,
    }
