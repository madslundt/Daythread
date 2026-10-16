"""Voice profile persistence and conservative, evaluated identity suggestions."""

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .domain import stamp, utc_now
from .speakers import fingerprint

if TYPE_CHECKING:
    from .service import Memory

MIN_CLEAN_SECONDS = 10
MAX_OVERLAP = 0.1
MAX_CLIPPING = 0.01
MAX_PROFILES = 100
MAX_EXAMPLES = 30


class EmbeddingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=300)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimension: int = Field(ge=1, le=4096, strict=True)
    normalization: str = Field(pattern=r"^l2-v1$")


class VoiceVector(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speaker: str = Field(min_length=1, max_length=200)
    embedding: list[float] = Field(min_length=1, max_length=4096)
    clean_seconds: float = Field(ge=0, le=28800, allow_inf_nan=False)
    overlap_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    clipped_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)


def compatible_voices(result: dict) -> tuple[dict | None, dict[str, dict]]:
    """An older or unsupported runtime may diarize without supporting recognition."""
    from .voice_matching import quality_eligible, validate_vector

    try:
        if not isinstance(result, dict):
            return None, {}
        model = EmbeddingModel.model_validate(result.get("embedding_model")).model_dump()
        raw = result.get("voices")
        if not isinstance(raw, list) or len(raw) > 64:
            return None, {}
        voices = {}
        for item in raw:
            if not isinstance(item, dict):
                return None, {}
            vector = validate_vector(item.get("embedding"), model["dimension"])
            if not math.isclose(math.hypot(*item["embedding"]), 1, rel_tol=1e-4, abs_tol=1e-6):
                return None, {}
            quality_eligible(item)
            voice = VoiceVector.model_validate(item).model_dump()
            voice["embedding"] = list(vector)
            if voice["speaker"] in voices:
                return None, {}
            voices[voice["speaker"]] = voice
        return model, voices
    except (ValidationError, ValueError, TypeError):
        return None, {}


def good_quality(voice: dict) -> bool:
    return (voice["clean_seconds"] >= MIN_CLEAN_SECONDS
            and voice["overlap_ratio"] <= MAX_OVERLAP
            and voice["clipped_ratio"] <= MAX_CLIPPING)


def turn_signature(payload: dict) -> str:
    return hashlib.sha256(json.dumps([
        (t["source_id"], t["start"], t["end"], t["speaker_id"])
        for t in payload["turns"]
    ], sort_keys=True).encode()).hexdigest()


