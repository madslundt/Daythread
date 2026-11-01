"""Opt-in, evidence-qualified capture reminders; a stale report is not audio loss."""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .domain import stamp, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_health_state (
 phone_id TEXT PRIMARY KEY, incident TEXT NOT NULL, last_alert_at TEXT
);
"""
MAX_EXPECTATION_AGE = timedelta(hours=24)


class ObservationChanged(Exception):
    pass


def _time(value):
    if not value:
        return None
    result = datetime.fromisoformat(value)
    return result if result.tzinfo is not None else None


def quiet_hours(settings, now):
    start, end = settings.capture_health_quiet_start_hour, settings.capture_health_quiet_end_hour
    hour = now.astimezone(ZoneInfo(settings.timezone)).hour
    return start != end and (start <= hour < end if start < end else hour >= start or hour < end)


def assessments(db, settings, now):
    result = []
    threshold = timedelta(minutes=settings.capture_health_stall_minutes)
    for row in db.execute("SELECT * FROM phone_observations ORDER BY phone_id"):
        phone = json.loads(row["payload"])
        state = db.execute(
            "SELECT * FROM capture_states WHERE phone_id=? AND occurred_at<=? "
            "ORDER BY occurred_at DESC,event_id DESC LIMIT 1", (row["phone_id"], stamp(now)),
        ).fetchone()
        observed = _time(row["observed_at"])
        status = "unknown"
        # Decoder activity alone cannot establish that audio is safely saved.
        pendant = phone.get("capture_source") == "omi"
        progress = [_time(phone.get("last_persisted_at"))] if pendant else []
        if pendant and phone.get("device_id"):
            audio = db.execute(
                "SELECT max(ended_at) AS ended_at FROM audio "
                "WHERE device_id=? AND source_kind='' AND started_at<=?",
                (phone["device_id"], stamp(now)),
            ).fetchone()
            progress.append(_time(audio["ended_at"]))
        # A recorded start provides a grace period even before the first audio.
        if pendant and state and state["state"] == "capturing":
            progress.append(_time(state["occurred_at"]))
        latest = max((at for at in progress if at and at <= now), default=None)
        intentional = phone.get("requested_pause") or phone.get("capture_state") == "paused"
        if (observed and state and _time(state["occurred_at"]) >= observed
                and state["state"] == "paused"):
            intentional = True
        if row["historical"] or not observed or observed > now:
            status = "unknown"
        elif settings.capture_paused or intentional:
            status = "paused"
        elif not pendant:
            # Phone-recorder drafts do not yet report durable progress under a
            # source-specific contract. Old pendant progress cannot qualify them.
            status = "unknown"
        elif phone.get("capture_state") not in {"capturing", "disconnected", "blocked"}:
            status = "unknown"
        elif now - observed > MAX_EXPECTATION_AGE or latest is None:
            status = "unknown"
        elif now - latest < threshold:
            status = "healthy"
        elif now - observed >= threshold:
            status = "report_stale"
        else:
            status = "capture_stalled"
        incident = hashlib.sha256(
            (row["phone_id"] + ":" + (stamp(latest) if latest else "unknown")).encode()
        ).hexdigest()
        result.append({
            "phone_id": row["phone_id"], "status": status,
            "last_progress_at": stamp(latest) if latest else None,
            "observed_at": row["observed_at"], "incident": incident,
            "snapshot": (row["sequence"], row["received_at"], state["event_id"] if state else None,
                         stamp(latest) if latest else None),
        })
    return result


def capture_health_status(store, settings, *, now=None):
    now = now or utc_now()
    with store.connect() as db:
        db.execute("BEGIN")
        phones = assessments(db, settings, now)
    return {
        "enabled": settings.capture_health_enabled, "as_of": stamp(now),
        "quiet_hours": quiet_hours(settings, now),
        "phones": [
            {key: value for key, value in phone.items() if key not in {"snapshot", "incident"}}
            for phone in phones
        ],
    }


def check_capture_health(memory, *, now=None):
    """Publish once per stalled incident, with a persistent cross-incident cooldown."""
    settings, store = memory.settings, memory.store
    if not settings.capture_health_enabled:
        store.clear_capture_health_notifications()
        return []
    now = now or utc_now()
    with store.connect() as db:
        db.execute("BEGIN")
        phones = assessments(db, settings, now)
    emitted = []
    for phone in phones:
        if phone["status"] not in {"capture_stalled", "report_stale"}:
            # Recovery or intentional stop cancels unsent reminders and the feed
            # item, so opening the app later cannot surface an obsolete alarm.
            store.clear_capture_health_notifications(phone["phone_id"])
            continue
        if quiet_hours(settings, now):
            continue

        def guard(db, phone=phone):
            current = next((value for value in assessments(db, settings, now)
                            if value["phone_id"] == phone["phone_id"]), None)
            if current is None or current["snapshot"] != phone["snapshot"]:
                raise ObservationChanged
            previous = db.execute(
                "SELECT * FROM capture_health_state WHERE phone_id=?", (phone["phone_id"],),
            ).fetchone()
            if previous and (previous["incident"] == phone["incident"] or (
                previous["last_alert_at"] and now - _time(previous["last_alert_at"])
                < timedelta(minutes=settings.capture_health_cooldown_minutes)
            )):
                raise ObservationChanged
            db.execute(
                "INSERT INTO capture_health_state(phone_id,incident,last_alert_at) VALUES(?,?,?) "
                "ON CONFLICT(phone_id) DO UPDATE SET incident=excluded.incident,"
                "last_alert_at=excluded.last_alert_at",
                (phone["phone_id"], phone["incident"], stamp(now)),
            )

        issue = phone["status"]
        body = (
            "The phone has stopped reporting capture progress. Open Capture to check the phone, "
            "pendant connection and upload queue. Current capture is unknown."
            if issue == "report_stale" else
            "The phone reports no recent capture progress. Open Capture to check the microphone, "
            "pendant connection and available phone storage."
        )
        try:
            emitted.append(store.publish_notification(
                "capture_health", "Capture needs a check", body,
                {"screen": "capture", "phone_id": phone["phone_id"], "issue": issue},
                "capture-health:" + phone["incident"], guard=guard,
            ))
        except ObservationChanged:
            pass
    return emitted
