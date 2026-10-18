"""Pause-based live conversations and explicit boundaries for finished uploads."""

import json
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .domain import project_events, stamp
from .source_matching import manual_upload, merge_groups
from .vendor_imports import transcript_import
from .voice_activity import VERSION


@dataclass
class Conversation:
    sources: list[dict]
    closed_at: str | None = None
    basis: str = "pending"
    proof: tuple = ()
    hints: dict = field(default_factory=dict)
    _end: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._end = max((row["ended_at"] for row in self.sources), default="")

    def append(self, row: dict) -> None:
        self.sources.append(row)
        self._end = max(self._end, row["ended_at"])

    @property
    def start(self) -> str:
        return self.sources[0]["started_at"]

    @property
    def end(self) -> str:
        return self._end

    @property
    def ready(self) -> bool:
        return bool(self.closed_at) and all(row["state"] == "done" for row in self.sources)


def activity(row: dict) -> dict:
    stored = row.get("recap_activity")
    result = json.loads(stored) if stored else json.loads(
        row.get("transcription_details") or "{}"
    ).get("voice_activity", {})
    return result if isinstance(result, dict) and result.get("version") == VERSION else {}


def quiet_runs(quiet: list, blockers: list) -> list:
    """Union observed quiet, then subtract every uncertain/voiced overlapping interval."""
    merged = []
    for start, end, proof in sorted(quiet):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
            merged[-1][2].append((start, end, proof))
        else:
            merged.append([start, end, [(start, end, proof)]])
    blocked = []
    for left, right in sorted(blockers):
        if blocked and left <= blocked[-1][1]:
            blocked[-1][1] = max(right, blocked[-1][1])
        else:
            blocked.append([left, right])
    result, index = [], 0
    for start, end, proof in merged:
        cursor = start
        while index < len(blocked) and blocked[index][1] <= start:
            index += 1
        scan = index
        while scan < len(blocked) and blocked[scan][0] < end:
            left, right = blocked[scan]
            if left > cursor:
                result.append((cursor, left, proof))
            cursor = max(cursor, right)
            scan += 1
        if cursor < end:
            result.append((cursor, end, proof))
    return result