class VoiceStore:
    def __init__(self, memory: Memory):
        self.memory = memory
        self._report_cache = {}

    @staticmethod
    def revision(db) -> str:
        return db.execute("SELECT revision FROM voice_meta WHERE singleton=1").fetchone()[0]

    @staticmethod
    def gallery(db) -> list[dict]:
        gallery = []
        for row in db.execute("SELECT * FROM voice_examples ORDER BY id"):
            sources = db.execute(
                "SELECT a.id,a.audio_available,a.state,a.digest,a.path "
                "FROM voice_example_sources s JOIN audio a ON a.id=s.audio_id "
                "WHERE s.example_id=?", (row["id"],),
            ).fetchall()
            source_ids, digests = json.loads(row["source_ids"]), json.loads(row["source_digests"])
            if (len(sources) != len(source_ids) or not sources or any(
                not r["audio_available"] or r["state"] != "done"
                or not Path(r["path"]).is_file() for r in sources
            ) or {r["id"]: r["digest"] for r in sources} != dict(zip(source_ids, digests))):
                continue
            gallery.append({
                "id": row["id"], "profile_id": row["profile_id"],
                "conversation_id": row["scope_key"], "embedding": json.loads(row["embedding"]),
                "model": json.loads(row["model"]), "source_ids": source_ids,
                "source_digests": digests,
            })
        return gallery

    def recognition(
        self, db, model: dict | None = None, gallery: list[dict] | None = None,
    ) -> tuple[dict, dict | None]:
        if not self.memory.settings.voice_recognition_enabled:
            return {"state": "disabled", "message":
                    "Enable Remember speakers in server settings to save confirmed voices."}, None
        gallery = self.gallery(db) if gallery is None else gallery
        message = ("Save clear examples from at least two separate conversations per person. "
                   "Suggestions stay off until evaluation on separate recordings passes.")
        path = self.memory.settings.voice_recognition_report
        if not path or not gallery:
            return {"state": "unvalidated", "message": message}, None
        try:
            from .voice_matching import gallery_fingerprint, validate_report

            report_path = Path(path)
            if not report_path.is_absolute():
                report_path = self.memory.store.root / report_path
            with report_path.open("rb") as stream:
                report_bytes = stream.read(4_000_001)
            if len(report_bytes) > 4_000_000:
                raise ValueError("Oversized report")
            report = json.loads(report_bytes)
            if model is None:
                model = gallery[0]["model"]
            cache_key = (hashlib.sha256(report_bytes).hexdigest(),
                         gallery_fingerprint(gallery, model))
            valid = self._report_cache.get(cache_key)
            if valid is None:
                valid = validate_report(report, gallery, model)
                if len(self._report_cache) >= 8:
                    self._report_cache.clear()
                self._report_cache[cache_key] = valid
            # The evaluator returns a bool/reason pair; no report can enable automatic naming.
            accepted, reason = valid
            if not accepted:
                return {"state": "unvalidated", "message": str(reason)}, None
            return {"state": "ready", "message":
                    "Voice suggestions passed the configured recording evaluation. "
                    "Confirm each suggestion before its name or example is saved."}, report
        except (OSError, ValueError, TypeError, KeyError):
            return {"state": "unvalidated", "message":
                    "Voice evaluation is unavailable or invalid. Suggestions remain off."}, None

    def directory(self, db=None) -> dict:
        if db is None:
            with self.memory.store.connect() as db:
                db.execute("BEGIN")
                return self.directory(db)
        from .voice_matching import independent_conversation_counts

        gallery = self.gallery(db)
        # Model upgrades leave old examples available to inspect/forget, but never combine
        # incompatible representations into two-conversation recognition support.
        model_groups = {}
        for example in gallery:
            model_key = json.dumps(example["model"], sort_keys=True)
            model_groups.setdefault(model_key, []).append(example)
        conversation_counts = {}
        for group in model_groups.values():
            for profile, count in independent_conversation_counts(group).items():
                conversation_counts[profile] = max(conversation_counts.get(profile, 0), count)
        profiles = []
        for row in db.execute("SELECT * FROM voice_profiles ORDER BY created_at,id"):
            examples = []
            for item in db.execute(
                "SELECT * FROM voice_examples WHERE profile_id=? ORDER BY created_at,id",
                (row["id"],),
            ):
                kind, key = item["scope_key"].split(":", 1)
                examples.append({"id": item["id"], "scope": {"kind": kind, "id": key},
                                 "speaker_id": item["speaker_id"], "seconds": item["seconds"],
                                 "created_at": item["created_at"]})
            profiles.append({"id": row["id"], "name": row["name"], "examples": examples,
                             "example_count": len(examples),
                             "conversation_count": conversation_counts.get(row["id"], 0)})
        return {"revision": self.revision(db), "profiles": profiles,
                "recognition": self.recognition(db, gallery=gallery)[0]}

    @staticmethod
    def invalidate_scope(db, scope_key: str) -> None:
        # Meeting/source projections may refer to the same audio under different scope IDs.
        db.execute("DELETE FROM voice_examples WHERE scope_key=? OR id IN ("
                   "SELECT v.example_id FROM voice_example_sources v "
                   "JOIN speaker_dependencies s ON s.audio_id=v.audio_id WHERE s.scope_key=?)",
                   (scope_key, scope_key))
        db.execute("DELETE FROM voice_vectors WHERE scope_key=? OR scope_key IN ("
                   "SELECT shared.scope_key FROM speaker_dependencies shared "
                   "JOIN speaker_dependencies current ON shared.audio_id=current.audio_id "
                   "WHERE current.scope_key=?)", (scope_key, scope_key))

    def save_vectors(self, db, scope: dict, payload: dict, result: dict) -> None:
        db.execute("DELETE FROM voice_vectors WHERE scope_key=?", (scope["key"],))
        if not self.memory.settings.voice_recognition_enabled:
            return
        model, voices = compatible_voices(result)
        if not model:
            return
        labels = {s.get("voice_key") for s in payload["speakers"]}
        if not set(voices).issubset(labels):
            return
        start, end = map(datetime.fromisoformat, scope["bounds"])
        duration = sum(max(0, (min(end, datetime.fromisoformat(r["ended_at"]))
                               - max(start, datetime.fromisoformat(r["started_at"])))
                           .total_seconds()) for r in scope["rows"])
        for speaker in payload["speakers"]:
            voice = voices.get(speaker.get("voice_key"))
            if voice and good_quality(voice) and voice["clean_seconds"] <= duration + 0.05:
                db.execute("INSERT INTO voice_vectors VALUES(?,?,?,?,?)", (
                    scope["key"], speaker["id"], json.dumps(model),
                    json.dumps(voice["embedding"]), json.dumps({
                        k: v for k, v in voice.items() if k not in {"embedding", "speaker"}
                    }),
                ))

    def annotate(self, db, scope: dict, payload: dict, ready: bool) -> dict:
        from .voice_matching import match_voice

        gallery = self.gallery(db) if self.memory.settings.voice_recognition_enabled else []
        recognition, report = self.recognition(db, gallery=gallery)
        models = {}
        if gallery:
            models[json.dumps(gallery[0]["model"], sort_keys=True)] = (recognition, report)
        names = {r["id"]: r["name"] for r in db.execute("SELECT * FROM voice_profiles")}
        for speaker in payload["speakers"]:
            speaker.pop("voice_key", None)
            speaker.update(voice_profile_id=None, remembered_example_id=None, suggestion=None)
            if not ready:
                continue
            learned = db.execute(
                "SELECT id,profile_id FROM voice_examples WHERE scope_key=? AND speaker_id=?",
                (scope["key"], speaker["id"]),
            ).fetchone()
            if learned:
                speaker.update(voice_profile_id=learned["profile_id"],
                               remembered_example_id=learned["id"])
            if speaker["name"] is not None or learned or recognition["state"] != "ready":
                continue
            vector = db.execute(
                "SELECT * FROM voice_vectors WHERE scope_key=? AND speaker_id=?",
                (scope["key"], speaker["id"]),
            ).fetchone()
            if not vector:
                continue
            model = json.loads(vector["model"])
            model_key = json.dumps(model, sort_keys=True)
            if model_key not in models:
                models[model_key] = self.recognition(db, model, gallery)
            state, report = models[model_key]
            if not report:
                recognition = state
                continue
            # A conversation's own confirmed examples must never identify its other clusters.
            sources = {row["id"] for row in scope["rows"]}
            candidates = [e for e in gallery if e["conversation_id"] != scope["key"]
                          and not sources.intersection(e["source_ids"])]
            result = match_voice(json.loads(vector["embedding"]), candidates,
                                 report["threshold"], report["margin"])
            if result and result["profile_id"] in names:
                speaker["suggestion"] = {"profile_id": result["profile_id"],
                                         "name": names[result["profile_id"]]}
        return recognition

    def forget(self, profile_id: str, revision: str, example_id: str | None = None) -> dict:
        with self.memory.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.revision(db) != revision:
                raise HTTPException(409, "Saved voices changed; reload and retry")
            if example_id:
                deleted = db.execute("DELETE FROM voice_examples WHERE id=? AND profile_id=?",
                                     (example_id, profile_id)).rowcount
            else:
                deleted = db.execute("DELETE FROM voice_profiles WHERE id=?",
                                     (profile_id,)).rowcount
            if not deleted:
                raise HTTPException(404, "Saved voice or example not found")
            return self.directory(db)

    def save_example(
        self, db, scope: dict, payload: dict, speaker_id: str, profile_id: str | None,
        name: str | None, model: dict, voice: dict,
        source_ids: list[str],
    ) -> str:
        if profile_id is None:
            if db.execute("SELECT count(*) FROM voice_profiles").fetchone()[0] >= MAX_PROFILES:
                raise HTTPException(409, "Forget an unused voice profile before adding another")
            profile_id = str(uuid4())
            db.execute("INSERT INTO voice_profiles VALUES(?,?,?)",
                       (profile_id, name, stamp(utc_now())))
        profile = db.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        if not profile:
            raise HTTPException(404, "Saved voice not found")
        old = db.execute("SELECT id FROM voice_examples WHERE scope_key=? AND speaker_id=?",
                         (scope["key"], speaker_id)).fetchone()
        if old:
            db.execute("DELETE FROM voice_examples WHERE id=?", (old["id"],))
        if db.execute("SELECT count(*) FROM voice_examples WHERE profile_id=?",
                      (profile_id,)).fetchone()[0] >= MAX_EXAMPLES:
            raise HTTPException(
                409, "Remove an older example before adding another for this person",
            )
        example_id = str(uuid4())
        source_rows = [r for r in scope["rows"] if r["id"] in source_ids]
        db.execute("INSERT INTO voice_examples VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            example_id, profile_id, scope["key"], speaker_id, json.dumps(voice["embedding"]),
            json.dumps(model), voice["clean_seconds"], json.dumps([r["id"] for r in source_rows]),
            json.dumps([r["digest"] for r in source_rows]),
            fingerprint(scope), turn_signature(payload), stamp(utc_now()),
        ))
        db.executemany("INSERT INTO voice_example_sources VALUES(?,?)", [
            (example_id, row["id"]) for row in source_rows
        ])
        db.execute("INSERT INTO voice_events VALUES(?,?,?,?,?)", (
            str(uuid4()), profile_id, example_id, "confirmed", stamp(utc_now()),
        ))
        for speaker in payload["speakers"]:
            if speaker["id"] == speaker_id:
                speaker["name"] = profile["name"]
        return example_id
