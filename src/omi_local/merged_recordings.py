"""A finished file's upload parts form one seekable recording and transcript."""

import hashlib
import io
import json
import re
import struct
import sys
import wave
from array import array
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .blob_storage import audio_is_present
from .source_matching import MANUAL_UPLOAD_SQL
from .transcript_turns import saved_speaker_turns, transcript_turns

if TYPE_CHECKING:
    from .service import Memory
    from .store import Store


@dataclass
class Recording:
    id: str
    rows: list[dict]

    @property
    def complete(self) -> bool:
        metadata = [json.loads(row["source_metadata"]) for row in self.rows]
        expected = metadata[0]["part_count"]
        indices = [part["part_index"] for part in metadata]
        return (
            all(part["part_count"] == expected for part in metadata)
            and indices == list(range(expected))
            and all(
                abs(
                    (
                        datetime.fromisoformat(right["started_at"])
                        - datetime.fromisoformat(left["ended_at"])
                    ).total_seconds()
                )
                <= 1 / 16000
                for left, right in zip(self.rows, self.rows[1:])
            )
        )

    @property
    def audio_available(self) -> bool:
        return self.complete and all(audio_is_present(row) for row in self.rows)

    @property
    def frames(self) -> list[int]:
        return [round((datetime.fromisoformat(row["ended_at"])
                       - datetime.fromisoformat(row["started_at"])).total_seconds() * 16000)
                for row in self.rows]

    @property
    def playback_frames(self) -> int:
        return sum(self.frames)

    @property
    def playback_starts(self) -> list[float]:
        seconds, starts = 0, []
        for frames in self.frames:
            starts.append(seconds)
            seconds += frames / 16000
        return starts

    @property
    def audio_etag(self) -> str:
        return '"' + hashlib.sha256(json.dumps([
            (row["id"], row["digest"]) for row in self.rows
        ]).encode()).hexdigest() + '"'

    def playback_pcm(self, store: Store, first: int, stop: int) -> Iterator[bytes]:
        cursor = 0
        for row, count in zip(self.rows, self.frames):
            end = cursor + count * 2
            if end > first and cursor < stop:
                # At most one original upload part is held in memory.
                data = pcm(store, row, count)
                left, right = max(0, first - cursor), min(count * 2, stop - cursor)
                for offset in range(left, right, 65536):
                    yield data[offset:min(right, offset + 65536)]
                del data
            cursor = end
            if cursor >= stop:
                break

    def snapshot(self, *, partial: bool = False, attributed: dict | None = None) -> dict:
        ready = self.complete and all(row["state"] == "done" for row in self.rows)
        metadata = json.loads(self.rows[0]["source_metadata"])
        texts = [row["text"] or "" for row in self.rows if (row["text"] or "").strip()
                 and (not partial or row["state"] == "done")]
        merged_text = "\n".join(texts)
        details = [json.loads(row["topic_details"] or "{}") for row in self.rows]
        sections, turns, offset = [], [], 0
        attributed = attributed or {}
        context_counts = Counter(
            detail.get("context") for row, detail in zip(self.rows, details)
            if (row["text"] or "").strip() and (not partial or row["state"] == "done")
        )
        analyzed = ready and all(detail.get("sections") for row, detail in zip(self.rows, details)
                                 if (row["text"] or "").strip())
        parts = zip(self.rows, details, self.playback_starts, self.frames)
        for row, detail, seconds, frames in parts:
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
                        if (section.get("summary") and detail.get("summary_sources")
                                == context_counts[detail.get("context")]):
                            span["summary"] = section["summary"]
                        for key in ("start_seconds", "end_seconds"):
                            if key in section:
                                span[key] = seconds + section[key]
                        if "start_seconds" not in span:
                            # Without word alignment, the containing upload part is the
                            # narrowest known audio interval. Do not invent a word timestamp.
                            span["start_seconds"] = seconds
                            span["start_estimated"] = True
                        if sections and sections[-1]["chapter_id"] == chapter:
                            sections[-1]["end_offset"] = span["end_offset"]
                            # An unaligned final passage must not reuse an earlier endpoint.
                            sections[-1].pop("end_seconds", None)
                            if "end_seconds" in span:
                                sections[-1]["end_seconds"] = span["end_seconds"]
                        else:
                            sections.append(span)
                if not partial or row["state"] == "done":
                    source_turns = transcript_turns(row, attributed.get(row["id"])) or [
                        {"text": text, "start_offset": 0, "end_offset": len(text)},
                    ]
                    for turn in source_turns:
                        position = {
                            key: seconds + turn[key] for key in ("start", "end") if key in turn
                        }
                        turns.append({**turn, **position, "source_part": row["id"],
                                      "part_start": seconds, "part_end": seconds + frames / 16000,
                                      "start_offset": offset + turn["start_offset"],
                                      "end_offset": offset + turn["end_offset"]})
                    offset += len(text) + 1
        if len(sections) > 5:
            # Stream analysis runs in bounded work windows, not one chapter per window.
            boundaries = [index * len(sections) // 5 for index in range(5)] + [len(sections)]
            broad = []
            for start, end in zip(boundaries[:-1], boundaries[1:]):
                section = sections[start]
                summaries = [item.get("summary") for item in sections[start:end]]
                if all(summaries):
                    section["summary"] = "\n\n".join(dict.fromkeys(summaries))
                else:
                    section.pop("summary", None)
                section.pop("end_seconds", None)
                broad.append(section)
            sections = broad
        for index, section in enumerate(sections):
            section["end_offset"] = (sections[index + 1]["start_offset"]
                                     if index + 1 < len(sections) else len(merged_text))
        versions = [(row["id"], row["transcript_version"], row["state"]) for row in self.rows]
        version = hashlib.sha256(json.dumps(versions).encode()).hexdigest()
        available = self.audio_available
        availability = ("available" if available else "incomplete" if not self.complete
                        else "removed" if any(not row["audio_available"] for row in self.rows)
                        else "missing")
        digests = [detail for row, detail in zip(self.rows, details) if (row["text"] or "").strip()]
        summary = None
        if ready and digests and all(
            detail.get("recording_summary") and detail.get("summary_sources")
            == context_counts[detail.get("context")] for detail in digests
        ):
            summary = "\n\n".join(dict.fromkeys(detail["recording_summary"] for detail in digests))
        return {
            "id": self.id, "title": next((detail["title"] for detail in details
                                          if detail.get("title")), self.rows[0]["source_id"]),
            "started_at": metadata.get("recording_started_at", self.rows[0]["started_at"]),
            "ended_at": metadata.get("recording_ended_at", self.rows[-1]["ended_at"]),
            "state": ("done" if ready else "expired"
                      if any(row["state"] == "expired" for row in self.rows) else "processing"),
            "duration_seconds": self.playback_frames / 16000,
            "text": merged_text if ready or (partial and texts) else None, "sections": sections,
            "transcript_turns": turns if ready or (partial and texts) else [],
            "recording_summary": summary,
            "audio_available": available, "audio_availability": availability,
            "transcript_version": version,
        }


def load_recording(store: Store, recording_id: str, db=None) -> Recording:
    if db is None:
        with store.connect() as db:
            return load_recording(store, recording_id, db)
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM audio WHERE json_extract(source_metadata,'$.recording_id')=? AND "
            + MANUAL_UPLOAD_SQL
            + " ORDER BY json_extract(source_metadata,'$.part_index'),id",
            (recording_id,),
        )
    ]
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
    size = recording.playback_frames * 2
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", size + 36, b"WAVE", b"fmt ",
                         16, 1, 1, 16000, 32000, 2, 16, b"data", size)
    total, first, last = size + len(header), 0, size + len(header) - 1
    requested = request.headers.get("range")
    # If-Range mismatches request the complete current representation.
    etag = recording.audio_etag
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
        if first < len(header):
            yield header[first:min(last + 1, len(header))]
        if last >= len(header):
            yield from recording.playback_pcm(store, max(0, first - len(header)),
                                              last + 1 - len(header))

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
    peaks, cursor = [0] * 160, 0
    total = recording.playback_frames
    for block in recording.playback_pcm(store, 0, total * 2):
        samples = array("h", block)
        count = len(samples)
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
    def recording(recording_id: UUID, response: Response, archive: bool = False) -> dict:
        response.headers["Cache-Control"] = "no-store"
        app = memory()
        with app.store.connect() as db:
            db.execute("BEGIN")
            current = load_recording(app.store, str(recording_id), db)
            value = current.snapshot(
                partial=archive, attributed=saved_speaker_turns(app, current.rows, db),
            )
            value["recording_transcription"] = app.store.recording_transcription_view(
                current, app.settings, db,
            )
            value["transcript_selection"] = app.store.selected_recording_transcription(
                current, app.settings, db,
            )
            selection = value["transcript_selection"]
            if selection.get("state") == "selected" and selection.get("transcript"):
                from .conversation_state import Conversation

                group = Conversation(current.rows, current.rows[-1]["ended_at"], "recording")
                evidence, evidence_version, evidence_state = (
                    app.recaps.recording_speaker_evidence(
                        group, current.id, selection["transcript"], db,
                    )
                )
                selection["transcript"]["speaker_evidence"] = {
                    "state": evidence_state,
                    "version": evidence_version,
                    "utterances": evidence,
                }
            return value

    @router.put("/{recording_id}/transcript-selection")
    async def select_transcript(recording_id: UUID, request: Request) -> dict:
        app = memory()
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "Invalid transcript selection") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "Invalid transcript selection")
        expected_version, result_version = body.get("expected_version"), body.get("result_version")
        if (type(expected_version) is not int or expected_version < 0
                or (result_version is not None and not isinstance(result_version, str))):
            raise HTTPException(422, "Invalid transcript selection")
        try:
            current = load_recording(app.store, str(recording_id))
            return app.store.select_recording_transcription(
                current, app.settings, expected_version, result_version,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{recording_id}/transcriptions/{result_version}")
    def exact_transcript(recording_id: UUID, result_version: str, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        app = memory()
        with app.store.connect() as db:
            db.execute("BEGIN")
            try:
                load_recording(app.store, str(recording_id), db)
            except HTTPException as exc:
                raise HTTPException(404, "Recording transcript unavailable") from exc
            result = app.store.exact_recording_transcription(
                str(recording_id), result_version, app.settings, db,
            )
            if result is None:
                raise HTTPException(404, "Recording transcript unavailable")
            return result

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
