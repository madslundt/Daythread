"""A finished file's upload parts form one seekable recording and transcript."""

import hashlib
import io
import json
import re
import struct
import sys
import wave
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .blob_storage import audio_is_present
from .source_matching import MANUAL_UPLOAD_SQL

if TYPE_CHECKING:
    from .service import Memory
    from .store import Store


@dataclass
class Recording:
    id: str
    rows: list[dict]

    @property
    def complete(self) -> bool:
        expected = json.loads(self.rows[0]["source_metadata"])["part_count"]
        indices = [json.loads(row["source_metadata"])["part_index"] for row in self.rows]
        return indices == list(range(expected)) and all(
            abs((datetime.fromisoformat(right["started_at"])
                 - datetime.fromisoformat(left["ended_at"])).total_seconds()) <= 1 / 16000
            for left, right in zip(self.rows, self.rows[1:])
        )

    @property
    def audio_available(self) -> bool:
        return self.complete and all(audio_is_present(row) for row in self.rows)

    @property
    def frames(self) -> list[int]:
        return [round((datetime.fromisoformat(row["ended_at"])
                       - datetime.fromisoformat(row["started_at"])).total_seconds() * 16000)
                for row in self.rows]

    def snapshot(self) -> dict:
        ready = self.complete and all(row["state"] == "done" for row in self.rows)
        metadata = json.loads(self.rows[0]["source_metadata"])
        texts = [row["text"] or "" for row in self.rows if (row["text"] or "").strip()]
        merged_text = "\n".join(texts)
        details = [json.loads(row["topic_details"] or "{}") for row in self.rows]
        sections, offset, seconds = [], 0, 0
        analyzed = ready and all(detail.get("sections") for row, detail in zip(self.rows, details)
                                 if (row["text"] or "").strip())
        for row, detail, frames in zip(self.rows, details, self.frames):
            text = row["text"] or ""
            if text.strip():
                if analyzed:
                    for section in detail["sections"]:
                        chapter = section.get(
                            "chapter_id", f'{row["id"]}:{section["start_offset"]}',
                        )
                        span = {"title": section["title"], "chapter_id": chapter,
                                "start_offset": offset + section["start_offset"],
                                "end_offset": offset + section["end_offset"]}
                        for key in ("start_seconds", "end_seconds"):
                            if key in section:
                                span[key] = seconds + section[key]
                        if sections and sections[-1]["chapter_id"] == chapter:
                            sections[-1]["end_offset"] = span["end_offset"]
                            # An unaligned final passage must not reuse an earlier endpoint.
                            sections[-1].pop("end_seconds", None)
                            if "end_seconds" in span:
                                sections[-1]["end_seconds"] = span["end_seconds"]
                        else:
                            sections.append(span)
                offset += len(text) + 1
            seconds += frames / 16000
        for index, section in enumerate(sections):
            section["end_offset"] = (sections[index + 1]["start_offset"]
                                     if index + 1 < len(sections) else len(merged_text))
        versions = [(row["id"], row["transcript_version"], row["state"]) for row in self.rows]
        version = hashlib.sha256(json.dumps(versions).encode()).hexdigest()
        available = self.audio_available
        availability = ("available" if available else "incomplete" if not self.complete
                        else "removed" if any(not row["audio_available"] for row in self.rows)
                        else "missing")
        return {
            "id": self.id, "title": next((detail["title"] for detail in details
                                          if detail.get("title")), self.rows[0]["source_id"]),
            "started_at": metadata.get("recording_started_at", self.rows[0]["started_at"]),
            "ended_at": metadata.get("recording_ended_at", self.rows[-1]["ended_at"]),
            "state": ("done" if ready else "expired"
                      if any(row["state"] == "expired" for row in self.rows) else "processing"),
            "duration_seconds": seconds,
            "text": merged_text if ready else None, "sections": sections,
            "audio_available": available, "audio_availability": availability,
            "transcript_version": version,
        }


def load_recording(store: Store, recording_id: str) -> Recording:
    with store.connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM audio WHERE json_extract(source_metadata,'$.recording_id')=? AND "
            + MANUAL_UPLOAD_SQL + " ORDER BY json_extract(source_metadata,'$.part_index'),id",
            (recording_id,),
        )]
    if not rows:
        raise HTTPException(404, "Recording unavailable")
    # Identical retries with a different source UUID still represent one file part.
    parts = {}
    for row in rows:
        parts.setdefault(json.loads(row["source_metadata"])["part_index"], row)
    return Recording(recording_id, list(parts.values()))


