"""Profile-local usage measurements, independent of recording retention.

Only durations, text/token counts, task categories and billing metadata enter this
ledger. Audio, transcripts, prompts, credentials and responses are never stored here.
"""

import json
import re
from datetime import UTC, date, datetime
from math import isfinite
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
 event_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL,
 outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_event_time ON usage_events(created_at);
"""


def _seconds(value) -> float:
    number = float(value)
    if not isfinite(number) or number < 0:
        raise ValueError("usage_duration_invalid")
    return number


def _metrics() -> dict:
    return dict.fromkeys((
        "recordings", "captured_minutes", "detected_speech_minutes", "retained_minutes",
        "submitted_minutes", "transcription_credits", "summary_credits", "other_credits",
        "unknown_credit_attempts", "provider_attempts", "managed_attempts", "failed_attempts",
        "filter_unavailable_recordings",
        "summary_original_characters", "summary_retained_characters",
        "summary_submitted_characters", "summary_removed_fillers", "summary_omitted_sources",
        "summary_filter_runs", "summary_skipped_runs", "summary_requests",
        "summary_input_tokens", "summary_output_tokens", "summary_unknown_token_requests",
        "summary_unknown_character_requests",
        "summary_source_minutes", "summary_unknown_duration_runs",
    ), 0)


def _count(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("usage_count_invalid")
    return value


def _summary(event: dict) -> bool:
    return event["kind"] == "llm" and event.get("task") in {"summary", "recap", "weekly"}


class UsageLedger:
    def __init__(self, store):
        self.store = store

    def record(self, event: dict) -> None:
        kind = event["kind"]
        if kind not in {"filter", "stt", "llm", "summary_filter"}:
            raise ValueError("usage_kind_invalid")
        created = datetime.fromisoformat(event.get("created_at") or datetime.now(UTC).isoformat())
        if created.tzinfo is None:
            raise ValueError("usage_timestamp_naive")
        created_at = created.astimezone(UTC).isoformat()
        payload = {"kind": kind}
        if event.get("recording_id"):
            payload["recording_id"] = str(event["recording_id"])
        if event.get("summary_run_id"):
            payload["summary_run_id"] = str(UUID(str(event["summary_run_id"])))
        if kind == "summary_filter":
            if "summary_run_id" not in payload:
                raise ValueError("usage_summary_run_id_missing")
            version = event["filter_version"]
            if not isinstance(version, str) or not version or len(version) > 128:
                raise ValueError("usage_filter_version_invalid")
            payload["filter_version"] = version
            for field in (
                "original_characters", "retained_characters", "removed_fillers",
                "omitted_sources", "source_count",
            ):
                payload[field] = _count(event[field])
            if payload["retained_characters"] > payload["original_characters"]:
                raise ValueError("usage_retained_characters_invalid")
            if payload["omitted_sources"] > payload["source_count"]:
                raise ValueError("usage_omitted_sources_invalid")
            seconds = event.get("source_seconds")
            payload["source_seconds"] = _seconds(seconds) if seconds is not None else None
            complete = event.get("source_duration_complete", False)
            if type(complete) is not bool or complete and seconds is None:
                raise ValueError("usage_source_duration_invalid")
            payload["source_duration_complete"] = complete
            event_key = "summary_filter:" + payload["summary_run_id"]
        elif kind == "filter":
            if "recording_id" not in payload:
                raise ValueError("usage_recording_id_missing")
            status = event["filter_status"]
            if status not in {"filtered", "disabled", "unavailable", "legacy_gate"}:
                raise ValueError("usage_filter_status_invalid")
            payload.update(
                original_seconds=_seconds(event["original_seconds"]),
                retained_seconds=_seconds(event["retained_seconds"]),
                detected_speech_seconds=(
                    _seconds(event["detected_speech_seconds"])
                    if event.get("detected_speech_seconds") is not None else None
                ),
                filter_status=status,
            )
            if payload["retained_seconds"] > payload["original_seconds"] + 0.001:
                raise ValueError("usage_retained_duration_invalid")
            event_key = "filter:" + payload["recording_id"]
        else:
            billing = event.get("billing", "unknown")
            outcome = event.get("outcome", "unknown")
            if billing not in {"credits", "provider", "unknown"}:
                raise ValueError("usage_billing_invalid")
            if outcome not in {"pending", "succeeded", "failed", "unknown"}:
                raise ValueError("usage_outcome_invalid")
            units = event.get("credit_units")
            if units is not None and (type(units) is not int or units < 0):
                raise ValueError("usage_credit_units_invalid")
            payload.update(
                task=str(event.get("task", kind)), billing=billing, outcome=outcome,
                credit_units=units,
            )
            if kind == "stt":
                payload["submitted_seconds"] = _seconds(event["submitted_seconds"])
            else:
                for field in ("submitted_characters", "input_tokens", "output_tokens"):
                    value = event.get(field)
                    payload[field] = _count(value) if value is not None else None
            operation = event.get("operation_id")
            if operation:
                payload["operation_id"] = str(operation)
            attempt = event.get("attempt_id")
            if attempt:
                payload["attempt_id"] = str(attempt)
            identity = attempt or operation
            event_key = f"{kind}:{identity}" if identity else str(uuid4())
        with self.store.connect() as db:
            # A repeated observation updates the measurement, while preserving the
            # first observation's date. Known managed operations are charged once.
            db.execute(
                "INSERT INTO usage_events(event_key,created_at,payload,outcome) VALUES(?,?,?,?) "
                "ON CONFLICT(event_key) DO UPDATE SET payload=excluded.payload, "
                "outcome=excluded.outcome "
                "WHERE excluded.outcome!='pending' OR usage_events.outcome='pending'",
                (event_key, created_at, json.dumps(payload, separators=(",", ":")),
                 payload.get("outcome", "filtered")),
            )

    def report(self, timezone: str = "UTC", *, month: str | None = None) -> dict:
        zone = ZoneInfo(timezone)
        if month is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
                raise ValueError("usage_month_invalid")
            date.fromisoformat(f"{month}-01")
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT created_at,payload FROM usage_events ORDER BY created_at,event_key"
            ).fetchall()
        # Several HTTP attempts may refer to one idempotent managed operation.
        # Prefer its settled, priced observation over pending/unknown attempts.
        observations = {}
        summary_attempts = []
        for index, row in enumerate(rows):
            event = json.loads(row["payload"])
            if _summary(event):
                summary_attempts.append((row, event))
            operation = event.get("operation_id")
            key = (event["kind"], operation) if operation else ("attempt", index)
            current = observations.get(key)
            score = (event.get("credit_units") is not None, event.get("outcome") != "pending")
            if current is None:
                observations[key] = (row, event, score)
            else:
                selected, alternate = (event, current[1]) if score >= current[2] else (
                    current[1], event
                )
                selected = dict(selected)
                for field in ("input_tokens", "output_tokens"):
                    if selected.get(field) is None and alternate.get(field) is not None:
                        selected[field] = alternate[field]
                observations[key] = (current[0], selected, max(score, current[2]))
        totals, days = _metrics(), {}
        for row, event, _score in observations.values():
            day = datetime.fromisoformat(row["created_at"]).astimezone(zone).date().isoformat()
            if month is not None and not day.startswith(month + "-"):
                continue
            daily = days.setdefault(day, _metrics())
            values = _metrics()
            if event["kind"] == "summary_filter":
                values.update(
                    summary_original_characters=event["original_characters"],
                    summary_retained_characters=event["retained_characters"],
                    summary_removed_fillers=event["removed_fillers"],
                    summary_omitted_sources=event["omitted_sources"],
                    summary_filter_runs=1,
                    summary_skipped_runs=int(event["retained_characters"] == 0),
                    summary_source_minutes=(event.get("source_seconds") or 0) / 60,
                    summary_unknown_duration_runs=int(
                        not event.get("source_duration_complete", False)
                    ),
                )
            elif event["kind"] == "filter":
                values.update(
                    recordings=1,
                    captured_minutes=event["original_seconds"] / 60,
                    detected_speech_minutes=(event["detected_speech_seconds"] or 0) / 60,
                    retained_minutes=event["retained_seconds"] / 60,
                    filter_unavailable_recordings=int(event["filter_status"] == "unavailable"),
                )
            else:
                billing = event["billing"]
                values["failed_attempts"] = int(event["outcome"] == "failed")
                values["provider_attempts"] = int(billing == "provider")
                values["managed_attempts"] = int(billing == "credits")
                values["unknown_credit_attempts"] = int(
                    billing == "unknown" or billing == "credits" and event["credit_units"] is None
                )
                if event["kind"] == "stt":
                    values["submitted_minutes"] = event["submitted_seconds"] / 60
                    credit_category = "transcription_credits"
                else:
                    credit_category = (
                        "summary_credits" if event["task"] in {"summary", "recap", "weekly"}
                        else "other_credits"
                    )
                if billing == "credits" and event["credit_units"] is not None:
                    values[credit_category] = event["credit_units"] / 1000
                if _summary(event):
                    values["summary_input_tokens"] = event.get("input_tokens") or 0
                    values["summary_output_tokens"] = event.get("output_tokens") or 0
            for key, value in values.items():
                totals[key] += value
                daily[key] += value
        # Request text is submitted on every HTTP attempt, including retries
        # returning an already-charged operation. Token receipts above count once.
        for row, event in summary_attempts:
            day = datetime.fromisoformat(row["created_at"]).astimezone(zone).date().isoformat()
            if month is not None and not day.startswith(month + "-"):
                continue
            daily = days.setdefault(day, _metrics())
            values = {
                "summary_requests": 1,
                "summary_submitted_characters": event.get("submitted_characters") or 0,
                "summary_unknown_character_requests": int(
                    event.get("submitted_characters") is None
                ),
                "summary_unknown_token_requests": int(
                    event.get("input_tokens") is None or event.get("output_tokens") is None
                ),
            }
            for key, value in values.items():
                totals[key] += value
                daily[key] += value
        for metrics in [totals, *days.values()]:
            captured = metrics["captured_minutes"]
            metrics["filtered_percent"] = (
                100 * max(0, captured - metrics["retained_minutes"]) / captured
                if captured else None
            )
            original = metrics["summary_original_characters"]
            metrics["summary_filtered_percent"] = (
                100 * max(0, original - metrics["summary_retained_characters"]) / original
                if original else None
            )
        return {
            "since": rows[0]["created_at"] if rows else None,
            "timezone": timezone,
            "month": month,
            "totals": totals,
            "credits_complete": totals["unknown_credit_attempts"] == 0,
            "days": [{"date": day, **days[day]} for day in sorted(days, reverse=True)],
        }
