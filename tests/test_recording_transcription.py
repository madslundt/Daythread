import asyncio
import io
import json
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp, utc_now
from omi_local.merged_recordings import load_recording
from omi_local.recording_transcription_client import _validate_response
from omi_local.recording_transcription_store import (
    pipeline_fingerprint,
    project_words,
    source_fingerprint,
)
from omi_local.recording_transcription_worker import _claim, discover, process_one, recover
from omi_local.settings import Settings


@pytest.fixture
def app_recording(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, timezone="UTC", _env_file=None)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.memory.store
        rid, at = str(uuid4()), datetime(2026, 10, 20, 12, tzinfo=UTC)
        ids, texts, pcm_parts = [], [], []
        for index, text in enumerate(("First part.", "Second part.")):
            source_id = str(uuid4())
            pcm = bytes((index + 1, 0)) * 32000
            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
                wav.writeframes(pcm)
            store.ingest(
                source_id,
                "manual-web",
                at + timedelta(seconds=index * 2),
                audio.getvalue(),
                2,
                source_kind="manual_upload",
                source_id="recording.wav",
                source_metadata={"recording_id": rid, "part_index": index, "part_count": 2},
            )
            store.complete(source_id, text)
            ids.append(source_id)
            texts.append(text)
            pcm_parts.append(pcm)
        yield client, store, rid, ids, texts, b"".join(pcm_parts)


def pinned_settings(memory):
    memory.settings.recording_transcription_expected_model_sha256 = "a" * 64
    memory.settings.recording_transcription_expected_model_snapshot = "model-r1"
    memory.settings.recording_transcription_expected_runtime_commit = "1234567890ab"
    memory.settings.recording_transcription_enabled = True


def test_feature_is_opt_in_and_requires_pinned_local_sidecar():
    assert not Settings(_env_file=None).recording_transcription_enabled
    with pytest.raises(ValueError, match="pinned local model/runtime provenance"):
        Settings(recording_transcription_enabled=True, _env_file=None)
    with pytest.raises(ValueError):
        Settings(
            recording_transcription_enabled=True,
            recording_transcription_base_url="https://example.com/v1",
            recording_transcription_expected_model_sha256="a" * 64,
            recording_transcription_expected_model_snapshot="model-r1",
            recording_transcription_expected_runtime_commit="1234567890ab",
            _env_file=None,
        )


def test_response_provenance_is_checked_against_exact_pinned_values(app_recording):
    client, _, _, _, _, _ = app_recording
    settings = client.app.state.memory.settings
    pinned_settings(client.app.state.memory)
    headers = {
        "x-homecompute-stt-model-sha256": "a" * 64,
        "x-homecompute-stt-model-snapshot": "model-r1",
        "x-homecompute-stt-runtime-commit": "1234567890ab",
        "x-homecompute-stt-runtime": "whisper.cpp-r1",
    }
    text = "Words."
    result = _validate_response(
        headers,
        {"text": text, "segments": [{"start": 0, "end": 1, "text": text}]},
        settings,
        1,
    )
    assert result[0] == text and result[1][0]["start_offset"] == 0
    with pytest.raises(ValueError, match="provenance_mismatch"):
        _validate_response(
            headers | {"x-homecompute-stt-model-sha256": "b" * 64},
            {"text": text, "segments": []},
            settings,
            1,
        )


def test_projection_keeps_unknown_and_boundary_words_exactly_once():
    text = "red edge blue"
    parts = [
        {"source_id": "a", "start": 0.0, "end": 1.0},
        {"source_id": "b", "start": 1.0, "end": 2.0},
    ]
    words = [
        {"text": "red", "start_offset": 0, "end_offset": 3, "start": 0.1, "end": 0.4},
        {"text": "edge", "start_offset": 4, "end_offset": 8, "start": 0.9, "end": 1.1},
        {"text": "blue", "start_offset": 9, "end_offset": 13, "start": 1.2, "end": 1.5},
    ]
    projection = project_words(text, words, parts)
    assert "".join(item["text"] for item in projection) == text
    assert [item["source_id"] for item in projection if item["kind"] != "unknown"] == [
        "a",
        None,
        "b",
    ]
    assert projection[2]["candidate_source_ids"] == ["a", "b"]


@pytest.mark.parametrize("start,end", [(True, 1.0), (-0.1, 0.2), (float("nan"), 0.2)])
def test_projection_rejects_invalid_times_but_preserves_unattributed_text(start, end):
    text = "word"
    projection = project_words(
        text,
        [
            {
                "text": text,
                "start_offset": 0,
                "end_offset": 4,
                "start": start,
                "end": end,
            }
        ],
        [{"source_id": "a", "start": 0.0, "end": 1.0}],
    )
    assert projection == [
        {
            "text": text,
            "start_offset": 0,
            "end_offset": 4,
            "start": None,
            "end": None,
            "source_id": None,
            "source_start": None,
            "source_end": None,
            "candidate_source_ids": [],
            "kind": "unknown",
        }
    ]


