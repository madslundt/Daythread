"""Transactional inbox projections; user state is independent of model proposals."""

import json
import re
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from .decision_export import day_bounds
from .domain import stamp, utc_now
from .inbox_domain import Mutation


class InboxConflict(ValueError):
    pass


def decode(row) -> dict:
    value = dict(row)
    proposal, overrides = json.loads(value.pop("proposal")), json.loads(value.pop("overrides"))
    accepted = value.pop("accepted_deadline")
    frozen = {"deadline": json.loads(accepted)} if accepted is not None else {}
    return (
        value
        | proposal
        | frozen
        | overrides
        | {
            "proposal": proposal,
            "overrides": overrides,
            "accepted_deadline": json.loads(accepted) if accepted else None,
            "user_note": overrides.get("user_note"),
            "user_note_provenance": "user" if "user_note" in overrides else None,
        }
    )


def normalized(title: str) -> str:
    return " ".join(re.findall(r"\w+", title.casefold()))


def deadline_identity(deadline: dict) -> tuple | None:
    if deadline.get("precision") == "date":
        return "date", deadline["day"]
    if deadline.get("precision") == "datetime":
        return "datetime", datetime.fromisoformat(deadline["instant"]).timestamp()
    if deadline.get("phrase"):
        return "phrase", normalized(deadline["phrase"])
    return None