def pcm(store: Store, row: dict, frames: int) -> bytes:
    with wave.open(io.BytesIO(store.read_audio(row["id"])), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) != (
            1, 2, 16000, frames,
        ):
            raise ValueError("Recording audio no longer matches its capture bounds")
        data = wav.readframes(frames)
    if len(data) != frames * 2:
        raise ValueError("Recording audio is incomplete")
    return data


def audio_response(store: Store, recording: Recording, request: Request) -> Response:
    if not recording.complete:
        raise HTTPException(409, "Recording upload is incomplete")
    if not recording.audio_available:
        raise HTTPException(404, "Recording audio unavailable")
    frames = recording.frames
    size = sum(frames) * 2
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", size + 36, b"WAVE", b"fmt ",
                         16, 1, 1, 16000, 32000, 2, 16, b"data", size)
    total, first, last = size + len(header), 0, size + len(header) - 1
    requested = request.headers.get("range")
    # If-Range mismatches request the complete current representation.
    etag = '"' + hashlib.sha256(json.dumps([
        (row["id"], row["digest"]) for row in recording.rows
    ]).encode()).hexdigest() + '"'
    partial = bool(requested and request.headers.get("if-range", etag) == etag)
    if partial:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
        if not match or not any(match.groups()):
            raise HTTPException(416, "Invalid audio range",
                                headers={"Content-Range": f"bytes */{total}"})
        left, right = match.groups()
        if left:
            first, last = int(left), min(int(right), last) if right else last
        else:
            first = max(0, total - int(right))
        if first > last or first >= total:
            raise HTTPException(416, "Invalid audio range",
                                headers={"Content-Range": f"bytes */{total}"})

    def stream():
        cursor = len(header)
        if first < len(header):
            yield header[first:min(last + 1, len(header))]
        for row, count in zip(recording.rows, frames):
            end = cursor + count * 2
            if end > first and cursor <= last:
                # At most one original upload part is held in memory, even for twelve hours.
                data = pcm(store, row, count)
                left, right = max(0, first - cursor), min(len(data), last + 1 - cursor)
                for offset in range(left, right, 65536):
                    yield data[offset:min(right, offset + 65536)]
                del data
            cursor = end
            if cursor > last:
                break

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(last - first + 1),
               "Content-Disposition": f'inline; filename="{recording.id}.wav"',
               "Cache-Control": "no-store", "ETag": etag}
    if partial:
        headers["Content-Range"] = f"bytes {first}-{last}/{total}"
    if request.method == "HEAD":
        return Response(media_type="audio/wav", headers=headers,
                        status_code=206 if partial else 200)
    return StreamingResponse(stream(), status_code=206 if partial else 200,
                             media_type="audio/wav", headers=headers)


def recording_waveform(store: Store, recording: Recording) -> dict:
    if not recording.complete:
        raise HTTPException(409, "Recording upload is incomplete")
    if not recording.audio_available:
        raise HTTPException(404, "Recording audio unavailable")
    frames, peaks, cursor = recording.frames, [0] * 160, 0
    total = sum(frames)
    for row, count in zip(recording.rows, frames):
        samples = array("h", pcm(store, row, count))
        if sys.byteorder != "little":
            samples.byteswap()
        offset = 0
        while offset < count:
            bin_index = min(159, (cursor + offset) * 160 // total)
            end = min(count, ((bin_index + 1) * total + 159) // 160 - cursor)
            span = samples[offset:end]
            peaks[bin_index] = max(peaks[bin_index], abs(min(span)), abs(max(span)))
            offset = end
        cursor += count
    maximum = max(peaks)
    return {"duration": total / 16000,
            "peaks": [peak / maximum for peak in peaks] if maximum else peaks}


def merged_recording_routes(memory: Callable[[], Memory]) -> APIRouter:
    router = APIRouter(prefix="/v1/recordings")

    @router.get("/{recording_id}")
    def recording(recording_id: UUID, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return load_recording(memory().store, str(recording_id)).snapshot()

    @router.api_route("/{recording_id}/audio", methods=["GET", "HEAD"])
    def audio(recording_id: UUID, request: Request) -> Response:
        store = memory().store
        return audio_response(store, load_recording(store, str(recording_id)), request)

    @router.get("/{recording_id}/waveform")
    def waveform(recording_id: UUID, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        store = memory().store
        return recording_waveform(store, load_recording(store, str(recording_id)))

    return router
