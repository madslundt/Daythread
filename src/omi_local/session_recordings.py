"""One browsing/player surface over original, scoped timeline session evidence."""

import hashlib
import json
import sys
from array import array
from collections.abc import Callable, Iterator
from datetime import date, datetime
from functools import cached_property
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .blob_storage import audio_is_present
from .merged_recordings import Recording, audio_response, pcm, recording_waveform
from .transcript_turns import saved_speaker_turns

if TYPE_CHECKING:
    from .store import Store


class SessionRecording(Recording):
    @property
    def complete(self) -> bool:
        # A session represents its scoped rows and exposes any interruptions.
        return True

    def snapshot(self, *, attributed: dict | None = None) -> dict:
        result = super().snapshot(partial=True, attributed=attributed)
        interruptions, seconds = [], 0
        for index, (row, frames) in enumerate(zip(self.rows, self.frames)):
            if index:
                previous = self.rows[index - 1]
                gap = (datetime.fromisoformat(row["started_at"])
                       - datetime.fromisoformat(previous["ended_at"])).total_seconds()
                if abs(gap) > 1 / 16000:
                    interruptions.append({"position_seconds": seconds, "gap_seconds": gap,
                                          "previous_end": previous["ended_at"],
                                          "next_start": row["started_at"]})
            seconds += frames / 16000
        result.update(interruptions=interruptions,
                      title=result["title"] or "Recording session")
        # Transcription progress must not reload unchanged audio during playback.
        result["audio_version"] = hashlib.sha256(json.dumps([
            [row[key] for key in ("id", "digest", "started_at", "ended_at")]
            for row in self.rows
        ]).encode()).hexdigest()
        if all(row["source_kind"].endswith(("_api", "_export")) for row in self.rows):
            result["audio_availability"] = "transcript_only"
        return result


class ParallelTrackRecording(SessionRecording):
    """Mix retained Parrot tracks on their shared timeline instead of concatenating them."""

    @cached_property
    def tracks(self) -> list[tuple[int, int, dict]]:
        origin = datetime.fromisoformat(self.rows[0]["started_at"])
        return [(round((datetime.fromisoformat(row["started_at"]) - origin).total_seconds()
                       * 16000), count, row) for row, count in zip(self.rows, self.frames)]

    @cached_property
    def available_tracks(self) -> list[tuple[int, int, dict]]:
        return [track for track in self.tracks if audio_is_present(track[2])]

    @property
    def audio_available(self) -> bool:
        return bool(self.available_tracks)

    @property
    def playback_frames(self) -> int:
        return max(start + count for start, count, _ in self.tracks)

    @property
    def playback_starts(self) -> list[float]:
        return [start / 16000 for start, _, _ in self.tracks]

    @cached_property
    def mixed_audio_version(self) -> str:
        return hashlib.sha256(json.dumps([
            [(start, frames, row["id"], row["digest"]) for start, frames, row in self.tracks],
            [row["id"] for _, _, row in self.available_tracks],
        ]).encode()).hexdigest()

    @property
    def audio_etag(self) -> str:
        return f'"{self.mixed_audio_version}"'

    def snapshot(self, *, attributed: dict | None = None) -> dict:
        result = Recording.snapshot(self, partial=True, attributed=attributed)
        # Capture tracks can overlap; transcript order is not playback order.
        for section in result["sections"]:
            for key in ("start_seconds", "end_seconds", "start_estimated"):
                section.pop(key, None)
        result.update(ended_at=max(row["ended_at"] for row in self.rows),
                      started_at=self.rows[0]["started_at"],
                      duration_seconds=self.playback_frames / 16000,
                      interruptions=[],
                      audio_version=self.mixed_audio_version,
                      title=result["title"] or "Recording session")
        if self.audio_available and len(self.available_tracks) < len(self.tracks):
            result["audio_availability"] = "partial"
        return result

    def playback_pcm(self, store: Store, first: int, stop: int) -> Iterator[bytes]:
        # Mix bounded blocks and cache only source parts overlapping the current block.
        cache = {}
        for left in range(first // 2, (stop + 1) // 2, 32768):
            right = min(left + 32768, (stop + 1) // 2)
            totals, counts = [0] * (right - left), [0] * (right - left)
            active = {}
            for start, frames, row in self.available_tracks:
                begin, end = max(left, start), min(right, start + frames)
                if begin >= end:
                    continue
                key = row["id"]
                if key not in cache:
                    cache[key] = array("h", pcm(store, row, frames))
                    if sys.byteorder != "little":
                        cache[key].byteswap()
                active[key] = cache[key]
                for offset in range(begin, end):
                    totals[offset - left] += cache[key][offset - start]
                    counts[offset - left] += 1
            cache = active
            samples = array("h", (int(total / count) if count else 0
                                  for total, count in zip(totals, counts)))
            if sys.byteorder != "little":
                samples.byteswap()
            yield samples.tobytes()[max(0, first - left * 2):stop - left * 2]


def session_recording_routes(memory: Callable) -> APIRouter:
    router = APIRouter(prefix="/v1/recording-groups")

    def load(group_id, day, q, zone_id, tag_id, version=None, *, archive=False):
        from .recording_groups import recording_groups

        start, end = memory().day_window(day)
        try:
            result = recording_groups(
                memory().store, start, end, q, 1, 0, group_id=str(group_id),
                zone_id=str(zone_id) if zone_id else None,
                tag_id=str(tag_id) if tag_id else None, playback=True, archive=archive,
            )
            if version and result.snapshot()["audio_version"] != version:
                raise HTTPException(
                    409, "Recorded evidence changed or expired; reload and review it",
                )
            return result
        except KeyError as exc:
            raise HTTPException(404, "Recording group unavailable") from exc

    @router.get("/{group_id}/recording")
    def recording(
        group_id: UUID, day: date, response: Response,
        q: str = Query(default="", max_length=200),
        zone_id: UUID | None = None, tag_id: UUID | None = None,
        archive: bool = False,
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        current = load(group_id, day, q, zone_id, tag_id, archive=archive)
        return current.snapshot(attributed=saved_speaker_turns(memory(), current.rows))

    @router.api_route("/{group_id}/audio", methods=["GET", "HEAD"])
    def audio(
        group_id: UUID, day: date, request: Request,
        q: str = Query(default="", max_length=200),
        zone_id: UUID | None = None, tag_id: UUID | None = None,
        version: str | None = Query(default=None, max_length=64),
        archive: bool = False,
    ) -> Response:
        recording = load(group_id, day, q, zone_id, tag_id, version, archive=archive)
        return audio_response(memory().store, recording, request)

    @router.get("/{group_id}/waveform")
    def waveform(
        group_id: UUID, day: date, q: str = Query(default="", max_length=200),
        zone_id: UUID | None = None, tag_id: UUID | None = None,
        version: str | None = Query(default=None, max_length=64),
        archive: bool = False,
    ) -> dict:
        recording = load(group_id, day, q, zone_id, tag_id, version, archive=archive)
        return recording_waveform(memory().store, recording)

    return router
