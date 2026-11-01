"""Logical recording context and chapter navigation across physical audio parts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property

from .conversation_state import finished_file
from .domain import utc_now

MAX_LIVE_WINDOW = timedelta(minutes=15)


@dataclass
class TopicContext:
    members: list[dict]
    eligible: bool = True

    @cached_property
    def key(self) -> str:
        fields = ("id", "source_version", "transcript_version", "state", "started_at",
                  "ended_at", "source_metadata")
        identity = [[row[key] for key in fields] for row in self.members]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @property
    def ids(self) -> list[str]:
        return [row["id"] for row in self.members]

    @property
    def ready(self) -> bool:
        if not self.eligible or any(row["state"] != "done" for row in self.members):
            return False
        metadata = [json.loads(row["source_metadata"] or "{}") for row in self.members]
        if metadata[0].get("recording_id"):
            expected = metadata[0]["part_count"]
            return (len(metadata) == expected
                    and {part["part_index"] for part in metadata} == set(range(expected)))
        return True

    def pending(self, fingerprint: str) -> bool:
        return any(row["has_text"] and (
            row["topics"].get("fingerprint") != fingerprint
            or row["topics"].get("context") != self.key
            or row["topics"].get("retry_at")
        ) for row in self.members)

    def due(self, fingerprint: str, now: str) -> bool:
        return self.ready and any(row["has_text"] and (
            row["topics"].get("fingerprint") != fingerprint
            or row["topics"].get("context") != self.key
            or (row["topics"].get("retry_at") and row["topics"]["retry_at"] <= now)
        ) for row in self.members)


def live_contexts(rows: list[dict], now: datetime) -> list[TopicContext]:
    """Stable work windows, closed by a speech-free upload or a 15-minute checkpoint.

    An empty, completed transcript means no intelligible speech was registered;
    it need not be acoustically silent. Missing/pending uploads are not a pause.
    These processing boundaries do not close a conversation or meeting.
    """
    streams = {}
    for row in rows:
        streams.setdefault((row["device_id"], row["source_kind"]), []).append(row)
    result = []
    for stream in streams.values():
        next_speech, following = [None] * len(stream), None
        for index in range(len(stream) - 1, -1, -1):
            next_speech[index] = following
            if stream[index]["state"] != "done" or stream[index]["has_text"]:
                following = datetime.fromisoformat(stream[index]["started_at"])
        current, end, deadline = [], None, None
        for index, row in enumerate(stream):
            start, finish = (datetime.fromisoformat(row[key])
                             for key in ("started_at", "ended_at"))
            if current and (start - end > timedelta(minutes=5) or finish > deadline):
                result.append(TopicContext(current, eligible=now >= deadline))
                current = []
            if row["state"] == "done" and not row["has_text"]:
                # A gap or a receipt-timed webhook cannot prove a speech break.
                if (current and start == end and finish <= now
                        and (next_speech[index] is None or next_speech[index] >= finish)
                        and row["source_kind"] != "omi_audio_webhook"):
                    result.append(TopicContext([*current, row]))
                    current = []
                continue
            if not current:
                deadline = start + MAX_LIVE_WINDOW
                end = finish
            else:
                end = max(end, finish)
            current.append(row)
        if current:
            result.append(TopicContext(current, eligible=now >= max(deadline, end)))
    return result


def topic_contexts(db, now: datetime | None = None) -> list[TopicContext]:
    # Import at call time: timeline presentation itself imports public_source.
    from .recording_groups import group_recordings

    rows = [dict(row) for row in db.execute(
        "SELECT id,device_id,started_at,ended_at,state,source_kind,source_metadata,"
        "source_version,transcript_version,"
        "json_extract(topic_details,'$.fingerprint') AS topic_fingerprint,"
        "json_extract(topic_details,'$.context') AS topic_context,"
        "json_extract(topic_details,'$.retry_at') AS topic_retry,"
        "length(trim(coalesce(text,'')))>0 AS has_text FROM audio "
        "WHERE state!='expired' ORDER BY started_at,id"
    )]
    streams, files, standalone = [], [], []
    for row in rows:
        row["topics"] = {"fingerprint": row.pop("topic_fingerprint"),
                         "context": row.pop("topic_context"), "retry_at": row.pop("topic_retry")}
        metadata = json.loads(row["source_metadata"] or "{}")
        if metadata.get("recording_id"):
            files.append(row)
        elif finished_file(row):
            standalone.append(TopicContext([row]))
        else:
            streams.append(row)
    contexts = (standalone + [TopicContext(group["members"]) for group in group_recordings(files)]
                + live_contexts(streams, now or utc_now()))
    for context in contexts:
        if json.loads(context.members[0]["source_metadata"] or "{}").get("recording_id"):
            context.members.sort(key=lambda row: json.loads(row["source_metadata"])["part_index"])
    return sorted(contexts, key=lambda context: context.members[0]["started_at"])


def chapter_navigation(members: list[dict]) -> list[dict]:
    """Only consecutive pieces of the same analyzed chapter are joined."""
    chapters = []
    for source_index, row in enumerate(members):
        details = json.loads(row.get("topic_details") or "{}")
        for section in details.get("sections", []):
            chapter_id = section.get("chapter_id", f'{row["id"]}:{section["start_offset"]}')
            part = {"source_id": row["id"], "source_index": source_index,
                    "transcript_version": row["transcript_version"],
                    "start_offset": section["start_offset"], "end_offset": section["end_offset"]}
            for key in ("start_seconds", "end_seconds"):
                if key in section:
                    part[key] = section[key]
            if not chapters or chapters[-1]["id"] != chapter_id:
                chapters.append({"id": chapter_id, "title": section["title"], "parts": [],
                                 "started_at": row["started_at"]})
            chapters[-1]["parts"].append(part)
    return chapters
