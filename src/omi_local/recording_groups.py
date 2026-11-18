"""Compact chronological timeline sessions; original recording evidence stays separate."""

import json
import re
from datetime import datetime

from .conversation_state import finished_file
from .conversation_tags import decorate_tags, tag_filter
from .domain import stamp
from .location_zones import zone_filter
from .recording_locations import decorate_locations
from .service import public_source
from .session_recordings import SessionRecording
from .source_matching import MANUAL_UPLOAD_SQL
from .topic_context import chapter_navigation

GAP_SECONDS = 5 * 60
TRANSCRIPT_TEXT_SQL = "length(trim(coalesce(text,''),?))>0"
TRANSCRIPT_WHITESPACE = (
    " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def group_recordings(rows: list[dict], *, archive: bool = False) -> list[dict]:
    groups, latest, explicit = [], {}, {}
    for row in rows:
        metadata = json.loads(row["source_metadata"] or "{}")
        recording = metadata.get("recording_id")
        kind = row["source_kind"]
        if not kind and row["device_id"] == "web":
            kind = "manual_upload"
        # Quiet-only chunks form compact runs without bridging separate speech sessions.
        stream = (row["device_id"], kind, row["state"] == "done" and not row["has_text"])
        if archive:
            file_id = row["id"] if finished_file(row) else None
            stream += (row["state"], row["audio_available"], file_id)
        previous = explicit.get(recording) if recording else latest.get(stream)
        if previous and not recording:
            gap = (datetime.fromisoformat(row["started_at"])
                   - datetime.fromisoformat(previous["ended_at"])).total_seconds()
            if gap > GAP_SECONDS:
                # These are visual proximity groups, never proof of conversation continuity.
                previous = None
        if previous is None:
            previous = {"id": row["id"], "started_at": row["started_at"],
                        "ended_at": row["ended_at"], "members": []}
            groups.append(previous)
        previous["members"].append(row)
        previous["ended_at"] = max(previous["ended_at"], row["ended_at"])
        if recording:
            explicit[recording] = previous
        else:
            latest[stream] = previous
    return sorted(groups, key=lambda group: (group["started_at"], group["id"]), reverse=not archive)


def recording_groups(
    store, start, end, query, limit, offset, group_id=None, zone_id=None, tag_id=None,
    playback=False, archive=False,
):
    filters, args = ["started_at<?", "ended_at>?"], [stamp(end), stamp(start)]
    if not archive or query:
        filters.extend(["state='done'", TRANSCRIPT_TEXT_SQL])
        args.append(TRANSCRIPT_WHITESPACE)
    if query:
        filters.append("text LIKE ? ESCAPE '\\'")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args.append(f"%{escaped}%")
    with store.connect() as db:
        db.execute("BEGIN")
        zone_sql, zone_args = zone_filter(db, zone_id)
        filters.append(zone_sql)
        args.extend(zone_args)
        tag_sql, tag_args = tag_filter(db, tag_id)
        filters.append(tag_sql)
        args.extend(tag_args)
        # Read lightweight metadata for grouping; load bodies only for the selected member page.
        rows = [dict(row) for row in db.execute(
            "SELECT id,device_id,started_at,ended_at,state,source_kind,source_metadata,"
            "transcript_version,topic_details,audio_available,"
            "json_extract(topic_details,'$.title') AS title,"
            + TRANSCRIPT_TEXT_SQL + " AS has_text,"
            "CASE WHEN " + MANUAL_UPLOAD_SQL + " THEN 'audio:' || digest || ':' || "
            "coalesce(json_extract(source_metadata,'$.part_index'),0) "
            "ELSE 'source:' || id END AS upload_key FROM audio WHERE "
            + " AND ".join(filters) + " ORDER BY started_at,id", [TRANSCRIPT_WHITESPACE, *args],
        )]
        if archive:
            # Prefer a usable copy over an otherwise identical expired upload.
            rows.sort(key=lambda row: (not row["has_text"] or row["state"] != "done",
                                      not row["audio_available"]))
        decorate_tags(db, rows)
        canonical, alternatives, upload_tags = {}, {}, {}
        for row in rows:
            if archive:
                # Identical parts in distinct files do not make those files interchangeable.
                recording = json.loads(row["source_metadata"] or "{}").get("recording_id")
                if recording:
                    row["upload_key"] = f'{recording}:{row["upload_key"]}'
            upload_tags.setdefault(row["upload_key"], {}).update({
                tag["id"]: tag for tag in row["tags"]
            })
            if row["upload_key"] in canonical:
                alternatives.setdefault(row["upload_key"], []).append(row["id"])
            else:
                canonical[row["upload_key"]] = row
        ordered = sorted(canonical.values(), key=lambda row: (row["started_at"], row["id"]))
        groups = group_recordings(ordered, archive=archive)

        def sources(members):
            ids = [row["id"] for row in members]
            ids.extend(key for row in members for key in alternatives.get(row["upload_key"], []))
            loaded = {row["id"]: dict(row) for row in db.execute(
                "SELECT * FROM audio WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),),
            )}
            decorate_tags(db, list(loaded.values()))
            decorate_locations(db, list(loaded.values()))
            result = []
            for row in members:
                source = loaded[row["id"]]
                source["alternate_sources"] = [
                    loaded[key] for key in alternatives.get(row["upload_key"], [])
                ]
                result.append(public_source(source))
            return result

        if group_id is not None:
            group = next((group for group in groups if group["id"] == group_id), None)
            if group is None:
                raise KeyError("Recording group unavailable")
            members = group["members"]
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM audio WHERE id IN (SELECT value FROM json_each(?)) "
                "ORDER BY started_at,id", (json.dumps([row["id"] for row in members]),),
            )]
            recording = SessionRecording(group_id, rows)
            if playback:
                return recording
            return {"items": sources(members[offset:offset + limit]), "total": len(members),
                    "chapters": chapter_navigation(members), "recording": recording.snapshot()}
        items = []
        for group in groups[offset:offset + limit]:
            members = group["members"]
            recording_metadata = json.loads(members[0]["source_metadata"] or "{}")
            recording_id = recording_metadata.get("recording_id")
            titles, seen = [], set()
            for row in members:
                title = row["title"] or ""
                key = " ".join(re.findall(r"\w+", title.casefold()))
                if key and key not in seen:
                    titles.append(title)
                    seen.add(key)
            title = " · ".join(titles[:2]) or "Recording session"
            if len(titles) > 2:
                title += " …"
            bounds = {key: group[key] for key in ("id", "started_at", "ended_at")}
            if recording_id and recording_metadata.get("recording_started_at"):
                bounds.update(started_at=recording_metadata["recording_started_at"],
                              ended_at=recording_metadata["recording_ended_at"])
            archive_state = {
                "states": sorted({row["state"] for row in members}),
                "has_transcript": any(r["state"] == "done" and r["has_text"] for r in members),
                "has_audio": any(row["audio_available"] for row in members),
            } if archive else {}
            items.append(bounds | {
                "title": title, "recording_count": 1 if recording_id else len(members),
                **({"recording_id": recording_id} if recording_id else {}),
                **archive_state,
                "topic_count": len({" ".join(chapter["title"].casefold().split())
                                    for chapter in chapter_navigation(members)}),
                "tags": list({
                    tag["id"]: tag for row in members
                    for tag in upload_tags[row["upload_key"]].values()
                }.values()),
                "estimated_times": any(r["source_kind"] == "omi_audio_webhook" for r in members),
                "sources": sources(members) if len(members) == 1 and not recording_id else [],
            })
        recording_keys = {json.loads(row["source_metadata"] or "{}").get("recording_id")
                          or row["id"] for row in canonical.values()}
        return {"items": items, "total": len(groups), "total_recordings": len(recording_keys),
                "gap_minutes": GAP_SECONDS // 60}
