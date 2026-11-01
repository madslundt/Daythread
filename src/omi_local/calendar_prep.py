"""Bounded, ephemeral calendar-file intake for user-confirmed preparation.

Only concrete VEVENT instances are read. Recurrence rules are not expanded;
calendar metadata never becomes recorded evidence or verifies attendance.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

MAX_BYTES = 2 * 1024 * 1024


class CalendarImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=MAX_BYTES)


def text(value: str, limit: int) -> str:
    value = re.sub(r"\\([nN,;\\])", lambda match: "\n" if match[1] in "nN" else match[1], value)
    return " ".join(value.split())[:limit]


def property_line(line: str):
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        if char == ":" and not quoted:
            parts = re.split(r';(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', line[:i])
            params = {}
            for part in parts[1:]:
                key, _, value = part.partition("=")
                params[key.upper()] = value.strip('"')
            return parts[0].upper(), params, line[i + 1:]
    raise ValueError("Invalid calendar property")


def event_time(value: str, params: dict, timezone: str):
    all_day = params.get("VALUE", "").upper() == "DATE" or len(value) == 8
    if all_day:
        parsed = datetime.strptime(value, "%Y%m%d")
    elif value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=ZoneInfo("UTC")
        ), False
    else:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    zone = ZoneInfo(params.get("TZID", timezone))
    aware = parsed.replace(tzinfo=zone)
    # A floating time in the missing DST hour has no concrete instant.
    if aware.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None) != parsed:
        raise ValueError("Calendar time falls in a daylight-saving gap")
    if aware.replace(fold=1).utcoffset() != aware.utcoffset():
        raise ValueError("Ambiguous daylight-saving time; export an explicit UTC instance")
    return aware, all_day


def parse_calendar(content: str, timezone: str) -> dict:
    if len(content.encode()) > MAX_BYTES:
        raise ValueError("Calendar file exceeds 2 MiB")
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = re.sub(r"\n[ \t]", "", content).splitlines()
    if not lines or lines[0].upper() != "BEGIN:VCALENDAR" or lines[-1].upper() != "END:VCALENDAR":
        raise ValueError("Choose an iCalendar .ics file")
    events, raw, depth = [], None, 0
    skipped, recurring = 0, 0
    count = 0
    for line in lines[1:-1]:
        if line.upper() == "BEGIN:VEVENT":
            if raw is not None:
                raise ValueError("Nested calendar event")
            raw, depth = [], 0
            count += 1
            if count > 1000:
                raise ValueError("Export at most 1,000 calendar events at a time")
        elif line.upper() == "END:VEVENT":
            if raw is None or depth:
                raise ValueError("Incomplete calendar event")
            try:
                event = parse_event(raw, timezone)
                if event is not None:
                    events.append(event)
                    recurring += int(event["recurrence_not_expanded"])
                else:
                    skipped += 1
            except (ValueError, ZoneInfoNotFoundError):
                skipped += 1
            raw = None
        elif raw is not None:
            if line.upper().startswith("BEGIN:"):
                depth += 1
            elif line.upper().startswith("END:"):
                depth -= 1
                if depth < 0:
                    raise ValueError("Invalid calendar component")
            elif depth == 0:
                raw.append(property_line(line))
    if raw is not None:
        raise ValueError("Incomplete calendar event")
    events.sort(key=lambda event: event["started_at"])
    return {
        "events": events[:100], "total": len(events), "omitted": max(0, len(events) - 100),
        "skipped": skipped, "recurring": recurring, "timezone": timezone,
    }


def parse_event(properties: list, timezone: str):
    values = {key: (params, value) for key, params, value in properties}
    if values.get("STATUS", ({}, ""))[1].upper() == "CANCELLED":
        return None
    start_params, start_raw = values.get("DTSTART", ({}, ""))
    start, all_day = event_time(start_raw, start_params, timezone)
    end = None
    if "DTEND" in values:
        params, value = values["DTEND"]
        end, _ = event_time(value, params, timezone)
        if end <= start:
            raise ValueError("Event must end after it starts")
    title = text(values.get("SUMMARY", ({}, ""))[1], 200)
    if not title:
        raise ValueError("Event has no title")
    people = []
    for key, params, value in properties:
        if key != "ATTENDEE" or params.get("PARTSTAT", "").upper() == "DECLINED":
            continue
        name = text(params.get("CN", ""), 120)
        if name and name not in people:
            people.append(name)
    return {
        "id": text(values.get("UID", ({}, start.isoformat() + title))[1], 255),
        "title": title, "started_at": start.isoformat(),
        "ended_at": end.isoformat() if end else None, "all_day": all_day,
        "attendee_candidates": people[:30],
        "recurrence_not_expanded": "RRULE" in values and "RECURRENCE-ID" not in values,
    }


def calendar_prep_routes(get_memory):
    router = APIRouter(prefix="/v1/conversation-prep/calendar")

    @router.post("/import")
    def import_file(body: CalendarImport):
        from fastapi import HTTPException

        try:
            return parse_calendar(body.content, get_memory().settings.timezone)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
