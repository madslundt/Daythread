"""Explicit enrollment rechecks current speaker turns and never learns from a guess."""

import asyncio
import io
import json
import wave
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from .speakers import Detection, Kind, fingerprint, pcm_wav, slice_pcm
from .voice_store import MIN_CLEAN_SECONDS, VoiceStore, compatible_voices, good_quality


def subtract_intervals(start: float, end: float, blockers: list[tuple[float, float]]) -> list:
    result, cursor = [], start
    for left, right in sorted(blockers):
        if right <= cursor or left >= end:
            continue
        if left > cursor:
            result.append((cursor, left))
        cursor = max(cursor, right)
        if cursor >= end:
            break
    if cursor < end:
        result.append((cursor, end))
    return result


class Voices(VoiceStore):
    def clean_sample(self, scope: dict, payload: dict, speaker_id: str) -> tuple[bytes, list[str]]:
        turns = self.memory.speakers.intervals(payload["turns"], speaker_id)
        rows = {row["id"]: row for row in scope["rows"]}
        intervals = []
        for source, start, end in turns:
            blockers = [(t["start"], t["end"]) for t in payload["turns"]
                        if t["source_id"] == source and t["speaker_id"] != speaker_id]
            intervals.extend((source, a, b) for a, b in subtract_intervals(start, end, blockers)
                             if b - a >= 1)
        if sum(b - a for _, a, b in intervals) < MIN_CLEAN_SECONDS:
            raise HTTPException(
                409, "Use at least ten seconds of clear speech without other speakers",
            )
        # Bound enrollment separately from full-conversation diarization. Preserve original PCM.
        pcm, seconds, used = bytearray(), 0.0, []
        for source, start, end in intervals:
            end = min(end, start + 60 - seconds)
            if end <= start:
                break
            piece = slice_pcm(rows[source], start, end)
            pcm.extend(piece)
            if source not in used:
                used.append(source)
            seconds += len(piece) / 32000
            # A short pause avoids artificial discontinuities between turns.
            pcm.extend(b"\0" * 3200)
        return pcm_wav(pcm), used

    def enrollment_snapshot(
        self, db, kind: Kind, key: str, speaker_id: str, version: str,
        profile_id: str | None, confirm: bool,
    ) -> tuple[dict, dict, dict]:
        scope = self.memory.speakers.resolve(kind, key, db)
        saved = self.memory.speakers.load(db, scope)
        if (not saved or saved["version"] != version
                or saved["fingerprint"] != fingerprint(scope)):
            raise HTTPException(409, "Speaker assignments or evidence changed; reload and retry")
        payload = json.loads(saved["payload"])
        if not any(s["id"] == speaker_id for s in payload["speakers"]):
            raise HTTPException(404, "Speaker not found")
        if profile_id and not db.execute("SELECT 1 FROM voice_profiles WHERE id=?",
                                         (profile_id,)).fetchone():
            raise HTTPException(404, "Saved voice not found")
        if confirm:
            preview = self.memory.speakers.view(scope, saved, db)
            speaker = next(s for s in preview["speakers"] if s["id"] == speaker_id)
            if not speaker["suggestion"] or speaker["suggestion"]["profile_id"] != profile_id:
                raise HTTPException(409, "This voice suggestion is no longer available; reload")
        return scope, saved, payload

    async def remember(
        self, kind: Kind, key: str, speaker_id: str, version: str,
        profile_id: str | None = None, name: str | None = None, confirm: bool = False,
    ) -> dict:
        if not self.memory.settings.voice_recognition_enabled:
            raise HTTPException(409, "Enable Remember speakers in server settings first")
        if not self.memory.settings.diarization_enabled:
            raise HTTPException(409, "Enable the local speaker service before remembering voices")
        async with self.memory.speakers.lock:
            with self.memory.store.connect() as db:
                db.execute("BEGIN")
                scope, saved, payload = self.enrollment_snapshot(
                    db, kind, key, speaker_id, version, profile_id, confirm,
                )
                revision = self.revision(db)
                expected_model = None
                if confirm:
                    row = db.execute(
                        "SELECT model FROM voice_vectors WHERE scope_key=? AND speaker_id=?",
                        (scope["key"], speaker_id),
                    ).fetchone()
                    expected_model = json.loads(row["model"]) if row else None
            data, source_ids = await asyncio.to_thread(
                self.clean_sample, scope, payload, speaker_id,
            )
            async with asyncio.timeout(self.memory.settings.diarization_timeout_seconds):
                # Do not force one speaker: a mixed sample must be allowed to fail enrollment.
                result = await self.memory.speakers.detect_turns(data)
            try:
                detected = Detection.model_validate(result)
                with wave.open(io.BytesIO(data)) as audio:
                    duration = audio.getnframes() / 16000
                if any(t.end <= t.start or t.end > duration + 0.05 for t in detected.turns):
                    raise ValueError("Invalid voice sample timing")
            except (ValidationError, ValueError) as exc:
                raise HTTPException(
                    502, "Local speaker service returned invalid voice timing",
                ) from exc
            model, voices = compatible_voices(result)
            distinct = {turn.speaker for turn in detected.turns}
            if not model or len(voices) != 1 or len(distinct) != 1:
                raise HTTPException(409, "A clear single-speaker voice sample is required. "
                                    "Check assignments and the local service's embedding support.")
            voice = next(iter(voices.values()))
            if (voice["speaker"] not in distinct or not good_quality(voice)
                    or voice["clean_seconds"] > duration + 0.05):
                raise HTTPException(409, "This sample is too short, overlapping or distorted. "
                                    "Choose a clearer conversation to remember this speaker.")
            if expected_model is not None and model != expected_model:
                raise HTTPException(
                    409, "The voice model changed; detect speakers and evaluate again",
                )
            with self.memory.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    current, latest, current_payload = self.enrollment_snapshot(
                        db, kind, key, speaker_id, version, profile_id, confirm,
                    )
                except HTTPException as exc:
                    if exc.status_code == 404:
                        raise HTTPException(
                            409, "Recorded evidence or saved voice was removed",
                        ) from exc
                    raise
                if (fingerprint(current) != fingerprint(scope) or self.revision(db) != revision
                        or not self.memory.settings.voice_recognition_enabled
                        or not self.memory.settings.diarization_enabled):
                    raise HTTPException(409, "Recorded evidence or saved voices changed; try again")
                # Verify availability under the same writer lock used by retention/deletion.
                if not self.memory.speakers.view(current, latest, db)["audio_available"]:
                    raise HTTPException(409, "Original audio is unavailable for learning")
                example_id = self.save_example(
                    db, current, current_payload, speaker_id, profile_id, name, model, voice,
                    source_ids,
                )
                new_version = str(uuid4())
                db.execute("UPDATE speaker_scopes SET version=?,payload=? WHERE scope_key=?", (
                    new_version, json.dumps(current_payload), current["key"],
                ))
                self.memory.speakers.event(db, current, "remembered", {
                    "speaker_id": speaker_id, "example_id": example_id, "version": new_version,
                })
                return self.memory.speakers.view(
                    current, self.memory.speakers.load(db, current), db,
                )
