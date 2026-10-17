"""Local observations and evidence provenance; none of these measures establish accuracy."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .domain import EvidenceChanged, stamp, utc_now
from .processing_progress import processing_observation
from .vendor_imports import TRANSCRIPT_IMPORT_SQL, transcript_import

SCHEMA = """
CREATE TABLE IF NOT EXISTS phone_observations (
 phone_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, observed_at TEXT NOT NULL,
 received_at TEXT NOT NULL, payload TEXT NOT NULL, historical INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS capture_states (
 event_id TEXT PRIMARY KEY, phone_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
 state TEXT NOT NULL, cause TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS capture_states_time ON capture_states(phone_id,occurred_at);
CREATE TABLE IF NOT EXISTS output_evidence (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS feedback_events (
 event_id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL, payload TEXT NOT NULL,
 created_at TEXT NOT NULL
);
"""


def union_seconds(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime):
    merged = []
    for left, right in sorted((max(a, start), min(b, end)) for a, b in intervals):
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return sum((b - a).total_seconds() for a, b in merged), merged


def audio_availability(row: dict) -> str:
    if transcript_import(row.get("source_kind", "")):
        return "transcript_only"
    if not row.get("audio_available", True):
        return "expired"
    return "available" if Path(row["path"]).is_file() else "missing"


def revision(source: dict) -> str:
    return hashlib.sha256((source.get("text") or "").encode()).hexdigest()


class CompletenessStore:
    def observations(self, now: datetime | None = None) -> dict:
        now = now or utc_now()
        with self.connect() as db:
            db.execute("BEGIN")
            local = f"NOT {TRANSCRIPT_IMPORT_SQL}"
            times = dict(
                db.execute(
                    "SELECT max(received_at) AS last_received_at,"
                    "max(CASE WHEN source_kind!='omi_audio_webhook' THEN ended_at END) "
                    "AS last_captured_at "
                    f"FROM audio WHERE {local}"
                ).fetchone()
            )
            queue = dict(
                db.execute(
                    "SELECT count(*) FILTER(WHERE state='queued') AS queued,"
                    "count(*) FILTER(WHERE state='transcribing') AS transcribing,"
                    "count(*) FILTER(WHERE state='queued' AND error IS NOT NULL) AS retrying,"
                    "count(*) FILTER(WHERE state='blocked') AS blocked,"
                    "min(received_at) FILTER(WHERE state IN ('queued','transcribing','blocked')) "
                    "AS oldest_received_at FROM audio"
                ).fetchone()
            )
            queue["recent_errors"] = [
                dict(row)
                for row in db.execute(
                    "SELECT id,error_code,attempts,retry_at FROM audio WHERE error IS NOT NULL "
                    "AND state IN ('queued','transcribing','blocked') "
                    "ORDER BY received_at DESC LIMIT 5"
                )
            ]
            oldest = queue["oldest_received_at"]
            queue["oldest_age_seconds"] = (
                max(0, (now - datetime.fromisoformat(oldest)).total_seconds()) if oldest else None
            )
            completed = db.execute(
                "SELECT received_at,processed_at FROM audio WHERE processed_at IS NOT NULL "
                "AND received_at IS NOT NULL ORDER BY processed_at DESC LIMIT 1"
            ).fetchone()
            queue["last_processed_at"] = completed["processed_at"] if completed else None
            queue["last_completion_delay_seconds"] = (
                max(
                    0,
                    (
                        datetime.fromisoformat(completed["processed_at"])
                        - datetime.fromisoformat(completed["received_at"])
                    ).total_seconds(),
                )
                if completed
                else None
            )
            queue.update(processing_observation(db, now))
            phones = []
            for row in db.execute("SELECT * FROM phone_observations ORDER BY received_at DESC"):
                age = max(0, (now - datetime.fromisoformat(row["observed_at"])).total_seconds())
                phones.append(
                    json.loads(row["payload"])
                    | {
                        "received_at": row["received_at"],
                        "age_seconds": age,
                        "fresh": age <= 120 and not row["historical"],
                        "historical": bool(row["historical"]),
                        "phone_id": row["phone_id"],
                    }
                )
            audio_counts = {
                r["state"]: r["n"]
                for r in db.execute("SELECT state,count(*) AS n FROM audio GROUP BY state")
            }
            gaps = db.execute("SELECT count(*) FROM capture_gaps").fetchone()[0]
        return {
            "as_of": stamp(now),
            "audio": audio_counts,
            "ingestion": times,
            "processing": queue,
            "phones": phones,
            "capture_gaps": gaps,
        }

    def save_phone(self, phone_id: str, body: dict) -> dict:
        serialized = json.dumps(body, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM phone_observations WHERE phone_id=?", (phone_id,)
            ).fetchone()
            if old and old["sequence"] > body["sequence"]:
                raise ValueError("Stale phone observation")
            if old and old["sequence"] == body["sequence"] and old["payload"] != serialized:
                raise ValueError("Observation sequence already used with different metadata")
            if not old or old["sequence"] < body["sequence"]:
                db.execute(
                    "INSERT OR REPLACE INTO phone_observations "
                    "(phone_id,sequence,observed_at,received_at,payload) VALUES(?,?,?,?,?)",
                    (phone_id, body["sequence"], body["observed_at"], stamp(utc_now()), serialized),
                )
            row = db.execute(
                "SELECT * FROM phone_observations WHERE phone_id=?", (phone_id,)
            ).fetchone()
        return json.loads(row["payload"]) | {
            "phone_id": phone_id,
            "received_at": row["received_at"],
        }

    def save_capture_state(self, event_id: str, body: dict) -> dict:
        values = (event_id, *(body[key] for key in ("phone_id", "occurred_at", "state", "cause")))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM capture_states WHERE event_id=?", (event_id,)
            ).fetchone()
            if old and tuple(old) != values:
                raise ValueError("Capture state ID already exists with different metadata")
            db.execute("INSERT OR IGNORE INTO capture_states VALUES(?,?,?,?,?)", values)
        return {"event_id": event_id, **body}

    def completeness(
        self, start: datetime, end: datetime, offset: int = 0, limit: int = 100
    ) -> dict:
        requested_end = end
        end = max(start, min(end, utc_now()))
        # One read snapshot for all counts and observations within this interval.
        with self.connect() as db:
            db.execute("BEGIN")
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT id,started_at,ended_at,state,audio_available,path,source_kind,"
                    "transcription_outcome,length(coalesce(text,''))>0 AS has_text "
                    "FROM audio WHERE started_at<? AND ended_at>? ORDER BY started_at,id",
                    (stamp(end), stamp(start)),
                )
            ]
            gaps = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM capture_gaps WHERE started_at<? AND ended_at>? "
                    "ORDER BY started_at,gap_id",
                    (stamp(end), stamp(start)),
                )
            ]
            states = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM capture_states WHERE occurred_at<? "
                    "ORDER BY phone_id,occurred_at,event_id",
                    (stamp(end),),
                )
            ]

        def intervals(selected):
            return [
                (datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["ended_at"]))
                for r in selected
            ]

        available = [r for r in rows if r["audio_available"] and Path(r["path"]).is_file()]
        text = [r for r in rows if r["state"] == "done" and r["has_text"]]
        pauses, interruptions = [], []
        capture_intervals = []
        for index, event in enumerate(states):
            right = (
                datetime.fromisoformat(states[index + 1]["occurred_at"])
                if (index + 1 < len(states) and states[index + 1]["phone_id"] == event["phone_id"])
                else end
            )
            left = datetime.fromisoformat(event["occurred_at"])
            if (
                right > start
                and left < end
                and event["state"] in ("paused", "disconnected", "blocked")
            ):
                capture_intervals.append(
                    {
                        "event_id": event["event_id"],
                        "phone_id": event["phone_id"],
                        "start": stamp(max(left, start)),
                        "end": stamp(min(right, end)),
                        "state": event["state"],
                        "cause": event["cause"],
                        "open": index + 1 == len(states)
                        or states[index + 1]["phone_id"] != event["phone_id"],
                    }
                )
            # Pauses persist until an explicit transition. Disconnect/failure intervals are
            # observations, never a claim that capture resumed when reporting went silent.
            if event["state"] == "paused":
                pauses.append((left, right))
            elif event["state"] in ("disconnected", "blocked"):
                interruptions.append((left, right))
        estimated = [r for r in rows if r["source_kind"] == "omi_audio_webhook"]
        tracks = {
            "available_audio": intervals(
                [r for r in available if r["source_kind"] != "omi_audio_webhook"]
            ),
            "estimated_audio": intervals(
                [r for r in available if r["source_kind"] == "omi_audio_webhook"]
            ),
            "transcript": intervals([r for r in text if r["source_kind"] != "omi_audio_webhook"]),
            "estimated_transcript": intervals(
                [r for r in text if r["source_kind"] == "omi_audio_webhook"]
            ),
            "intentional_pause": pauses,
            "reported_interruption": interruptions + intervals(gaps),
        }
        durations = {key: union_seconds(value, start, end)[0] for key, value in tracks.items()}
        # Even receipt of every transcript does not establish continuous audio capture.
        known = (
            intervals([r for r in rows if r["source_kind"] != "omi_audio_webhook"])
            + pauses
            + interruptions
            + intervals(gaps)
        )
        _, merged = union_seconds(known, start, end)
        unknown = []
        cursor = start
        for left, right in merged:
            if left > cursor:
                unknown.append({"start": stamp(cursor), "end": stamp(left)})
            cursor = right
        if cursor < end:
            unknown.append({"start": stamp(cursor), "end": stamp(end)})
        categories = {
            "webhook_receipt_estimates": len(estimated),
            "pending": sum(r["state"] in ("queued", "transcribing") for r in rows),
            "blocked": sum(r["state"] == "blocked" for r in rows),
            "expired_transcripts": sum(r["state"] == "expired" for r in rows),
            "transcript_only_imports": sum(transcript_import(r["source_kind"]) for r in rows),
            "expired_audio": sum(
                not r["audio_available"] and not transcript_import(r["source_kind"]) for r in rows
            ),
            "missing_audio": sum(
                r["audio_available"] and not Path(r["path"]).is_file() for r in rows
            ),
            "silence_gate": sum(r["transcription_outcome"] == "silence_gate" for r in rows),
            "empty_stt": sum(r["transcription_outcome"] == "empty_stt" for r in rows),
        }
        return {
            "start": stamp(start),
            "end": stamp(requested_end),
            "observed_until": stamp(end),
            "as_of": stamp(utc_now()),
            "seconds": durations,
            "counts": categories,
            "source_count": len(rows),
            "unknown_intervals": unknown[offset : offset + limit],
            "unknown_count": len(unknown),
            "capture_gaps": gaps[offset : offset + limit],
            "capture_gap_count": len(gaps),
            "capture_intervals": capture_intervals[offset : offset + limit],
            "capture_interval_count": len(capture_intervals),
            "offset": offset,
            "limit": limit,
            "truncated": max(len(gaps), len(unknown), len(capture_intervals)) > offset + limit,
        }

    def selection_counts(self, start: datetime | None, end: datetime | None) -> dict:
        scope = " WHERE started_at<? AND ended_at>?" if start and end else ""
        args = (stamp(end), stamp(start)) if start and end else ()
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) FILTER(WHERE state='done' AND text!='') AS eligible_transcripts,"
                "count(*) FILTER(WHERE state IN ('queued','transcribing')) AS pending_chunks,"
                "count(*) FILTER(WHERE state='blocked') AS blocked_chunks FROM audio" + scope,
                args,
            ).fetchone()
        return dict(row) | {"selection_observed_at": stamp(utc_now())}

    def _save_evidence(self, db, output_id: str, evidence: dict, sources: list[dict]):
        self.verify_sources(db, sources)
        db.execute("INSERT INTO output_evidence VALUES(?,?)", (output_id, json.dumps(evidence)))

    def save_evidence(self, output_id: str, evidence: dict, sources: list[dict]):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._save_evidence(db, output_id, evidence, sources)

    def evidence(self, output_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM output_evidence WHERE id=?", (output_id,)
            ).fetchone()
            if not row:
                return None
            result = json.loads(row[0])
            changed = []
            for source in result["selected"]:
                current = db.execute(
                    "SELECT text,transcript_version,source_version FROM audio WHERE id=?",
                    (source["id"],),
                ).fetchone()
                if (
                    not current
                    or revision(dict(current)) != source["revision"]
                    or (
                        source.get("source_version") is not None
                        and source["source_version"] != current["source_version"]
                    )
                    or (
                        source.get("transcript_version") is not None
                        and source["transcript_version"] != current["transcript_version"]
                    )
                ):
                    changed.append(source["id"])
        return result | {"changed_source_ids": changed}

    def save_feedback(self, key: str, body: dict) -> dict:
        payload = json.dumps(body, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM feedback WHERE id=?", (key,)).fetchone()
            if old:
                if old["payload"] != payload:
                    raise ValueError("Feedback ID already exists with different metadata")
                return self._feedback_row(db, old)
            for source_id in [body.get("source_id"), *body.get("expected_source_ids", [])]:
                if (
                    source_id
                    and not db.execute("SELECT id FROM audio WHERE id=?", (source_id,)).fetchone()
                ):
                    raise EvidenceChanged("Feedback source was deleted")
            if (
                body.get("output_id")
                and not db.execute(
                    "SELECT id FROM output_evidence WHERE id=?", (body["output_id"],)
                ).fetchone()
            ):
                raise EvidenceChanged("Feedback output was deleted or is unavailable")
            db.execute(
                "INSERT INTO feedback(id,payload,created_at) VALUES(?,?,?)",
                (key, payload, stamp(utc_now())),
            )
            return self._feedback_row(
                db, db.execute("SELECT * FROM feedback WHERE id=?", (key,)).fetchone()
            )

    def _feedback_row(self, db, row) -> dict:
        body = json.loads(row["payload"])
        suggested = "unknown"
        expected_ids = set(body.get("expected_source_ids", []))
        referenced = expected_ids | ({body["source_id"]} if body.get("source_id") else set())
        sources = [
            source
            for source_id in referenced
            if (
                source := db.execute(
                    "SELECT id,state,text FROM audio WHERE id=?",
                    (source_id,),
                ).fetchone()
            )
        ]
        if any(source["state"] == "blocked" for source in sources):
            suggested = "transcription_blocked"
        elif any(source["state"] in ("queued", "transcribing") for source in sources):
            suggested = "transcription_pending"
        elif any(source["state"] == "expired" for source in sources):
            suggested = "evidence_unavailable"
        elif body.get("output_id"):
            output = db.execute(
                "SELECT payload FROM output_evidence WHERE id=?", (body["output_id"],)
            ).fetchone()
            if output:
                selected = {s["id"] for s in json.loads(output[0])["selected"]}
                searchable = {s["id"] for s in sources if s["state"] == "done" and s["text"]}
                if expected_ids & searchable - selected:
                    suggested = "retrieval"
        events = [
            json.loads(r["payload"]) | {"created_at": r["created_at"]}
            for r in db.execute(
                "SELECT * FROM feedback_events WHERE feedback_id=? ORDER BY created_at,event_id",
                (row["id"],),
            )
        ]
        return body | {
            "id": row["id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "suggested_stage": suggested,
            "events": events,
        }

    def feedback(self, limit: int = 50, offset: int = 0) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            total = db.execute("SELECT count(*) FROM feedback").fetchone()[0]
            items = [
                self._feedback_row(db, row)
                for row in db.execute(
                    "SELECT * FROM feedback ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def resolve_feedback(self, key: str, event: dict) -> dict:
        payload = json.dumps(event, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM feedback WHERE id=?", (key,)).fetchone()
            if not row:
                raise KeyError(key)
            old = db.execute(
                "SELECT payload,feedback_id FROM feedback_events WHERE event_id=?",
                (event["event_id"],),
            ).fetchone()
            if old and (old[0] != payload or old[1] != key):
                raise ValueError("Feedback event ID already used")
            if not old:
                db.execute(
                    "INSERT INTO feedback_events VALUES(?,?,?,?)",
                    (event["event_id"], key, payload, stamp(utc_now())),
                )
                db.execute("UPDATE feedback SET status=? WHERE id=?", (event["status"], key))
            return self._feedback_row(
                db, db.execute("SELECT * FROM feedback WHERE id=?", (key,)).fetchone()
            )

    def delete_feedback(self, key: str):
        with self.connect() as db:
            db.execute("DELETE FROM feedback WHERE id=?", (key,))
            db.execute("DELETE FROM feedback_events WHERE feedback_id=?", (key,))

    @staticmethod
    def purge_diagnostics(db):
        # Free text may repeat removed evidence. Match the existing conservative purge.
        for table in ("output_evidence", "feedback_events", "feedback"):
            db.execute(f"DELETE FROM {table}")