def test_idle_variant_is_explicit_and_does_not_rewrite_child_transcripts(app_recording):
    client, store, rid, ids, texts, _ = app_recording
    with store.connect() as db:
        before = [
            db.execute("SELECT text FROM audio WHERE id=?", (key,)).fetchone()[0] for key in ids
        ]
    body = client.get(f"/v1/recordings/{rid}").json()
    assert body["text"] == "\n".join(texts)
    assert body["recording_transcription"]["state"] == "disabled"
    with store.connect() as db:
        after = [
            db.execute("SELECT text FROM audio WHERE id=?", (key,)).fetchone()[0] for key in ids
        ]
    assert after == before


def test_worker_sends_one_exact_original_pcm_wave_and_publishes_separate_variant(
    app_recording,
    monkeypatch,
):
    client, store, rid, ids, texts, pcm = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    recording = load_recording(store, rid)
    source = source_fingerprint(recording)
    pipeline = pipeline_fingerprint(memory.settings)
    assert store.enqueue_recording_transcription(rid, source, pipeline, len(ids)) == 1
    seen = {}

    async def fake_request(settings, path):
        raw = path.read_bytes()
        seen["wave"] = raw
        headers = {
            "x-homecompute-stt-model-sha256": "a" * 64,
            "x-homecompute-stt-model-snapshot": "model-r1",
            "x-homecompute-stt-runtime-commit": "1234567890ab",
            "x-homecompute-stt-runtime": "whisper.cpp-r1",
        }
        transcript = "First part.\nSecond part."
        return headers, {
            "text": transcript,
            "segments": [
                {"start": 0.0, "end": 4.0, "text": transcript},
            ],
        }

    monkeypatch.setattr("omi_local.recording_transcription_worker._request", fake_request)
    assert asyncio.run(process_one(memory))
    assert wave.open(io.BytesIO(seen["wave"])).getnframes() == 64000
    assert seen["wave"][44:] == pcm
    with store.connect() as db:
        row = db.execute(
            "SELECT * FROM recording_transcription_jobs WHERE recording_id=?", (rid,)
        ).fetchone()
        assert row["state"] == "ready"
        assert row["transcript"] == "First part.\nSecond part."
        assert json.loads(row["native_segments"])[0]["kind"] == "segment"
        assert row["result_version"]
        assert not list((store.root / ".recording-transcription-spool").glob("*.wav"))
        assert (
            db.execute("SELECT count(*) FROM transcript_search WHERE id IN (?,?)", ids).fetchone()[
                0
            ]
            == 2
        )
    result = client.get(f"/v1/recordings/{rid}").json()
    variant = result["recording_transcription"]
    assert variant["state"] == "ready"
    assert variant["source_ids"] == ids
    assert result["text"] == "\n".join(texts)


def test_incomplete_recording_is_not_scheduled_and_restart_recovers_claim(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    store.delete_audio(ids[1])
    assert discover(memory) == 0
    # A previously claimed job is restart-safe and is requeued without losing its version.
    store.enqueue_recording_transcription(rid, "x" * 64, "y" * 64, 2)
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET state='processing' WHERE recording_id=?",
            (rid,),
        )
    recover(store)
    with store.connect() as db:
        row = db.execute(
            "SELECT state FROM recording_transcription_jobs WHERE recording_id=?", (rid,)
        ).fetchone()
    assert row["state"] == "queued"


@pytest.mark.parametrize("mutation", ["correct", "delete"])
def test_input_change_during_inference_cannot_publish_or_restore_variant(
    app_recording,
    monkeypatch,
    mutation,
):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    recording = load_recording(store, rid)
    source = source_fingerprint(recording)
    pipeline = pipeline_fingerprint(memory.settings)
    store.enqueue_recording_transcription(rid, source, pipeline, len(ids))

    async def mutate_then_return(settings, path):
        if mutation == "correct":
            store.correct_transcript(ids[0], "Reviewed correction.")
        else:
            store.delete_audio(ids[0])
        text = "first second"
        return (
            {
                "x-homecompute-stt-model-sha256": "a" * 64,
                "x-homecompute-stt-model-snapshot": "model-r1",
                "x-homecompute-stt-runtime-commit": "1234567890ab",
                "x-homecompute-stt-runtime": "whisper.cpp-r1",
            },
            {"text": text, "segments": [{"start": 0, "end": 1, "text": text}]},
        )

    monkeypatch.setattr(
        "omi_local.recording_transcription_worker._request",
        mutate_then_return,
    )
    assert asyncio.run(process_one(memory))
    with store.connect() as db:
        row = db.execute(
            "SELECT state,result_version FROM recording_transcription_jobs WHERE recording_id=?",
            (rid,),
        ).fetchone()
    if mutation == "correct":
        assert row["state"] == "stale" and row["result_version"] is None
    else:
        assert row is None


