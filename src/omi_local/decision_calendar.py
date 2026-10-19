"""Portable RFC 5545 exports; creating a file never writes to an external calendar."""

from datetime import UTC, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .decision_followups import CalendarExportRequest, FollowUp, current_decision
from .domain import utc_now


def escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold(line: str) -> str:
    # RFC 5545's 75-octet limit counts the continuation space and UTF-8 bytes.
    lines, current, size = [], "", 0
    for char in line:
        length = len(char.encode("utf-8"))
        if size + length > 75:
            lines.append(current)
            current, size = " ", 1
        current += char
        size += length
    lines.append(current)
    return "\r\n".join(lines)


def calendar_time(name: str, value: datetime, all_day: bool) -> str:
    if all_day:
        return f"{name};VALUE=DATE:{value.strftime('%Y%m%d')}"
    try:
        return f"{name}:{value.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    except (OverflowError, ValueError) as exc:
        raise ValueError("Calendar time is outside the supported UTC date range") from exc


def compose_dates(item: FollowUp, timezone: str) -> tuple[datetime, datetime, str]:
    """Fill missing composer dates without changing inferred or persisted suggestion data."""
    zone = ZoneInfo(timezone)
    start, end = item.start_at, item.end_at
    missing = []
    try:
        if item.all_day:
            if start is None:
                day = end.date() - timedelta(days=1) if end else utc_now().astimezone(zone).date()
                start = datetime.combine(day, time.min, zone)
                missing.append(f"start date {start.date()}")
            if end is None:
                end = datetime.combine(start.date() + timedelta(days=1), time.min, zone)
                missing.append(f"exclusive end date {end.date()}")
        else:
            if start is None:
                if end:
                    start = (end.astimezone(UTC) - timedelta(hours=1)).astimezone(zone)
                else:
                    local_hour = (
                        utc_now().astimezone(zone).replace(minute=0, second=0, microsecond=0)
                    )
                    start = (local_hour.astimezone(UTC) + timedelta(hours=1)).astimezone(zone)
                missing.append(f"start {start.isoformat()}")
            if end is None:
                end = (start.astimezone(UTC) + timedelta(hours=1)).astimezone(zone)
                missing.append(f"end {end.isoformat()}")
    except (OverflowError, ValueError) as exc:
        raise ValueError("Calendar defaults are outside the supported date range") from exc
    annotation = (
        "Calendar defaults (not stated in the recording): " + "; ".join(missing) + "."
        if missing
        else ""
    )
    return start, end, annotation


def export_calendar(memory, key: str, body: CalendarExportRequest) -> dict:
    with memory.store.connect() as db:
        db.execute("BEGIN")
        row, _ = current_decision(memory.store, key, body.expected_version, db)
        return calendar_file(key, row["version"], body, memory.settings.timezone)


def calendar_file(
    key: str,
    version: int,
    body: CalendarExportRequest,
    timezone: str,
    *,
    context: str = "decision",
) -> dict:
    """Serialize a reviewed follow-up after its owning context checked the evidence."""
    item = body.suggestion
    if item.location_trigger:
        raise ValueError(
            "Open this follow-up on iPhone to confirm its place and location alert"
        )
    start, end, defaults_note = item.start_at, item.end_at, ""
    component = "VEVENT" if item.kind == "event" else "VTODO"
    if item.kind == "event":
        if body.use_calendar_defaults and (start is None or end is None):
            start, end, defaults_note = compose_dates(item, timezone)
        if not start or not end:
            raise ValueError("Choose both start and end before exporting an event")
        if item.all_day and end.date() <= start.date():
            raise ValueError("All-day event end date must follow its start date (exclusive)")
    schedule = item.start_at if item.kind == "event" else item.due_at
    schedule_key = calendar_time("AT", schedule, item.all_day) if schedule else "undated"
    if item.kind == "event" and schedule is None and item.end_at:
        schedule_key = calendar_time("END", item.end_at, item.all_day)
    uid = uuid5(
        NAMESPACE_URL, f"omi-local:{context}:{key}:{item.kind}:{item.title}:{schedule_key}"
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//Daythread//{context.title()} follow-ups//EN",
        "CALSCALE:GREGORIAN",
        f"BEGIN:{component}",
        f"UID:{uid}@omi-local",
        calendar_time("DTSTAMP", utc_now(), False),
        f"SEQUENCE:{version}",
        f"SUMMARY:{escape(item.title)}",
        "DESCRIPTION:"
        + escape(
            "\n\n".join(
                filter(None, [item.notes, item.reason, defaults_note, f"Omi {context}: {key}"])
            )
        ),
    ]
    if item.location:
        lines.append(f"LOCATION:{escape(item.location)}")
    if item.kind == "event":
        lines += [
            calendar_time("DTSTART", start, item.all_day),
            calendar_time("DTEND", end, item.all_day),
        ]
    else:
        lines.append("STATUS:NEEDS-ACTION")
        if item.due_at:
            lines.append(calendar_time("DUE", item.due_at, item.all_day))
    lines += [f"END:{component}", "END:VCALENDAR"]
    return {
        "filename": f"omi-{item.kind}-{key}.ics",
        "content": "\r\n".join(fold(line) for line in lines) + "\r\n",
    }