def conversations(
    snapshot: dict, now: datetime, quiet_seconds: int, *, respect_open_meeting: bool = True,
) -> list[Conversation]:
    settled = stamp(now - timedelta(seconds=30))
    streams: dict[str, list[dict]] = {}
    result = []
    timeline = project_events(snapshot["events"], now)
    active = timeline["active_meeting"]
    active_device = next((event["device_id"] for event in snapshot["events"]
                          if active and event["event_id"] == active["id"]), None)
    for row in snapshot["rows"]:
        if row["started_at"] > stamp(now) or row["state"] == "expired":
            continue
        if manual_upload(row) or transcript_import(row.get("source_kind", "")):
            if (row["text"] or row["state"] != "done"
                    or json.loads(row.get("source_metadata") or "{}").get("recording_id")):
                result.append(Conversation(
                    [row], row["ended_at"] if row["ended_at"] <= settled else None,
                    "finished_import" if transcript_import(row["source_kind"]) else "uploaded_file",
                ))
            continue
        streams.setdefault(row["device_id"], []).append(row)
    for device, rows in streams.items():
        quiet, blockers, candidates = [], [], []
        for row in rows:
            if row.get("source_kind") == "omi_audio_webhook":
                # Receipt estimates cannot establish capture ordering or
                # continuous quiet across delayed/reordered webhook payloads.
                blockers.append((row["started_at"], row["ended_at"]))
                if row["text"] or row["state"] != "done":
                    candidates.append(row)
                continue
            value = activity(row)
            if (row["state"] != "done" or row["text"] or
                    value.get("status") != "observed"):
                blockers.append((row["started_at"], row["ended_at"]))
                candidates.append(row)
                continue
            base = datetime.fromisoformat(row["started_at"])
            for start, end in value.get("non_speech", []):
                quiet.append((stamp(base + timedelta(seconds=start)),
                              stamp(base + timedelta(seconds=end)),
                              (row["id"], row["source_version"])))
            for start, end in value.get("speech", []):
                blockers.append((stamp(base + timedelta(seconds=start)),
                                 stamp(base + timedelta(seconds=end))))
            if value.get("speech"):
                candidates.append(row)
        blockers.extend((gap["started_at"], gap["ended_at"]) for gap in snapshot["gaps"]
                        if gap["device_id"] in {device, ""})
        runs = [(start, end, proof,
                 stamp(datetime.fromisoformat(start) + timedelta(seconds=quiet_seconds)))
                for start, end, proof in quiet_runs(quiet, blockers)
                if (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
                >= quiet_seconds]
        starts = [run[0] for run in runs]
        current = None

        def close(group: Conversation, before: str) -> None:
            # Speaker grouping keeps a manually open meeting together across long pauses.
            if (respect_open_meeting and active and device == active_device
                    and group.end > active["started_at"]):
                return
            index = bisect_left(starts, group.end)
            if index < len(runs):
                start, end, proof, finish = runs[index]
                if finish <= min(end, before, settled):
                    group.closed_at, group.basis = finish, "observed_non_speech"
                    group.proof = tuple(sorted({token for a, b, token in proof
                                                if a < finish and b > start}))

        for row in candidates:
            if current:
                close(current, row["started_at"])
                if current.closed_at:
                    result.append(current)
                    current = None
            if current is None:
                current = Conversation([])
            current.append(row)
        if current:
            close(current, settled)
            result.append(current)
    result = merge_groups(
        result, snapshot.get("source_matches", []), snapshot.get("upload_parts", [])
    )
    for group in result:
        group.hints = {
            kind: [{**hint, "context_end": min(hint["context_end"], group.end)}
                   for hint in timeline[kind]
                   if hint["context_start"] < group.end and hint["context_end"] > group.start]
            for kind in ("markers", "meetings")
        }
    return sorted(result, key=lambda group: group.start)


def finished_file(row: dict) -> bool:
    return manual_upload(row) or transcript_import(row.get("source_kind", ""))


def recordings(snapshot: dict, now: datetime, quiet_seconds: int) -> list[Conversation]:
    timestamp = stamp(now)
    result = []
    timeline = project_events(snapshot["events"], now)
    for row in snapshot["rows"]:
        if not finished_file(row):
            continue
        if row["started_at"] > timestamp or row["state"] == "expired":
            continue
        # Keep silent multipart entries so the file can finish, but standalone
        # silence adds no evidence and must not invalidate a useful recap.
        if (row["state"] == "done" and not row["text"]
                and not json.loads(row.get("source_metadata") or "{}").get("recording_id")):
            continue
        result.append(Conversation(
            [row], row["ended_at"] if row["ended_at"] <= timestamp else None,
            "finished_import" if transcript_import(row["source_kind"]) else
            "uploaded_file",
        ))
    result = merge_groups(
        result, snapshot.get("source_matches", []), snapshot.get("upload_parts", [])
    )
    live = conversations(
        snapshot | {"rows": [row for row in snapshot["rows"] if not finished_file(row)]},
        now, quiet_seconds, respect_open_meeting=False,
    )
    all_groups = result + live
    # An explicitly finished file need not wait for its ongoing live alternative.
    # Merge matching evidence only once both sides are eligible for the recap.
    result = merge_groups(
        [group for group in all_groups if group.ready], snapshot.get("source_matches", []),
        snapshot.get("upload_parts", []),
    ) + [group for group in all_groups if not group.ready]
    for group in result:
        group.hints = {
            kind: [{**hint, "context_end": min(hint["context_end"], group.end)}
                   for hint in timeline[kind]
                   if hint["context_start"] < group.end and hint["context_end"] > group.start]
            for kind in ("markers", "meetings")
        }
    return sorted(result, key=lambda group: group.start)