def test_audio_only_expiry_keeps_variant_but_transcript_expiry_purges_it(
    app_recording, monkeypatch
):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    recording = load_recording(store, rid)
    store.enqueue_recording_transcription(
        rid,
        source_fingerprint(recording),
        pipeline_fingerprint(memory.settings),
        len(ids),
    )

    async def fake_request(settings, path):
        text = "Finished full transcript."
        return (
            {
                "x-homecompute-stt-model-sha256": "a" * 64,
                "x-homecompute-stt-model-snapshot": "model-r1",
                "x-homecompute-stt-runtime-commit": "1234567890ab",
                "x-homecompute-stt-runtime": "whisper.cpp-r1",
            },
            {"text": text, "segments": [{"start": 0, "end": 4, "text": text}]},
        )

    monkeypatch.setattr("omi_local.recording_transcription_worker._request", fake_request)
    assert asyncio.run(process_one(memory))
    expiry = datetime(2026, 11, 3, tzinfo=UTC)
    store.apply_retention(1, 0, now=expiry)
    variant = client.get(f"/v1/recordings/{rid}").json()["recording_transcription"]
    assert variant["state"] == "ready" and not variant["audio_available"]
    store.apply_retention(0, 1, now=expiry)
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT 1 FROM recording_transcription_jobs WHERE recording_id=?",
                (rid,),
            ).fetchone()
            is None
        )


def test_claim_prefers_newest_capture_but_skips_future_retry_and_then_claims_older(
    app_recording,
):
    client, store, older_id, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    older = load_recording(store, older_id)
    newer_id, retry_id = str(uuid4()), str(uuid4())
    starts = [datetime(2026, 10, 22, 12, tzinfo=UTC), datetime(2026, 10, 23, 12, tzinfo=UTC)]
    for recording_id, started in zip((newer_id, retry_id), starts):
        source_id = str(uuid4())
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
            wav.writeframes(b"\x01\x00" * 16000)
        store.ingest(
            source_id,
            "manual-web",
            started,
            audio.getvalue(),
            1,
            source_kind="manual_upload",
            source_id="new.wav",
            source_metadata={"recording_id": recording_id, "part_index": 0, "part_count": 1},
        )
        store.complete(source_id, "One part.")
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    pipeline = pipeline_fingerprint(memory.settings)
    for recording in (older, load_recording(store, newer_id), load_recording(store, retry_id)):
        store.enqueue_recording_transcription(
            recording.id,
            source_fingerprint(recording),
            pipeline,
            len(recording.rows),
            now,
        )
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET state='error',retry_at=? WHERE recording_id=?",
            (stamp(now + timedelta(hours=1)), retry_id),
        )
    first = _claim(store)
    assert first["recording_id"] == newer_id
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET state='ready' WHERE recording_id=?",
            (newer_id,),
        )
    second = _claim(store)
    assert second["recording_id"] == older_id
    with store.connect() as db:
        retry = db.execute(
            "SELECT state FROM recording_transcription_jobs WHERE recording_id=?", (retry_id,)
        ).fetchone()
    assert retry["state"] == "error"


def test_upload_cap_blocks_before_request_without_chunked_fallback(app_recording, monkeypatch):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    memory.settings.recording_transcription_max_bytes = 100
    recording = load_recording(store, rid)
    store.enqueue_recording_transcription(
        rid,
        source_fingerprint(recording),
        pipeline_fingerprint(memory.settings),
        len(ids),
    )
    called = False

    async def should_not_request(settings, path):
        nonlocal called
        called = True
        raise AssertionError("capped input must not be submitted or chunked")

    monkeypatch.setattr(
        "omi_local.recording_transcription_worker._request",
        should_not_request,
    )
    assert asyncio.run(process_one(memory))
    assert not called
    with store.connect() as db:
        row = db.execute(
            "SELECT state,error_code FROM recording_transcription_jobs WHERE recording_id=?",
            (rid,),
        ).fetchone()
    assert row["state"] == "blocked" and row["error_code"] == "byte_cap"


@pytest.mark.parametrize(
    "failure,expected_state",
    [
        (httpx.ReadTimeout("sidecar timeout"), "error"),
        (
            httpx.HTTPStatusError(
                "refused",
                request=httpx.Request("POST", "http://localhost"),
                response=httpx.Response(401),
            ),
            "blocked",
        ),
        (
            httpx.HTTPStatusError(
                "busy",
                request=httpx.Request("POST", "http://localhost"),
                response=httpx.Response(429),
            ),
            "error",
        ),
    ],
)
def test_timeout_backoff_and_provider_refusal_are_durable(
    app_recording,
    monkeypatch,
    failure,
    expected_state,
):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    recording = load_recording(store, rid)
    store.enqueue_recording_transcription(
        rid,
        source_fingerprint(recording),
        pipeline_fingerprint(memory.settings),
        len(ids),
    )

    async def raise_failure(settings, path):
        raise failure

    monkeypatch.setattr(
        "omi_local.recording_transcription_worker._request",
        raise_failure,
    )
    assert asyncio.run(process_one(memory))
    with store.connect() as db:
        row = db.execute(
            "SELECT state,retry_at,error_code FROM recording_transcription_jobs "
            "WHERE recording_id=?",
            (rid,),
        ).fetchone()
    assert row["state"] == expected_state
    if expected_state == "error":
        assert datetime.fromisoformat(row["retry_at"]) > utc_now()
        assert row["error_code"] == "provider_unavailable"
    else:
        assert row["retry_at"] is None and row["error_code"] == "provider_refused"
