"""Deterministic Markdown from SQLite, including portable decision provenance."""

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def day_bounds(day: str, zone: str) -> tuple[str, str]:
    calendar_day = date.fromisoformat(day)
    start = datetime.combine(calendar_day, time.min, ZoneInfo(zone))
    try:
        utc_start = start.astimezone(UTC)
    except OverflowError:
        utc_start = datetime.min.replace(tzinfo=UTC)
    try:
        utc_end = (start + timedelta(days=1)).astimezone(UTC)
    except OverflowError:
        utc_end = datetime.max.replace(tzinfo=UTC)
    return utc_start.isoformat(timespec="microseconds"), utc_end.isoformat(timespec="microseconds")


def decision_markdown(db: sqlite3.Connection, day: str, zone: str) -> str:
    start, end = day_bounds(day, zone)
    rows = db.execute(
        "SELECT * FROM decisions WHERE evidence_condition='current' "
        "AND review_status!='dismissed' AND started_at<? AND ended_at>? "
        "ORDER BY started_at,id",
        (end, start),
    ).fetchall()
    lines = [
        "\n\n## Decision register\n",
        "Owner review records accuracy of this interpretation. Capture times have chunk precision.",
    ]
    if not rows:
        lines.append("\nNo current decision records for this day.")
    for row in rows:
        content = json.loads(row["content"])
        state = "revised" if row["successor_id"] else row["state"]
        lines.extend(
            [
                f"\n### {content['choice']['text']}",
                f"\nID: {row['id']} · {state} (original: {row['state']}) · {row['review_status']}",
                f"\nCaptured: {row['started_at']} – {row['ended_at']}",
            ]
        )
        if row["predecessor_id"]:
            lines.append(f"\nRevises: {row['predecessor_id']}")
        if row["successor_id"]:
            lines.append(f"\nRevised by: {row['successor_id']}")
        claims = [
            ("Choice", content["choice"]),
            ("Context", content.get("context")),
            ("Rationale", content.get("rationale")),
        ]
        claims.extend(
            (f"Explicit alternative {i + 1}", claim)
            for i, claim in enumerate(content["alternatives"])
        )
        claims.extend(
            (f"Contradictory evidence for {claim.get('field', 'choice')} {i + 1}", claim)
            for i, claim in enumerate(content.get("contradictions", []))
        )
        for label, claim in claims:
            lines.append(f"\n{label}: {claim['text'] if claim else 'Not stated.'}")
            for anchor in claim["evidence"] if claim else []:
                lines.append(f"\n> {anchor['quote'].replace(chr(10), chr(10) + '> ')}")
                lines.append(
                    f"\nSource: [{anchor['source_id']}] version {anchor['source_version']} "
                    f"characters {anchor['start']}–{anchor['end']} (Unicode code points)"
                )
        if content.get("owner_notes"):
            entered = row["updated_at"]
            for event in db.execute(
                "SELECT content,created_at FROM decision_events "
                "WHERE decision_id=? ORDER BY version",
                (row["id"],),
            ):
                if (
                    event["content"]
                    and json.loads(event["content"]).get("owner_notes") == content["owner_notes"]
                ):
                    entered = event["created_at"]
                    break
            lines.append(f"\nOwner note (entered {entered}): {content['owner_notes']}")
        for relationship in content.get("relationships", []):
            lines.append(
                f"\nOwner link ({relationship['kind']}): {relationship['label']}"
                + (f" [{relationship['target_id']}]" if relationship.get("target_id") else "")
            )
    return "\n".join(lines)


def output_provenance_markdown(content: dict) -> str:
    evidence = content.get("evidence")
    if not evidence:
        return ""
    return (
        f"Output: {content.get('output_id', 'unknown')} · "
        f"generated {evidence['generated_at']}\n\n"
        f"Evidence: {len(evidence['selected'])} selected / "
        f"{evidence['eligible_transcripts']} eligible transcript sources; "
        f"{evidence['pending_chunks']} pending and "
        f"{evidence['blocked_chunks']} blocked at selection. "
        "Selection and citations do not verify accuracy.\n\n"
    )


def diary_markdown(db: sqlite3.Connection, day: str, content: dict) -> str:
    coverage = ""
    if "included_through" in content:
        coverage = (
            f"Transcribed recordings through: {content.get('included_through') or 'none'}. "
            f"Pending recordings: {content.get('deferred_conversations', 0)}.\n\n"
            f"{content.get('boundary_note', '')}\n\n"
        )
    return (
        f"# {day}\n\n{content.get('summary') or '_No generated recap._'}\n\n"
        + coverage
        + output_provenance_markdown(content)
        + "## Transcript sources\n\n"
        + "\n".join(f"- {r['started_at']} [{r['id']}] {r['text']}" for r in content["sources"])
        + decision_markdown(db, day, content.get("timezone", "UTC"))
        + "\n"
    )


def export_days(db: sqlite3.Connection) -> list[tuple[str, dict]]:
    diaries = {
        r["day"]: json.loads(r["content"]) for r in db.execute("SELECT day,content FROM diary")
    }
    setting = db.execute("SELECT value FROM runtime_settings WHERE key='timezone'").fetchone()
    zone = json.loads(setting["value"]) if setting else "UTC"
    metadata = db.execute("SELECT value FROM decision_metadata WHERE key='timezone'").fetchone()
    if metadata:
        zone = metadata["value"]
    for row in db.execute(
        "SELECT started_at,ended_at FROM decisions WHERE evidence_condition='current' "
        "AND review_status!='dismissed'"
    ):
        start = datetime.fromisoformat(row["started_at"])
        end = datetime.fromisoformat(row["ended_at"])
        day = start.astimezone(ZoneInfo(zone)).date()
        # Endpoints are exclusive. Subtract before timezone conversion to respect DST folds.
        last_day = (end - timedelta(microseconds=1)).astimezone(ZoneInfo(zone)).date()
        while day <= last_day:
            diaries.setdefault(day.isoformat(), {"summary": None, "sources": [], "timezone": zone})
            if day == date.max:
                break
            day += timedelta(days=1)
    return sorted(diaries.items())
