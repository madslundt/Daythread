"""The full-file alignment toggle is independent from ordinary STT alignment."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from recording_transcription_helpers import pinned_settings

from omi_local.merged_recordings import load_recording
from omi_local.recording_transcription_store import pipeline_fingerprint, source_fingerprint
from omi_local.recording_transcription_worker import process_one
from omi_local.settings import Settings


def test_full_recording_alignment_setting_defaults_off_and_is_localized(app_recording):
    client, _, _, _, _, _ = app_recording
    assert Settings(_env_file=None).recording_transcription_alignment_enabled is False
    view = client.get("/v1/configuration").json()
    assert view["values"]["recording_transcription_alignment_enabled"] is False
    field = next(
        item for item in view["fields"]
        if item["key"] == "recording_transcription_alignment_enabled"
    )
    assert field["label"] == "Align whole-recording transcript"
    assert "independent" in field["description"].lower()
    assert field["restart_required"] is True

    locales = Path(__file__).parents[1] / "src/web/src/locales"
    catalogs = list(locales.glob("*.json"))
    assert {path.stem for path in catalogs} == {"en", "da", "de", "fr", "es", "nb", "sv"}
    for path in catalogs:
        assert json.loads(path.read_text())[field["label"]]


@pytest.mark.parametrize(
    ("chunk_alignment", "recording_alignment", "expected_calls"),
    [(False, True, 1), (True, False, 0)],
)
def test_recording_worker_uses_only_its_own_alignment_switch(
    app_recording, monkeypatch, chunk_alignment, recording_alignment, expected_calls
):
    client, store, rid, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    settings = memory.settings
    settings.alignment_enabled = chunk_alignment
    settings.recording_transcription_alignment_enabled = recording_alignment
    recording = load_recording(store, rid)
    assert store.enqueue_recording_transcription(
        rid, source_fingerprint(recording), pipeline_fingerprint(settings), len(recording.rows)
    ) == 1
    calls = []

    async def fake_request(_settings, _path):
        text = "alpha beta"
        return (
            {
                "x-homecompute-stt-model-sha256": "a" * 64,
                "x-homecompute-stt-model-snapshot": "model-r1",
                "x-homecompute-stt-runtime-commit": "1234567890ab",
                "x-homecompute-stt-runtime": "whisper.cpp-r1",
            },
            {"text": text, "segments": [{"start": 0.0, "end": 1.0, "text": text}]},
        )

    async def fake_align(parts, passed_settings, budget):
        calls.append((parts, passed_settings, budget))
        return (
            [
                {"text": "alpha", "start": 0.1, "end": 0.4,
                 "start_offset": 0, "end_offset": 5, "kind": "word"},
                {"text": "beta", "start": 0.5, "end": 0.8,
                 "start_offset": 6, "end_offset": 10, "kind": "word"},
            ],
            {"status": "aligned", "model": "alignment-r1"},
        )

    monkeypatch.setattr("omi_local.recording_transcription_worker._request", fake_request)
    monkeypatch.setattr(memory.inference, "_align_chunks", fake_align)
    assert asyncio.run(process_one(memory))
    assert len(calls) == expected_calls
    with store.connect() as db:
        row = db.execute(
            "SELECT alignment_status,word_segments FROM recording_transcription_jobs "
            "WHERE recording_id=?",
            (rid,),
        ).fetchone()
    if recording_alignment:
        parts, passed_settings, budget = calls[0]
        assert passed_settings is settings and budget == settings.alignment_timeout_seconds
        assert len(parts) == 1 and parts[0][2] == "alpha beta"
        assert parts[0][5][0]["kind"] == "segment"
        assert row["alignment_status"] == "aligned"
        assert [word["text"] for word in json.loads(row["word_segments"])] == ["alpha", "beta"]
    else:
        assert row["alignment_status"] == "disabled"
        assert json.loads(row["word_segments"]) == []


def test_recording_fingerprint_v3_uses_independent_alignment_switches():
    settings = Settings(
        recording_transcription_expected_model_sha256="a" * 64,
        recording_transcription_expected_model_snapshot="model-r1",
        recording_transcription_expected_runtime_commit="1234567890ab",
        recording_transcription_alignment_enabled=True,
        alignment_enabled=False,
        _env_file=None,
    )
    legacy_values = {
        "pipeline": "whisper-cpp-full-recording-v3-cli-beam5-maxlen-unlimited",
        "endpoint": settings.recording_transcription_base_url,
        "model": "whisper",
        "model_sha256": settings.recording_transcription_expected_model_sha256,
        "model_snapshot": settings.recording_transcription_expected_model_snapshot,
        "runtime_commit": settings.recording_transcription_expected_runtime_commit,
        "decoding": {
            "model": "whisper", "response_format": "verbose_json", "language": "auto",
            "max_context": "0", "max_len": "-1", "beam_size": "5", "best_of": "5",
            "temperature": "0",
            "temperature_inc": "0.2", "carry_initial_prompt": "false", "translate": "false",
            "no_timestamps": "false",
        },
        "prompt": None,
        "timeout_seconds": settings.recording_transcription_timeout_seconds,
        "max_seconds": settings.recording_transcription_max_seconds,
        "max_bytes": settings.recording_transcription_max_bytes,
        "alignment_enabled": True,
        "alignment_base_url": settings.alignment_base_url,
        "alignment_language": settings.alignment_language,
        "alignment_timeout_seconds": settings.alignment_timeout_seconds,
    }
    old_effective_fingerprint = hashlib.sha256(
        json.dumps(legacy_values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert pipeline_fingerprint(settings) == old_effective_fingerprint

    enabled_fingerprint = pipeline_fingerprint(settings)
    settings.alignment_enabled = True
    assert pipeline_fingerprint(settings) == enabled_fingerprint
    settings.recording_transcription_alignment_enabled = False
    assert pipeline_fingerprint(settings) != enabled_fingerprint
