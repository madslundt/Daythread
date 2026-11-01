"""One browsing/player surface over original, scoped timeline session evidence."""

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .merged_recordings import Recording, audio_response, recording_waveform


class SessionRecording(Recording):
    @property
    def complete(self) -> bool:
        # A timeline session contains only its scoped, transcribed parts. Missing
        # or quiet parts remain in the archive and appear as playback interruptions.
        return True

    def snapshot(self) -> dict:
        result = super().snapshot(partial=True)
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


def session_recording_routes(memory: Callable) -> APIRouter:
    router = APIRouter(prefix="/v1/recording-groups")

    def load(group_id, day, q, zone_id, tag_id, version=None):
        from .recording_groups import recording_groups

        start, end = memory().day_window(day)
        try:
            result = recording_groups(
                memory().store, start, end, q, 1, 0, group_id=str(group_id),
                zone_id=str(zone_id) if zone_id else None,
                tag_id=str(tag_id) if tag_id else None, playback=True,
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
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return load(group_id, day, q, zone_id, tag_id).snapshot()

    @router.api_route("/{group_id}/audio", methods=["GET", "HEAD"])
    def audio(
        group_id: UUID, day: date, request: Request,
        q: str = Query(default="", max_length=200),
        zone_id: UUID | None = None, tag_id: UUID | None = None,
        version: str | None = Query(default=None, max_length=64),
    ) -> Response:
        recording = load(group_id, day, q, zone_id, tag_id, version)
        return audio_response(memory().store, recording, request)

    @router.get("/{group_id}/waveform")
    def waveform(
        group_id: UUID, day: date, q: str = Query(default="", max_length=200),
        zone_id: UUID | None = None, tag_id: UUID | None = None,
        version: str | None = Query(default=None, max_length=64),
    ) -> dict:
        recording = load(group_id, day, q, zone_id, tag_id, version)
        return recording_waveform(memory().store, recording)

    return router
