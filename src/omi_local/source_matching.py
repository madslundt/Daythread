"""Conservative source relationships; original evidence and retry receipts stay intact."""

import hashlib
import json
import re
import sqlite3
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, model_validator

MATCH_VERSION = "2"
MANUAL_UPLOAD_SQL = "(source_kind='manual_upload' OR (source_kind='' AND device_id='web'))"

SCHEMA = """
CREATE INDEX IF NOT EXISTS audio_duplicate ON audio(digest,started_at,ended_at,state);
CREATE TABLE IF NOT EXISTS source_matches (
 left_id TEXT NOT NULL, right_id TEXT NOT NULL, basis TEXT NOT NULL,
 PRIMARY KEY(left_id,right_id)
);
CREATE TABLE IF NOT EXISTS source_match_checked (id TEXT PRIMARY KEY, version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS upload_parts (
 recording_id TEXT NOT NULL, part_index INTEGER NOT NULL, part_count INTEGER NOT NULL,
 PRIMARY KEY(recording_id,part_index)
);
CREATE TRIGGER IF NOT EXISTS source_matches_delete AFTER DELETE ON audio BEGIN
 DELETE FROM source_matches WHERE left_id=OLD.id OR right_id=OLD.id;
 DELETE FROM source_match_checked WHERE id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS source_matches_change AFTER UPDATE OF text,state ON audio
WHEN OLD.text IS NOT NEW.text OR OLD.state IS NOT NEW.state BEGIN
 DELETE FROM source_matches WHERE left_id=OLD.id OR right_id=OLD.id;
 DELETE FROM source_match_checked WHERE id=OLD.id;
END;
"""


class UploadGrouping(BaseModel):
    recording_id: str | None = None
    part_index: int | None = Field(default=None, ge=0, le=59)
    part_count: int | None = Field(default=None, ge=1, le=60)
    conversation_source_id: str | None = None
    recording_started_at: AwareDatetime | None = None
    recording_ended_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def complete_parts(self):
        parts = (self.recording_id, self.part_index, self.part_count)
        if any(value is not None for value in parts) and (
            any(value is None for value in parts) or self.part_index >= self.part_count
        ):
            raise ValueError("Provide a recording ID and a valid part index and count together")
        if (self.recording_started_at is None) != (self.recording_ended_at is None):
            raise ValueError("Provide both recording bounds")
        if self.recording_started_at is not None and (
            not self.recording_id or self.recording_ended_at <= self.recording_started_at
        ):
            raise ValueError("Recording bounds require a recording ID and an ordered interval")
        return self


def words(text: str) -> list[str]:
    # Imported diarization labels are formatting, not additional spoken evidence.
    text = re.sub(r"(?im)^\s*speaker\s+[\w-]+\s*:\s*", "", text)
    return re.findall(r"\w+", text.casefold(), re.UNICODE)


def same_content(left: str, right: str) -> bool:
    a, b = words(left), words(right)
    if min(len(a), len(b)) < 16:
        return False  # Common short replies cannot establish a conversation identity.
    a = {tuple(a[i:i + 5]) for i in range(len(a) - 4)}
    b = {tuple(b[i:i + 5]) for i in range(len(b) - 4)}
    shared = len(a & b)
    return shared >= 8 and shared / min(len(a), len(b)) >= 0.55


def compatible_time(left: dict, right: dict) -> bool:
    if "omi_audio_webhook" in {left["source_kind"], right["source_kind"]}:
        return False  # Receipt estimates cannot identify capture overlap.
    a, b = (datetime.fromisoformat(left[key]) for key in ("started_at", "ended_at"))
    c, d = (datetime.fromisoformat(right[key]) for key in ("started_at", "ended_at"))
    return (min(b, d) - max(a, c)).total_seconds() >= min(
        (b - a).total_seconds(), (d - c).total_seconds()
    ) * 0.5


def manual_upload(row: dict) -> bool:
    return row.get("source_kind") == "manual_upload" or (
        row.get("source_kind", "") == "" and row.get("device_id") == "web"
    )