class InboxStore:
    def __init__(self, archive):
        self.archive = archive

    def item(self, key: str) -> dict | None:
        with self.archive.connect() as db:
            row = db.execute("SELECT * FROM inbox_items WHERE id=?", (key,)).fetchone()
            return self.detail(db, row) if row else None

    @staticmethod
    def detail(db, row) -> dict:
        value = decode(row)
        evidence = []
        for occurrence in db.execute(
            "SELECT o.evidence,j.state FROM inbox_occurrences o JOIN inbox_jobs j ON j.id=o.job_id "
            "WHERE o.item_id=?",
            (row["id"],),
        ):
            for span in json.loads(occurrence["evidence"]):
                audio = db.execute(
                    "SELECT audio_available FROM audio WHERE id=?", (span["source_id"],)
                ).fetchone()
                if audio:
                    evidence.append(
                        span
                        | {
                            "audio_available": bool(audio["audio_available"]),
                            "current": occurrence["state"] == "done",
                        }
                    )
        value["evidence"] = list(
            {(s["source_id"], s["revision"], s["start"], s["end"]): s for s in evidence}.values()
        )
        value["history"] = [
            dict(e) | {"payload": json.loads(e["payload"])}
            for e in db.execute(
                "SELECT * FROM inbox_events WHERE item_id=? ORDER BY created_at,id", (row["id"],)
            )
        ]
        return value

    def listing(
        self,
        status: str,
        kind: str | None,
        due: str | None,
        limit: int,
        offset: int,
        *,
        day: date | None = None,
        q: str = "",
        timezone: str = "UTC",
    ) -> dict:
        with self.archive.connect() as db:
            rows = db.execute(
                "SELECT * FROM inbox_items WHERE status=? ORDER BY created_at DESC,id", (status,)
            ).fetchall()
            items = [decode(r) for r in rows]
            evidence: dict[str, list[dict]] = {}
            for occurrence in db.execute(
                "SELECT o.item_id,o.evidence,j.state FROM inbox_occurrences o "
                "JOIN inbox_jobs j ON j.id=o.job_id "
                "JOIN inbox_items i ON i.id=o.item_id WHERE i.status=?",
                (status,),
            ):
                evidence.setdefault(occurrence["item_id"], []).extend(
                    span | {"current": occurrence["state"] == "done"}
                    for span in json.loads(occurrence["evidence"])
                )
            zone, terms = ZoneInfo(timezone), q.casefold().split()
            for item in items:
                item["source_ids"] = sorted(
                    {span["source_id"] for span in evidence.get(item["id"], [])}
                )
                item["evidence_spans"] = list(
                    {
                        (span["source_id"], span["start"], span["end"], span["current"]): {
                            field: span[field] for field in ("source_id", "start", "end", "current")
                        }
                        for span in evidence.get(item["id"], [])
                    }.values()
                )
            now = utc_now()

            def matches(item):
                if kind and item["kind"] != kind:
                    return False
                spans = evidence.get(item["id"], [])
                if day and not any(
                    datetime.fromisoformat(span["started_at"]).astimezone(zone).date() == day
                    for span in spans
                ):
                    return False
                searchable = " ".join(
                    [
                        item["title"],
                        item["owner"] or "",
                        item["user_note"] or "",
                        (item["deadline"] or {}).get("phrase") or "",
                    ]
                    + [span["quote"] for span in spans]
                ).casefold()
                if not all(term in searchable for term in terms):
                    return False
                deadline = item["deadline"] or {}
                precision = deadline.get("precision", "unknown")
                if precision == "date":
                    overdue = (
                        deadline["day"]
                        < now.astimezone(ZoneInfo(deadline.get("timezone") or "UTC"))
                        .date()
                        .isoformat()
                    )
                elif precision == "datetime":
                    overdue = datetime.fromisoformat(deadline["instant"]) < now
                else:
                    overdue = False
                item["overdue"] = overdue
                return (
                    not due
                    or (due == "overdue" and overdue)
                    or (due == "unknown" and precision == "unknown")
                    or (due == "upcoming" and precision != "unknown" and not overdue)
                )

            items = [i for i in items if matches(i)]
            items.sort(
                key=lambda i: (
                    not i["overdue"],
                    (i["deadline"] or {}).get("day") or "9999",
                    i["created_at"],
                )
            )
            bounds = day_bounds(day.isoformat(), timezone) if day else ()
            source_scope = " AND a.started_at>=? AND a.started_at<?" if day else ""
            job_scope = (
                " WHERE EXISTS (SELECT 1 FROM inbox_dependencies d "
                "JOIN audio a ON a.id=d.source_id "
                "WHERE d.job_id=j.id" + source_scope + ")"
                if day
                else ""
            )
            jobs = dict(
                db.execute(
                    "SELECT j.state,count(*) FROM inbox_jobs j" + job_scope + " GROUP BY j.state",
                    bounds,
                ).fetchall()
            )
            pending = db.execute(
                "SELECT count(*) FROM inbox_items WHERE status='pending'"
            ).fetchone()[0]
            recent = [
                dict(r)
                for r in db.execute(
                    "SELECT j.id,j.state,j.attempts,j.error,(SELECT min(a.started_at) "
                    "FROM inbox_dependencies d JOIN audio a ON a.id=d.source_id "
                    "WHERE d.job_id=j.id" + source_scope + ") AS recording_start FROM inbox_jobs j"
                    + job_scope + " ORDER BY j.created_at DESC LIMIT 20",
                    bounds + bounds,
                )
            ]
            unscanned = db.execute(
                "SELECT count(*) FROM inbox_sources s JOIN audio a ON a.id=s.source_id "
                "WHERE length(trim(a.text))>0 AND s.scanned_revision!=s.revision "
                "AND a.id NOT IN (SELECT source_id FROM inbox_tombstones)" + source_scope,
                bounds,
            ).fetchone()[0]
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "pending": pending,
            "coverage": {"jobs": jobs, "unscanned_sources": unscanned},
            "recent_jobs": recent,
        }

    def mutate(self, key: str, body: Mutation) -> dict:
        payload = body.model_dump_json(exclude_unset=True)
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old_op = db.execute(
                "SELECT * FROM inbox_operations WHERE id=?", (str(body.operation_id),)
            ).fetchone()
            if old_op:
                if old_op["request"] != payload or old_op["item_id"] != key:
                    raise InboxConflict("Operation ID was already used with different content")
                current = db.execute("SELECT * FROM inbox_items WHERE id=?", (key,)).fetchone()
                if current:
                    # The action is not repeated; return current evidence availability/health.
                    return self.detail(db, current)
                raise KeyError(key)
            row = db.execute("SELECT * FROM inbox_items WHERE id=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            if row["version"] != body.expected_version:
                raise InboxConflict(
                    "Item changed. Reload the saved version before applying your draft"
                )
            if body.duplicate_of:
                other = db.execute(
                    "SELECT duplicate_of FROM inbox_items WHERE id=?", (str(body.duplicate_of),)
                ).fetchone()
                if not other or str(body.duplicate_of) == key or other["duplicate_of"]:
                    raise ValueError("Choose a different, non-duplicate item")
            overrides = json.loads(row["overrides"])
            overrides.update(body.changes.model_dump(mode="json", exclude_unset=True))
            if "deadline" in body.changes.model_fields_set and overrides.get("deadline"):
                overrides["deadline"]["basis"] = "user edit"
            for field in body.reset:
                overrides.pop(field, None)
            now = stamp(utc_now())
            accepted = row["accepted_deadline"]
            if accepted is None or "deadline" in body.reset:
                accepted = json.dumps(json.loads(row["proposal"])["deadline"])
            health = row["health"]
            if health == "needs_review" and (
                "deadline" in body.reset or "deadline" in body.changes.model_fields_set
            ):
                if db.execute(
                    "SELECT 1 FROM inbox_occurrences o JOIN inbox_jobs j ON j.id=o.job_id "
                    "WHERE o.item_id=? AND j.state='done' LIMIT 1",
                    (key,),
                ).fetchone():
                    health = "current"
            duplicate = row["duplicate_of"]
            if body.status is not None and body.status != "dismissed":
                duplicate = None
            elif "duplicate_of" in body.model_fields_set:
                duplicate = str(body.duplicate_of) if body.duplicate_of else None
            db.execute(
                "UPDATE inbox_items SET overrides=?,status=?,reviewed_at=?,accepted_deadline=?,"
                "version=version+1,"
                "duplicate_of=?,health=?,updated_at=? WHERE id=?",
                (
                    json.dumps(overrides),
                    body.status or row["status"],
                    now,
                    accepted,
                    duplicate,
                    health,
                    now,
                    key,
                ),
            )
            db.execute(
                "INSERT INTO inbox_events VALUES(?,?,?,?,?)",
                (str(body.operation_id), key, "user_edit", payload, now),
            )
            result = self.detail(
                db, db.execute("SELECT * FROM inbox_items WHERE id=?", (key,)).fetchone()
            )
            db.execute(
                "INSERT INTO inbox_operations VALUES(?,?,?,?)",
                (str(body.operation_id), key, payload, json.dumps(result)),
            )
            return result

    def reconcile(self, db, job: dict, candidates: list[dict]) -> None:
        now = stamp(utc_now())
        assigned: set[str] = set()
        proposed_titles = {normalized(p["title"]) for p in candidates}
        for proposal in candidates:
            spans = proposal.pop("evidence")
            match = None
            source_ids = list({span["source_id"] for span in spans})
            placeholders = ",".join("?" for _ in source_ids)
            possible = db.execute(
                "SELECT DISTINCT i.* FROM inbox_items i JOIN inbox_occurrences o ON o.item_id=i.id "
                "JOIN inbox_dependencies d ON d.job_id=o.job_id WHERE d.source_id IN ("
                + placeholders
                + ") ORDER BY i.created_at,i.id",
                source_ids,
            ).fetchall()
            matches = []
            for row in possible:
                if row["id"] in assigned:
                    continue
                machine = json.loads(row["proposal"])
                machine_title = normalized(machine["title"])
                if (
                    machine_title != normalized(proposal["title"])
                    and machine_title in proposed_titles
                ):
                    continue
                # v3 splits reminders/events out of the older commitment category.
                # Keep the same identity and user state when the grounded action still matches.
                if machine["kind"] != proposal["kind"] and not {
                    machine["kind"], proposal["kind"]
                } <= {"commitment", "reminder", "event"}:
                    continue
                occurrences = db.execute(
                    "SELECT evidence FROM inbox_occurrences WHERE item_id=?", (row["id"],)
                ).fetchall()
                anchors = [s for o in occurrences for s in json.loads(o["evidence"])]
                # Shared utterance anchors are stronger than model wording. Across revisions,
                # require equal machine titles to avoid repurposing a user's edited item.
                same = any(
                    a["source_id"] == b["source_id"]
                    and (
                        (
                            a["revision"] == b["revision"]
                            and max(a["start"], b["start"]) < min(a["end"], b["end"])
                        )
                        or (
                            a["revision"] != b["revision"]
                            and abs(a["start"] - b["start"]) < 200
                            and normalized(machine["title"]) == normalized(proposal["title"])
                        )
                    )
                    for a in anchors
                    for b in spans
                )
                words = set(normalized(machine["title"]).split())
                proposed = set(normalized(proposal["title"]).split())
                similar = len(words & proposed) / max(1, len(words | proposed)) >= 0.4
                compatible = all(
                    not machine.get(k) or not proposal.get(k) or machine[k] == proposal[k]
                    for k in ("owner",)
                )
                old_due, new_due = machine.get("deadline") or {}, proposal.get("deadline") or {}
                old_identity, new_identity = deadline_identity(old_due), deadline_identity(new_due)
                calendar_change = (
                    (old_due.get("phrase") or "").lower()
                    in {"today", "tomorrow", "i dag", "i morgen"}
                    and old_due.get("phrase") == new_due.get("phrase")
                    and old_due.get("timezone") != new_due.get("timezone")
                    and normalized(machine["title"]) == normalized(proposal["title"])
                )
                deadlines_match = (
                    not old_identity or not new_identity or old_identity == new_identity
                )
                if same and similar and compatible and (deadlines_match or calendar_change):
                    matches.append(row)
            exact = [
                r
                for r in matches
                if normalized(json.loads(r["proposal"])["title"]) == normalized(proposal["title"])
            ]
            if len(exact) == 1:
                match = exact[0]
            elif len(matches) == 1:
                match = matches[0]
            if match:
                key = match["id"]
                accepted = match["accepted_deadline"]
                health = "current"
                if accepted is not None and json.loads(accepted) != proposal["deadline"]:
                    health = "needs_review"
                db.execute(
                    "UPDATE inbox_items SET proposal=?,health=?,version=version+1,"
                    "updated_at=? WHERE id=?",
                    (json.dumps(proposal), health, now, key),
                )
            else:
                key = str(uuid4())
                db.execute(
                    "INSERT INTO inbox_items(id,proposal,created_at,updated_at) VALUES(?,?,?,?)",
                    (key, json.dumps(proposal), now, now),
                )
            assigned.add(key)
            db.execute(
                "INSERT OR REPLACE INTO inbox_occurrences VALUES(?,?,?)",
                (job["id"], key, json.dumps(spans)),
            )
            db.execute(
                "INSERT INTO inbox_events VALUES(?,?,?,?,?)",
                (
                    str(uuid4()),
                    key,
                    "extracted",
                    json.dumps(
                        {
                            "job_id": job["id"],
                            "proposal": proposal,
                            "model": job["model"],
                            "prompt_version": job["prompt_version"],
                        }
                    ),
                    now,
                ),
            )
