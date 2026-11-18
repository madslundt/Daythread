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
from .session_recordings import ParallelTrackRecording, SessionRecording
from .source_matching import MANUAL_UPLOAD_SQL, manual_upload
from .topic_context import chapter_navigation

GAP_SECONDS = 5 * 60
TRANSCRIPT_TEXT_SQL = "length(trim(coalesce(text,''),?))>0"
TRANSCRIPT_WHITESPACE = (
    " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
PARROT_TRACK = re.compile(r"(system|mic)_(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ)\.caf")
PARROT_CANDIDATE_SQL = (
    "(" + MANUAL_UPLOAD_SQL + " AND (source_id GLOB 'system_*.caf' OR source_id GLOB 'mic_*.caf'))"
)
UPLOAD_KEY_SQL = (
    "CASE WHEN " + MANUAL_UPLOAD_SQL + " THEN 'audio:' || digest || ':' || "
    "coalesce(json_extract(source_metadata,'$.part_index'),0) ELSE 'source:' || id END"
)


def parrot_track(row: dict) -> tuple[str, str] | None:
    if not manual_upload(row):
        return None
    match = PARROT_TRACK.fullmatch(row.get("source_id") or "")
    if match is None:
        return None
    try:
        datetime.strptime(match[2], "%Y-%m-%dT%H-%M-%SZ")
    except ValueError:
        return None
    return match[1], match[2]


def upload_key(row: dict, *, archive: bool = False):
    key = row["upload_key"]
    track = parrot_track(row)
    recording = json.loads(row["source_metadata"] or "{}").get("recording_id")
    if track:
        # Equal PCM (including silence) is not evidence that different tracks or
        # captures are retries. Only deduplicate within the same track and bounds.
        return track, recording, row["started_at"], row["ended_at"], key
    if archive and recording:
        return recording, key
    return key


def group_recordings(
    rows: list[dict], *, archive: bool = False, parrot: bool = False,
) -> list[dict]:
    groups, latest, explicit = [], {}, {}
    for row in rows:
        metadata = json.loads(row["source_metadata"] or "{}")
        recording = metadata.get("recording_id")
        kind = row["source_kind"]
        if not kind and row["device_id"] == "web":
            kind = "manual_upload"
        # Quiet-only chunks form compact runs without bridging separate speech sessions.
        stream = (row["device_id"], kind, row["state"] == "done" and not row["has_text"])
        if parrot and parrot_track(row):
            stream += (row["id"],)
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
    if parrot:
        pair_parrot_tracks(groups)
    return sorted(groups, key=lambda group: (group["started_at"], group["id"]), reverse=not archive)


def pair_parrot_tracks(groups: list[dict]) -> None:
    candidates = {}
    for group in groups:
        rows = group["members"]
        track = parrot_track(rows[0])
        if track and all(parrot_track(row) == track for row in rows[1:]):
            role, captured = track
            candidates.setdefault(captured, {}).setdefault(role, []).append(group)

    paired = set()
    for roles in candidates.values():
        system, mic = roles.get("system", []), roles.get("mic", [])
        if len(system) != 1 or len(mic) != 1:
            continue  # Ambiguous matching should not merge separate imports.
        first, second = sorted([system[0], mic[0]], key=lambda group: (
            group["started_at"], group["id"],
        ))
        if not tracks_overlap(first["members"], second["members"]):
            continue
        first.update(ended_at=max(first["ended_at"], second["ended_at"]),
                     members=sorted(first["members"] + second["members"],
                                    key=lambda row: (row["started_at"], row["id"])),
                     parallel_tracks=True)
        paired.add(second["id"])
    groups[:] = [group for group in groups if group["id"] not in paired]


def tracks_overlap(left: list[dict], right: list[dict]) -> bool:
    # Track parts are in capture order. A missing part does not establish overlap.
    if any(a["ended_at"] > b["started_at"] for rows in (left, right)
           for a, b in zip(rows, rows[1:])):
        return False
    first = second = 0
    while first < len(left) and second < len(right):
        a, b = left[first], right[second]
        if max(a["started_at"], b["started_at"]) < min(a["ended_at"], b["ended_at"]):
            return True
        if a["ended_at"] <= b["ended_at"]:
            first += 1
        else:
            second += 1
    return False


def recording_groups(
    store, start, end, query, limit, offset, group_id=None, zone_id=None, tag_id=None,
    playback=False, archive=False, speaker_evidence=None,
):
    filters, args = ["1=1"], []
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
            "WITH scoped AS ("
            "SELECT id,device_id,started_at,ended_at,state,source_kind,source_id,source_metadata,"
            "transcript_version,topic_details,audio_available,"
            "json_extract(topic_details,'$.title') AS title,"
            + TRANSCRIPT_TEXT_SQL + " AS has_text," + UPLOAD_KEY_SQL + " AS upload_key, ("
            + " AND ".join(filters) + ") AS matched FROM audio WHERE started_at<? AND ended_at>?) "
            "SELECT * FROM scoped WHERE matched OR " + PARROT_CANDIDATE_SQL
            + " ORDER BY started_at,id", [TRANSCRIPT_WHITESPACE, *args, stamp(end), stamp(start)],
        )]
        narrowed = bool(query or zone_id or tag_id)
        # Include out-of-scope Parrot metadata to check that a match is unambiguous,
        # but use in-scope copies first and never expose filtered-out sources in a search.
        rows = [row for row in rows if row["matched"] or parrot_track(row)]
        if archive or any(not row["matched"] for row in rows):
            rows.sort(key=lambda row: (not row["matched"],
                                      not row["has_text"] or row["state"] != "done",
                                      not row["audio_available"]))
        decorate_tags(db, rows)
        canonical, alternatives, upload_tags = {}, {}, {}
        for row in rows:
            key = row["upload_key"] = upload_key(row, archive=archive)
            tags = upload_tags.setdefault(key, {})
            if row["matched"] or not narrowed:
                tags.update({tag["id"]: tag for tag in row["tags"]})
            if key in canonical:
                if row["matched"] or not narrowed:
                    alternatives.setdefault(key, []).append(row["id"])
            else:
                canonical[key] = row
        ordered = sorted(canonical.values(), key=lambda row: (row["started_at"], row["id"]))
        groups = []
        for group in group_recordings(ordered, archive=archive, parrot=True):
            members = [row for row in group["members"] if row["matched"]]
            if not members:
                continue
            complete_scope = len(members) == len(group["members"])
            if complete_scope or (group.get("parallel_tracks") and not narrowed):
                groups.append(group)
            else:
                # Only a confirmed pair can stay paired in a narrower scope.
                groups.extend(group_recordings(members, archive=archive,
                                                parrot=bool(group.get("parallel_tracks"))))
        groups.sort(key=lambda group: (group["started_at"], group["id"]), reverse=not archive)

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
            recording = (ParallelTrackRecording if group.get("parallel_tracks")
                         else SessionRecording)(group_id, rows)
            if playback:
                return recording
            return {"items": sources(members[offset:offset + limit]), "total": len(members),
                    "chapters": [] if group.get("parallel_tracks") else chapter_navigation(members),
                    "recording": recording.snapshot(
                        attributed=speaker_evidence(rows, db) if speaker_evidence else None,
                    )}
        items = []
        for group in groups[offset:offset + limit]:
            members = group["members"]
            recording_metadata = json.loads(members[0]["source_metadata"] or "{}")
            parallel = group.get("parallel_tracks", False)
            recording_id = None if parallel else recording_metadata.get("recording_id")
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
                "title": title, "recording_count": 1 if recording_id or parallel else len(members),
                **({"recording_id": recording_id} if recording_id else {}),
                **({"parallel_tracks": True} if parallel else {}),
                **archive_state,
                "topic_count": 0 if parallel else len({" ".join(chapter["title"].casefold().split())
                                                       for chapter in chapter_navigation(members)}),
                "tags": list({
                    tag["id"]: tag for row in members
                    for tag in upload_tags[row["upload_key"]].values()
                }.values()),
                "estimated_times": any(r["source_kind"] == "omi_audio_webhook" for r in members),
                "sources": sources(members) if len(members) == 1 and not recording_id else [],
            })
        recording_keys = {
            group["id"] if group.get("parallel_tracks") else
            json.loads(row["source_metadata"] or "{}").get("recording_id") or row["id"]
            for group in groups for row in group["members"]
        }
        return {"items": items, "total": len(groups), "total_recordings": len(recording_keys),
                "gap_minutes": GAP_SECONDS // 60}