def recognition_contract(settings) -> str:
    fields = {key: value for key, value in settings.model_dump(mode="json").items()
              if key.startswith("stt_") and key != "stt_routes"}
    fields["silence_threshold"] = settings.silence_threshold
    fields["routes"] = {
        language: settings.stt_route(language).model_dump(mode="json", exclude={"token"})
        for language in {"auto", *settings.stt_routes}
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class SourceMatchingStore:
    @staticmethod
    def refresh_source_matches(db: sqlite3.Connection, key: str) -> None:
        db.execute("DELETE FROM source_matches WHERE left_id=? OR right_id=?", (key, key))
        row = db.execute("SELECT * FROM audio WHERE id=?", (key,)).fetchone()
        if not row:
            return
        row = dict(row)
        if row["state"] == "done" and row["text"]:
            candidates = db.execute(
                "SELECT * FROM audio WHERE id!=? AND state='done' AND length(trim(text))>0 "
                "AND ((started_at<? AND ended_at>?) OR "
                f"(? AND digest=? AND {MANUAL_UPLOAD_SQL})) ORDER BY started_at,id",
                (key, row["ended_at"], row["started_at"], manual_upload(row), row["digest"]),
            )
            for candidate in candidates:
                other = dict(candidate)
                exact = (
                    row["source_kind"] not in {"omi_api", "omi_audio_webhook"}
                    and other["source_kind"] not in {"omi_api", "omi_audio_webhook"}
                    and row["digest"] == other["digest"]
                    and ((row["started_at"], row["ended_at"]) ==
                         (other["started_at"], other["ended_at"])
                         or (manual_upload(row) and manual_upload(other)))
                )
                if exact or (compatible_time(row, other)
                             and same_content(row["text"], other["text"])):
                    a, b = sorted((key, other["id"]))
                    db.execute("INSERT OR REPLACE INTO source_matches VALUES(?,?,?)",
                               (a, b, "audio" if exact else "transcript"))
        db.execute("INSERT OR REPLACE INTO source_match_checked VALUES(?,?)",
                   (key, f"{MATCH_VERSION}:{row['source_version']}"))

    def reusable_transcription(self, row: dict, contract: str) -> dict | None:
        with self.connect() as db:
            for candidate in db.execute(
                "SELECT * FROM audio WHERE id!=? AND digest=? "
                f"AND ((started_at=? AND ended_at=?) OR (? AND {MANUAL_UPLOAD_SQL})) "
                "AND state='done' AND transcription_outcome IN ('text','empty_stt','silence_gate') "
                "ORDER BY processed_at,id",
                (row["id"], row["digest"], row["started_at"], row["ended_at"], manual_upload(row)),
            ):
                details = json.loads(candidate["transcription_details"])
                if details.get("recognition_contract") == contract:
                    return dict(candidate)
        return None


def merge_groups(groups: list, links: list[dict], received_parts: list[dict]) -> list:
    """Project one conversation from related sources, retaining every original passage."""
    if not groups:
        return groups
    parents = list(range(len(groups)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(a, b):
        parents[find(b)] = find(a)

    source_groups, recordings = {}, {}
    metadata = {}
    for index, group in enumerate(groups):
        for row in group.sources:
            source_groups[row["id"]] = index
            value = json.loads(row.get("source_metadata") or "{}")
            metadata[row["id"]] = value
            recording = value.get("recording_id")
            if recording:
                if recording in recordings:
                    union(index, recordings[recording])
                recordings[recording] = index
    for link in links:
        if link["left_id"] in source_groups and link["right_id"] in source_groups:
            union(source_groups[link["left_id"]], source_groups[link["right_id"]])
    anchors = {}
    for key, value in metadata.items():
        target = value.get("conversation_source_id")
        if target in source_groups:
            union(source_groups[key], source_groups[target])
        if target:
            if target in anchors:
                union(source_groups[key], anchors[target])
            anchors[target] = source_groups[key]
    receipts = {}
    for part in received_parts:
        receipts.setdefault(part["recording_id"], set()).add(part["part_index"])
    components = {}
    for index, group in enumerate(groups):
        components.setdefault(find(index), []).append(group)
    result = []
    for members in components.values():
        # Mutate the first group's projection only; no stored capture metadata changes.
        group = members[0]
        if len(members) > 1:
            group.sources = sorted([r for member in members for r in member.sources],
                                   key=lambda r: (r["started_at"], r["id"]))
            group.__post_init__()
            group.closed_at = (max(member.closed_at for member in members)
                               if all(member.closed_at for member in members) else None)
            group.basis = "combined_sources"
            group.proof = tuple(sorted({p for member in members for p in member.proof}))
        parts = {}
        for row in group.sources:
            value = metadata[row["id"]]
            if value.get("recording_id"):
                parts.setdefault(value["recording_id"], []).append((row, value))
        complete_bounds = set()
        for recording, entries in parts.items():
            value = entries[0][1]
            indices = receipts.get(recording, {v["part_index"] for _, v in entries})
            if indices == set(range(value["part_count"])):
                if value.get("recording_started_at") and value.get("recording_ended_at"):
                    complete_bounds.add(
                        (value["recording_started_at"], value["recording_ended_at"])
                    )
        for recording, entries in parts.items():
            counts = {value["part_count"] for _, value in entries}
            indices = receipts.get(recording, {v["part_index"] for _, v in entries})
            value = entries[0][1]
            replaced = (value.get("recording_started_at"), value.get("recording_ended_at"))
            if ((len(counts) != 1 or indices != set(range(next(iter(counts)))))
                    and replaced not in complete_bounds):
                group.closed_at = None
        result.append(group)
    return sorted(result, key=lambda group: group.start)
