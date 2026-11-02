"""Automatic speaker work and source-local evidence projection."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_speakers import detector, recordings, wav

from omi_local.api import create_app
from omi_local.domain import utc_now
from omi_local.settings import Settings
from omi_local.speaker_projection import attach_text, project_utterances
from omi_local.speaker_worker import discover, process_one
from omi_local.store import Store


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                worker_enabled=False,
                diarization_enabled=True,
                _env_file=None,
            )
        )
    ) as client:
        yield client


def test_completed_upload_is_automatically_diarized_without_manual_post(client):
    keys, path = recordings(client)
    calls = detector(client)

    assert client.get(path).json()["state"] == "idle"
    assert discover(client.app.state.memory) == 1
    assert asyncio.run(process_one(client.app.state.memory))

    value = client.get(path).json()
    assert value["state"] == "ready"
    assert value["source_count"] == 2
    assert len(value["speakers"]) == 2
    assert calls == [64000]
    assert client.get(f"/v1/speakers/source/{keys[1]}").json() == value


def test_projection_keeps_unaligned_words_and_marks_turn_crossings_unknown(client):
    keys, path = recordings(client)
    store = client.app.state.memory.store
    for key in keys:
        store.complete(
            key,
            "alpha beta gamma",
            segments=[
                {
                    "start": 0.1,
                    "end": 0.3,
                    "text": "alpha",
                    "start_offset": 0,
                    "end_offset": 5,
                    "kind": "word",
                },
                {
                    "start": 0.9,
                    "end": 1.1,
                    "text": " beta",
                    "start_offset": 5,
                    "end_offset": 10,
                    "kind": "word",
                },
            ],
        )

    async def detect(data, num_speakers=None):
        return {
            "turns": [
                {"start": 0, "end": 1, "speaker": "a"},
                {"start": 1, "end": 4, "speaker": "b"},
            ]
        }

    client.app.state.memory.speakers.detect_turns = detect
    value = client.post(path).json()
    pieces = [p for p in value["utterances"] if p["source_id"] == keys[0]]
    assert "".join(p["text"] for p in pieces) == "alpha beta gamma"
    assert pieces[0]["speaker_id"] is not None
    assert pieces[1]["speaker_id"] is None
    assert pieces[1]["attribution"] == "unknown"
    assert pieces[-1]["text"] == " beta gamma"
    assert pieces[-1]["attribution"] == "unknown"
    assert [(piece["start_offset"], piece["end_offset"]) for piece in pieces] == [(0, 5), (5, 16)]


def test_automatic_failure_is_visible_and_backed_off(client):
    _, path = recordings(client)

    async def unavailable(data, num_speakers=None):
        raise HTTPException(502, "Local service unavailable")

    memory = client.app.state.memory
    memory.speakers.detect_turns = unavailable
    assert discover(memory) == 1
    assert asyncio.run(process_one(memory))
    status = client.get(path).json()
    assert status["state"] == "idle"
    assert status["processing"]["state"] == "error"
    assert status["processing"]["error"] == "Speaker detection failed: Local service unavailable"
    assert status["processing"]["retry_at"] is not None
    with memory.store.connect() as db:
        job = db.execute("SELECT attempts FROM speaker_jobs").fetchone()
    assert job["attempts"] == 1


def test_disabled_diarization_does_not_discover_automatic_jobs(client):
    memory = client.app.state.memory
    memory.settings.diarization_enabled = False
    recordings(client)
    assert discover(memory) == 0
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM speaker_jobs").fetchone()[0] == 0
    assert client.app.state.profiles.runtimes["default"].worker is None


def test_background_processing_disabled_does_not_start_speaker_worker(tmp_path):
    with TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                worker_enabled=True,
                background_processing_enabled=False,
                diarization_enabled=True,
                _env_file=None,
            )
        )
    ) as client:
        assert client.app.state.profiles.runtimes["default"].worker is None


def test_restart_requeues_processing_job_only_after_enable_and_cancellation_is_durable(
    client, monkeypatch
):
    import omi_local.speaker_worker as speaker_worker

    recordings(client)
    memory = client.app.state.memory
    assert discover(memory) == 1
    with memory.store.connect() as db:
        db.execute("UPDATE speaker_jobs SET state='processing'")
    memory.settings.diarization_enabled = False

    async def run_disabled_then_enable():
        worker = asyncio.create_task(speaker_worker.run_speakers(memory))
        await asyncio.sleep(0.01)
        with memory.store.connect() as db:
            assert db.execute("SELECT state FROM speaker_jobs").fetchone()["state"] == "processing"
        memory.settings.diarization_enabled = True
        observed = asyncio.Event()

        async def cancel_after_recovery(_memory):
            with memory.store.connect() as db:
                assert db.execute("SELECT state FROM speaker_jobs").fetchone()["state"] == "queued"
            observed.set()
            raise asyncio.CancelledError

        monkeypatch.setattr(speaker_worker, "process_one", cancel_after_recovery)
        await asyncio.wait_for(observed.wait(), timeout=1)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    asyncio.run(run_disabled_then_enable())
    with memory.store.connect() as db:
        assert db.execute("SELECT state FROM speaker_jobs").fetchone()["state"] == "queued"
        assert db.execute("SELECT attempts FROM speaker_jobs").fetchone()["attempts"] == 0


def test_multipart_waits_until_every_resolved_part_is_complete(client):
    keys, _ = recordings(client)
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute("UPDATE audio SET state='transcribing' WHERE id=?", (keys[1],))
    memory = client.app.state.memory
    assert discover(memory) == 0
    with store.connect() as db:
        db.execute("UPDATE audio SET state='done' WHERE id=?", (keys[1],))
    assert discover(memory) == 1


def test_resolve_loads_transcripts_only_for_its_projected_scope(client):
    keys, _ = recordings(client)
    store = client.app.state.memory.store
    other = str(uuid4())
    store.ingest(other, "manual-web", utc_now() - timedelta(minutes=2), wav(), 2,
                 source_kind="manual_upload", source_id="unrelated.wav")
    store.complete(other, "Private unrelated transcript")
    statements = []
    with store.connect() as db:
        db.set_trace_callback(statements.append)
        scope = client.app.state.memory.speakers.resolve("source", keys[0], db)
    reads = [sql for sql in statements if "SELECT id,text,segments FROM audio WHERE id IN" in sql]
    assert len(reads) == 1
    assert all(ident in scope["key"] or ident in reads[0] for ident in keys)
    assert other not in reads[0]


def test_automatic_discovery_never_replaces_reviewed_names(client):
    _, path = recordings(client)
    detector(client)
    named = client.post(path).json()
    speaker = named["speakers"][0]
    response = client.patch(
        f"{path}/{speaker['id']}",
        json={
            "name": "Reviewed",
            "version": named["version"],
        },
    )
    assert response.status_code == 200
    assert discover(client.app.state.memory) == 0
    assert client.get(path).json()["speakers"][0]["name"] == "Reviewed"


@pytest.mark.parametrize("action", ["correct", "delete"])
def test_automatic_detection_correction_and_deletion_races_never_restore_scope(client, action):
    keys, _ = recordings(client, filename=f"{action}.wav")
    memory = client.app.state.memory

    async def mutate_during_detection(data, num_speakers=None):
        if action == "correct":
            memory.store.correct_transcript(keys[0], "Changed transcript")
        else:
            memory.store.delete_audio(keys[0])
        return {"turns": [{"start": 0, "end": 2, "speaker": "a"}]}

    memory.speakers.detect_turns = mutate_during_detection
    assert discover(memory) == 1
    assert asyncio.run(process_one(memory))
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM speaker_scopes").fetchone()[0] == 0


def test_existing_speaker_trigger_upgrade_preserves_other_pending_jobs(tmp_path):
    store = Store(tmp_path)
    root = store.root
    with store.connect() as db:
        db.executescript("""
            DROP TRIGGER speakers_delete;
            DROP TRIGGER speakers_expire;
            CREATE TRIGGER speakers_delete AFTER DELETE ON audio BEGIN
              DELETE FROM speaker_events WHERE scope_key IN
                (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
              DELETE FROM speaker_scopes WHERE scope_key IN
                (SELECT scope_key FROM speaker_dependencies WHERE audio_id=OLD.id);
              DELETE FROM speaker_dependencies WHERE scope_key NOT IN
                (SELECT scope_key FROM speaker_scopes);
            END;
            CREATE TRIGGER speakers_expire AFTER UPDATE OF state ON audio
            WHEN NEW.state='expired' BEGIN
              DELETE FROM speaker_events WHERE scope_key IN
                (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
              DELETE FROM speaker_scopes WHERE scope_key IN
                (SELECT scope_key FROM speaker_dependencies WHERE audio_id=NEW.id);
              DELETE FROM speaker_dependencies WHERE scope_key NOT IN
                (SELECT scope_key FROM speaker_scopes);
            END;
        """)

    # Reopening an archive must replace trigger bodies, not keep IF NOT EXISTS
    # definitions installed by the previous release.
    store = Store(root)
    ids = [str(uuid4()), str(uuid4())]
    for ident in ids:
        store.ingest(
            ident,
            "manual-web",
            utc_now() - timedelta(minutes=1),
            wav(),
            2,
            source_kind="manual_upload",
            source_id=f"{ident}.wav",
        )
        store.complete(ident, "hello")
    with store.connect() as db:
        for index, ident in enumerate(ids):
            scope = f"source:{ident}"
            state = "queued" if index == 0 else "processing"
            db.execute(
                "INSERT INTO speaker_jobs(scope_key,fingerprint,state,updated_at) VALUES(?,?,?,?)",
                (scope, "fp", state, "now"),
            )
            db.execute("INSERT INTO speaker_dependencies VALUES(?,?)", (scope, ident))
        db.execute("DELETE FROM audio WHERE id=?", (ids[0],))
        assert (
            db.execute(
                "SELECT count(*) FROM speaker_jobs WHERE scope_key=?", (f"source:{ids[1]}",)
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT count(*) FROM speaker_dependencies WHERE audio_id=?", (ids[1],)
            ).fetchone()[0]
            == 1
        )
        db.execute("UPDATE audio SET state='expired' WHERE id=?", (ids[1],))
        assert (
            db.execute(
                "SELECT count(*) FROM speaker_jobs WHERE scope_key=?", (f"source:{ids[1]}",)
            ).fetchone()[0]
            == 0
        )


def test_projection_rejects_malformed_timing_and_offsets_without_losing_canonical_text():
    row = {
        "id": "source",
        "text": "echo echo tail",
        "segments": "not-json",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:02+00:00",
    }
    attach_text([], [row])
    pieces = project_utterances([], [row], True, [])
    assert len(pieces) == 1
    assert pieces[0]["text"] == row["text"]
    assert (pieces[0]["start_offset"], pieces[0]["end_offset"]) == (0, len(row["text"]))

    row["segments"] = json.dumps(
        [
            {"start": float("nan"), "end": 0.4, "start_offset": 0, "end_offset": 4, "text": "echo"},
            {"start": -1, "end": 0.3, "start_offset": 5, "end_offset": 9, "text": "echo"},
            {"start": 0.4, "end": 1.0, "start_offset": 10, "end_offset": 14, "text": "tail"},
        ]
    )
    pieces = project_utterances([], [row], True, [])
    assert "".join(piece["text"] for piece in pieces) == row["text"]
    assert all(piece["speaker_id"] is None for piece in pieces)
    assert all(piece["start"] is None and piece["end"] is None for piece in pieces)
    assert all("start_offset" in piece and "end_offset" in piece for piece in pieces)

    row["segments"] = json.dumps([
        {"start": 1.0, "end": 1.2, "start_offset": 0, "end_offset": 4, "text": "echo"},
        {"start": 0.3, "end": 0.5, "start_offset": 5, "end_offset": 9, "text": "echo"},
        {"start": 0.4, "end": 1.0, "start_offset": 10, "end_offset": 14, "text": "tail"},
    ])
    pieces = project_utterances([], [row], True, [])
    assert "".join(piece["text"] for piece in pieces) == row["text"]
    assert all(piece["speaker_id"] is None for piece in pieces)

    row["text"] = ""
    row["segments"] = "[]"
    assert project_utterances([], [row], True, []) == []


def test_projection_skips_overlapping_char_anchors_and_marks_voice_overlap_unknown():
    row = {
        "id": "source",
        "text": "echo echo tail",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:02+00:00",
        "segments": json.dumps(
            [
                {"start": 0.1, "end": 0.5, "start_offset": 0, "end_offset": 4, "text": "echo"},
                {"start": 0.2, "end": 0.6, "start_offset": 3, "end_offset": 9, "text": "o echo"},
                {
                    "start": 0.3,
                    "end": 0.4,
                    "start_offset": True,
                    "end_offset": 14,
                    "text": "echo echo tail",
                },
            ]
        ),
    }
    turns = [
        {"source_id": "source", "start": 0, "end": 0.35, "speaker_id": "a"},
        {"source_id": "source", "start": 0.3, "end": 0.8, "speaker_id": "b"},
    ]
    pieces = project_utterances(
        turns,
        [row],
        True,
        [{"id": "a", "label": "A", "name": None}, {"id": "b", "label": "B", "name": None}],
    )
    assert "".join(piece["text"] for piece in pieces) == row["text"]
    assert pieces[0]["attribution"] == "overlap"
    assert all(piece["speaker_id"] is None for piece in pieces)
    assert all("start_offset" in piece and "end_offset" in piece for piece in pieces)


def test_projection_coalesces_same_speaker_words_and_safe_punctuation_gap():
    row = {
        "id": "source",
        "text": "good, morning",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:02+00:00",
        "segments": json.dumps(
            [
                {
                    "start": 0.1,
                    "end": 0.3,
                    "start_offset": 0,
                    "end_offset": 4,
                    "text": "good",
                    "kind": "word",
                },
                {
                    "start": 0.3,
                    "end": 0.5,
                    "start_offset": 6,
                    "end_offset": 13,
                    "text": "morning",
                    "kind": "word",
                },
            ]
        ),
    }
    turns = [{"source_id": "source", "start": 0, "end": 1, "speaker_id": "s"}]
    pieces = project_utterances(turns, [row], True, [{"id": "s", "label": "A", "name": None}])
    assert len(pieces) == 1
    assert pieces[0]["text"] == "good, morning"
    assert pieces[0]["speaker_label"] == "A"
    assert (pieces[0]["start_offset"], pieces[0]["end_offset"]) == (0, 13)
