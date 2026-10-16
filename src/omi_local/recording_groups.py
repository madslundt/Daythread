"""Compact chronological timeline sessions; original recording evidence stays separate."""

import json
import re
from datetime import datetime

from .domain import stamp
from .service import public_source
from .source_matching import MANUAL_UPLOAD_SQL

GAP_SECONDS = 5 * 60


def group_recordings(rows: list[dict]) -> list[dict]:
    groups, latest, explicit = [], {}, {}
    for row in rows:
        metadata = json.loads(row["source_metadata"] or "{}")
        recording = metadata.get("recording_id")
        kind = row["source_kind"]
        if not kind and row["device_id"] == "web":
            kind = "manual_upload"
        # Quiet-only chunks form compact runs without bridging separate speech sessions.
        stream = (row["device_id"], kind, row["state"] == "done" and not row["has_text"])
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
    return sorted(groups, key=lambda group: (group["started_at"], group["id"]), reverse=True)


def recording_groups(store, start, end, query, limit, offset, group_id=None) -> dict:
    filters, args = ["started_at<?", "ended_at> ?"], [stamp(end), stamp(start)]
    if query:
        filters.append("text LIKE ? ESCAPE '\\'")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args.append(f"%{escaped}%")
    with store.connect() as db:
        db.execute("BEGIN")
        # Read lightweight metadata for grouping; load bodies only for the selected member page.
        rows = [dict(row) for row in db.execute(
            "SELECT id,device_id,started_at,ended_at,state,source_kind,source_metadata,"
            "json_extract(topic_details,'$.title') AS title,"
            "length(trim(coalesce(text,'')))>0 AS has_text,"
            "CASE WHEN " + MANUAL_UPLOAD_SQL + " THEN 'audio:' || digest || ':' || "
            "coalesce(json_extract(source_metadata,'$.part_index'),0) "
            "ELSE 'source:' || id END AS upload_key FROM audio WHERE "
            + " AND ".join(filters) + " ORDER BY started_at,id", args,
        )]
        canonical, alternatives = {}, {}
        for row in rows:
            if row["upload_key"] in canonical:
                alternatives.setdefault(row["upload_key"], []).append(row["id"])
            else:
                canonical[row["upload_key"]] = row
        groups = group_recordings(list(canonical.values()))

        def sources(members):
            ids = [row["id"] for row in members]
            ids.extend(key for row in members for key in alternatives.get(row["upload_key"], []))
            loaded = {row["id"]: dict(row) for row in db.execute(
                "SELECT * FROM audio WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),),
            )}
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
            return {"items": sources(members[offset:offset + limit]), "total": len(members)}
        items = []
        for group in groups[offset:offset + limit]:
            members = group["members"]
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
            items.append({key: group[key] for key in ("id", "started_at", "ended_at")} | {
                "title": title, "recording_count": len(members),
                "estimated_times": any(r["source_kind"] == "omi_audio_webhook" for r in members),
                "sources": sources(members) if len(members) == 1 else [],
            })
        return {"items": items, "total": len(groups), "total_recordings": len(canonical),
                "gap_minutes": GAP_SECONDS // 60}
