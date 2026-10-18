"""Local diarization over complete conversations, with user-reviewed speaker names."""

import asyncio
import hashlib
import io
import json
import wave
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .conversation_state import conversations, meeting_timeline
from .domain import stamp, utc_now

if TYPE_CHECKING:
    from .service import Memory

Kind = Literal["source", "meeting"]
MAX_SECONDS = 8 * 3600


class DetectedTurn(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    speaker: str = Field(min_length=1, max_length=200)


class Detection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(default="local", max_length=200)
    turns: list[DetectedTurn] = Field(max_length=100000)


def alphabet(index: int) -> str:
    result = ""
    while index >= 0:
        result = chr(65 + index % 26) + result
        index = index // 26 - 1
    return result


def fingerprint(scope: dict) -> str:
    return hashlib.sha256(json.dumps({
        "scope": scope["scope"], "bounds": scope["bounds"],
        "sources": [(r["id"], r["digest"], r["source_version"], r["transcript_version"],
                     r["state"]) for r in scope["rows"]],
    }, sort_keys=True).encode()).hexdigest()


def pcm_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(pcm)
    return output.getvalue()


def slice_pcm(row: dict, start: float, end: float, read_audio=None) -> bytes:
    if not row["audio_available"] or (not read_audio and not Path(row["path"]).is_file()):
        raise HTTPException(409, "Original audio is unavailable for this conversation")
    try:
        source = io.BytesIO(read_audio(row["id"])) if read_audio else row["path"]
        with wave.open(source, "rb") as stream:
            if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (
                1, 2, 16000,
            ):
                raise ValueError("Invalid audio format")
            first, last = round(start * 16000), round(end * 16000)
            stream.setpos(first)
            pcm = stream.readframes(last - first)
            if len(pcm) != (last - first) * 2:
                raise ValueError("Incomplete audio")
            return pcm
    except (OSError, ValueError, wave.Error, EOFError) as exc:
        raise HTTPException(409, "Original audio is missing or incomplete") from exc


class Speakers:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.lock = asyncio.Lock()

    def resolve(self, kind: Kind, key: str, db) -> dict:
        snapshot = self.memory.store.recap_snapshot(db)
        rows = snapshot["rows"]
        source = next((r for r in rows if r["id"] == key), None) if kind == "source" else None
        if kind == "source" and source is None:
            raise HTTPException(404, "Recording not found")
        meetings = meeting_timeline(snapshot, utc_now())["meetings"]
        meeting = next((m for m in meetings if (
            m["id"] == key if kind == "meeting" else
            m["started_at"] < source["ended_at"]
            and source["started_at"] < (m["ended_at"] or stamp(utc_now()))
        )), None)
        if kind == "meeting" and meeting is None:
            raise HTTPException(404, "Meeting not found")
        if meeting:
            kind, key = "meeting", meeting["id"]
            left = meeting["started_at"]
            right = meeting["ended_at"] or max(
                (r["ended_at"] for r in rows if r["started_at"] >= left), default=left,
            )
            rows = [r for r in rows if r["started_at"] < right and r["ended_at"] > left]
        elif source["source_kind"] == "manual_upload":
            # A browser upload is split into contiguous chunks with the original filename.
            candidates = [r for r in rows if r["source_kind"] == "manual_upload"
                          and (r["device_id"], r["source_id"]) ==
                          (source["device_id"], source["source_id"])]
            rows = [source]
            for row in reversed(candidates[:candidates.index(source)]):
                if row["ended_at"] != rows[0]["started_at"]:
                    break
                rows.insert(0, row)
            for row in candidates[candidates.index(source) + 1:]:
                if row["started_at"] != rows[-1]["ended_at"]:
                    break
                rows.append(row)
            key = rows[0]["id"]
            left, right = rows[0]["started_at"], rows[-1]["ended_at"]
        else:
            groups = conversations(
                snapshot, utc_now(), 5 * 60,
            )
            group = next((g for g in groups if any(r["id"] == key for r in g.sources)), None)
            rows = group.sources if group else [source]
            key = rows[0]["id"]
            left, right = rows[0]["started_at"], rows[-1]["ended_at"]
        return {"scope": {"kind": kind, "id": key}, "key": f"{kind}:{key}",
                "rows": rows, "bounds": [left, right]}

    @staticmethod
    def load(db, scope: dict) -> dict | None:
        row = db.execute("SELECT * FROM speaker_scopes WHERE scope_key=?",
                         (scope["key"],)).fetchone()
        return dict(row) if row else None

    def view(self, scope: dict, saved: dict | None, db=None) -> dict:
        if db is None:
            with self.memory.store.connect() as db:
                db.execute("BEGIN")
                return self.view(scope, saved, db)
        ready = saved is not None and saved["fingerprint"] == fingerprint(scope)
        payload = json.loads(saved["payload"]) if saved else {"speakers": [], "turns": []}
        turns = payload["turns"]
        if ready:
            self.attach_text(turns, scope["rows"])
        speakers = payload["speakers"]
        audio_available = bool(scope["rows"]) and all(
            r["audio_available"] and self.memory.store.has_audio(r["id"]) for r in scope["rows"]
        )
        recognition = self.memory.voices.annotate(db, scope, payload, ready and audio_available)
        for speaker in speakers:
            # Union same-speaker overlap so totals agree with playback.
            speaker["seconds"] = round(sum(b - a for _, a, b in
                self.intervals(turns, speaker["id"])), 3)
        return {"state": "ready" if ready else "stale" if saved else "idle",
                "configured": self.memory.settings.diarization_enabled,
                "version": saved["version"] if saved else None,
                "scope": scope["scope"], "source_count": len(scope["rows"]),
                "speakers": speakers, "turns": turns,
                "recognition": recognition,
                "audio_available": audio_available}

    def read(self, kind: Kind, key: str) -> dict:
        with self.memory.store.connect() as db:
            db.execute("BEGIN")
            scope = self.resolve(kind, key, db)
            return self.view(scope, self.load(db, scope), db)

    @staticmethod
    def attach_text(turns: list[dict], rows: list[dict]) -> None:
        # Assign each timestamped segment once; never invent word-level speaker alignment.
        for row in rows:
            matching = [t for t in turns if t["source_id"] == row["id"]]
            for segment in json.loads(row.get("segments") or "[]"):
                if not matching or not segment.get("text"):
                    continue
                start, end = segment.get("start"), segment.get("end")
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    continue
                overlapping = [t for t in matching
                               if min(t["end"], end) > max(t["start"], start)]
                if not overlapping or len({t["speaker_id"] for t in overlapping}) != 1:
                    continue
                best = max(overlapping, key=lambda t: min(t["end"], end) - max(t["start"], start))
                if min(best["end"], end) - max(best["start"], start) >= (end - start) * 0.8:
                    best["text"] = (best.get("text", "") + " " + segment["text"]).strip()

    @staticmethod
    def intervals(turns: list[dict], speaker: str) -> list[tuple[str, float, float]]:
        intervals = []
        # Source order is already chronological; merge only within the same recording.
        sources = {}
        for turn in turns:
            if turn["speaker_id"] == speaker:
                sources.setdefault(turn["source_id"], []).append((turn["start"], turn["end"]))
        for source, times in sources.items():
            for a, b in sorted(times):
                if intervals and intervals[-1][0] == source and a <= intervals[-1][2]:
                    intervals[-1] = (source, intervals[-1][1], max(b, intervals[-1][2]))
                else:
                    intervals.append((source, a, b))
        return intervals

    def assemble(self, scope: dict) -> tuple[bytes, list[dict]]:
        pcm, mappings = bytearray(), []
        left, right = map(datetime.fromisoformat, scope["bounds"])
        for row in scope["rows"]:
            at = datetime.fromisoformat(row["started_at"])
            start = max(0, (left - at).total_seconds())
            end = (min(right, datetime.fromisoformat(row["ended_at"])) - at).total_seconds()
            if end <= start:
                continue
            if len(pcm) / 32000 + end - start > MAX_SECONDS:
                raise HTTPException(422, "Choose a conversation with at most eight hours of audio")
            offset = len(pcm) / 32000
            piece = slice_pcm(row, start, end, self.memory.store.read_audio)
            mappings.append({"source_id": row["id"], "offset": offset,
                             "start": start, "end": start + len(piece) / 32000})
            pcm.extend(piece)
        if not mappings:
            raise HTTPException(409, "No recorded audio is available")
        return pcm_wav(pcm), mappings

    async def detect_turns(self, data: bytes, num_speakers: int | None = None) -> dict:
        settings = self.memory.settings
        try:
            response = await self.memory.inference.client.post(
                settings.diarization_base_url + "/diarize",
                files={"file": ("conversation.wav", data, "audio/wav")},
                data={"num_speakers": str(num_speakers)} if num_speakers else {},
                timeout=settings.diarization_timeout_seconds,
            )
            response.raise_for_status()
            if len(response.content) > 16_000_000:
                raise ValueError("Oversized diarization result")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                502, "Local speaker detection is unavailable; check its service",
            ) from exc

    @staticmethod
    def map_turns(result: dict, mappings: list[dict]) -> dict:
        try:
            detected = Detection.model_validate(result)
        except ValidationError as exc:
            raise HTTPException(502, "Speaker service returned invalid turns") from exc
        duration = mappings[-1]["offset"] + mappings[-1]["end"] - mappings[-1]["start"]
        ends = [m["offset"] + m["end"] - m["start"] for m in mappings]
        speakers, turns = {}, []
        for turn in sorted(detected.turns, key=lambda t: (t.start, t.end, t.speaker)):
            if (turn.end <= turn.start or turn.start >= duration
                    or turn.end > duration + 0.05):
                raise HTTPException(502, "Speaker service returned invalid timing")
            if turn.speaker not in speakers:
                if len(speakers) >= 64:
                    raise HTTPException(502, "Speaker service returned too many speakers")
                speakers[turn.speaker] = {
                    "id": str(uuid4()), "label": alphabet(len(speakers)), "name": None,
                    "voice_key": turn.speaker,
                }
            for index in range(bisect_right(ends, turn.start), len(mappings)):
                mapping = mappings[index]
                if mapping["offset"] >= turn.end:
                    break
                left = max(turn.start, mapping["offset"])
                right = min(turn.end, mapping["offset"] + mapping["end"] - mapping["start"])
                if right > left:
                    turns.append({"id": str(uuid4()), "source_id": mapping["source_id"],
                                  "start": round(left - mapping["offset"] + mapping["start"], 6),
                                  "end": round(right - mapping["offset"] + mapping["start"], 6),
                                  "speaker_id": speakers[turn.speaker]["id"]})
        order = {m["source_id"]: i for i, m in enumerate(mappings)}
        turns.sort(key=lambda t: (order[t["source_id"]], t["start"], t["end"]))
        return {"speakers": list(speakers.values()), "turns": turns, "model": detected.model}

    @staticmethod
    def event(db, scope: dict, action: str, payload: dict) -> None:
        db.execute("INSERT INTO speaker_events VALUES(?,?,?,?,?)",
                   (str(uuid4()), scope["key"], action, json.dumps(payload), stamp(utc_now())))

    async def detect(self, kind: Kind, key: str, num_speakers=None, reset=False) -> dict:
        if not self.memory.settings.diarization_enabled:
            raise HTTPException(
                409, "Enable speaker detection in Settings and start its local service",
            )
        # Coalesce clicks and keep expensive requests serialized within each archive.
        async with self.lock:
            with self.memory.store.connect() as db:
                db.execute("BEGIN")
                scope = self.resolve(kind, key, db)
                saved = self.load(db, scope)
                before = saved["version"] if saved else None
                if saved and not reset:
                    if saved["fingerprint"] == fingerprint(scope):
                        return self.view(scope, saved, db)
                    raise HTTPException(
                        409, "Evidence changed; detect again to replace labels and names",
                    )
                if any(row["state"] != "done" for row in scope["rows"]):
                    raise HTTPException(409, "Wait for this conversation's transcription to finish")
            data, mappings = await asyncio.to_thread(self.assemble, scope)
            async with asyncio.timeout(self.memory.settings.diarization_timeout_seconds):
                result = await self.detect_turns(data, num_speakers)
            payload = self.map_turns(result, mappings)
            with self.memory.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    current = self.resolve(kind, key, db)
                except HTTPException as exc:
                    raise HTTPException(
                        409, "Conversation changed during detection; try again",
                    ) from exc
                latest = self.load(db, current)
                if fingerprint(current) != fingerprint(scope) or (
                    (latest["version"] if latest else None) != before
                ):
                    raise HTTPException(409, "Conversation changed during detection; try again")
                version = str(uuid4())
                db.execute("INSERT OR REPLACE INTO speaker_scopes VALUES(?,?,?,?)",
                           (scope["key"], version, fingerprint(scope), json.dumps(payload)))
                db.execute("DELETE FROM speaker_dependencies WHERE scope_key=?", (scope["key"],))
                db.executemany("INSERT INTO speaker_dependencies VALUES(?,?)",
                               [(scope["key"], row["id"]) for row in scope["rows"]])
                self.memory.voices.invalidate_scope(db, scope["key"])
                self.memory.voices.save_vectors(db, scope, payload, result)
                # Detection snapshots plus later edit events can replay this projection.
                self.event(db, scope, "detected", payload | {
                    "version": version, "fingerprint": fingerprint(scope),
                })
                return self.view(current, self.load(db, current), db)

    def mutate(self, kind: Kind, key: str, version: str, action: str, values: dict) -> dict:
        with self.memory.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self.resolve(kind, key, db)
            saved = self.load(db, scope)
            if (not saved or saved["version"] != version
                    or saved["fingerprint"] != fingerprint(scope)):
                raise HTTPException(
                    409, "Speaker assignments or evidence changed; reload and retry",
                )
            payload = json.loads(saved["payload"])
            speakers = {s["id"]: s for s in payload["speakers"]}
            if action == "rename":
                if values["speaker_id"] not in speakers:
                    raise HTTPException(404, "Speaker not found")
                speakers[values["speaker_id"]]["name"] = values["name"]
            elif action == "reassign":
                turn = next((t for t in payload["turns"] if t["id"] == values["turn_id"]), None)
                if turn is None or values["speaker_id"] not in speakers:
                    raise HTTPException(404, "Turn or speaker not found")
                turn["speaker_id"] = values["speaker_id"]
            else:
                source, target = values["source_id"], values["target_id"]
                if source == target or source not in speakers or target not in speakers:
                    raise HTTPException(422, "Choose two different speakers in this conversation")
                for turn in payload["turns"]:
                    if turn["speaker_id"] == source:
                        turn["speaker_id"] = target
                payload["speakers"] = [s for s in payload["speakers"] if s["id"] != source]
            new_version = str(uuid4())
            self.memory.voices.invalidate_scope(db, scope["key"])
            db.execute("UPDATE speaker_scopes SET version=?,payload=? WHERE scope_key=?",
                       (new_version, json.dumps(payload), scope["key"]))
            self.event(db, scope, action, values | {"version": new_version})
            return self.view(scope, self.load(db, scope), db)

    def audio(self, kind: Kind, key: str, speaker: str, version: str | None = None) -> bytes:
        with self.memory.store.connect() as db:
            # Prevent retention/deletion between the availability check and reading audio.
            db.execute("BEGIN IMMEDIATE")
            scope = self.resolve(kind, key, db)
            saved = self.load(db, scope)
            if (not saved or saved["fingerprint"] != fingerprint(scope)
                    or (version is not None and saved["version"] != version)):
                raise HTTPException(409, "Speaker assignments need detection before playback")
            payload = json.loads(saved["payload"])
            if not any(s["id"] == speaker for s in payload["speakers"]):
                raise HTTPException(404, "Speaker not found")
            rows = {r["id"]: r for r in scope["rows"]}
            intervals = self.intervals(payload["turns"], speaker)
            if not intervals:
                raise HTTPException(409, "This speaker has no assigned speaking turns")
            return pcm_wav(b"".join(
                slice_pcm(rows[source], a, b, self.memory.store.read_audio)
                for source, a, b in intervals
            ))
