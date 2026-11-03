import hashlib
import io
import json
import sqlite3
import wave
from dataclasses import replace

import pytest

from omi_local.merged_recordings import load_recording
from omi_local.recording_transcription_import import (
    ImportExpectations,
    ImportRefused,
    QualifiedVariant,
    profile_directory,
    publish_variant,
    read_qualified_variant,
    validate_variant,
)
from omi_local.recording_transcription_store import (
    SCHEMA,
    pipeline_fingerprint,
    recording_source_map,
    recording_transcription_result_version,
    source_fingerprint,
)


def _fixture_variant(app_recording, tmp_path):
    client, store, rid, ids, _texts, pcm = app_recording
    memory = client.app.state.memory
    memory.settings.recording_transcription_expected_model_sha256 = "a" * 64
    memory.settings.recording_transcription_expected_model_snapshot = "model-snapshot-r1"
    memory.settings.recording_transcription_expected_runtime_commit = "1234567890ab"
    recording = load_recording(store, rid)
    source = source_fingerprint(recording)
    pipeline = pipeline_fingerprint(memory.settings)
    text = "Hej verden.\nIgen."
    native = [
        {"start": 0.1, "end": 0.9, "text": "Hej verden.", "kind": "segment",
         "start_offset": 0, "end_offset": 11},
        {"start": 1.1, "end": 1.8, "text": "Igen.", "kind": "segment",
         "start_offset": 12, "end_offset": 17},
    ]
    words = [
        {"start": 0.12, "end": 0.3, "text": "Hej", "kind": "word",
         "start_offset": 0, "end_offset": 3},
        {"start": 0.4, "end": 0.8, "text": "verden", "kind": "word",
         "start_offset": 4, "end_offset": 10},
        {"start": 1.2, "end": 1.5, "text": "Igen", "kind": "word",
         "start_offset": 12, "end_offset": 16},
    ]
    provenance = {
        "model-sha256": "a" * 64,
        "model-snapshot": "model-snapshot-r1",
        "runtime": "whisper.cpp",
        "runtime-commit": "1234567890ab",
        "homecompute-revision": "b" * 40,
        "image-id": "sha256:" + "c" * 64,
    }
    version = recording_transcription_result_version(
        source, pipeline, text, native, words, provenance,
    )
    variant = QualifiedVariant(
        rid, source, pipeline, version, text, native, words, provenance,
        recording_source_map(recording), "partial", "roest-r1", len(ids),
    )
    expected = ImportExpectations(
        rid, version, hashlib.sha256(_wav(pcm)).hexdigest(), provenance["image-id"],
        provenance["homecompute-revision"], provenance["model-sha256"],
        provenance["model-snapshot"], provenance["runtime"], provenance["runtime-commit"],
        "roest-r1",
    )
    source_db = tmp_path / "source-copy.sqlite3"
    with sqlite3.connect(source_db) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO recording_transcription_jobs "
            "(recording_id,input_fingerprint,pipeline_fingerprint,state,total_parts,result_version,"
            "transcript,native_segments,word_segments,provenance,source_map,alignment_status,"
            "alignment_model,created_at,updated_at) VALUES(?,?,?,'ready',?,?,?,?,?,?,?,?,?,?,?)",
            (rid, source, pipeline, len(ids), version, text, json.dumps(native),
             json.dumps(words), json.dumps(provenance), json.dumps(variant.source_map),
             "partial", "roest-r1", "2026-11-03T00:00:00+00:00",
             "2026-11-03T00:00:00+00:00"),
        )
    return client, store, rid, ids, memory.settings, variant, expected, source_db


def _wav(pcm):
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        output.writeframes(pcm)
    return audio.getvalue()


def test_read_validates_ready_variant_and_import_is_idempotent(app_recording, tmp_path):
    client, store, rid, ids, settings, expected_variant, expected, source_db = (
        _fixture_variant(app_recording, tmp_path)
    )
    variant = read_qualified_variant(source_db, expected)
    with store.connect() as db:
        original = load_recording(store, rid, db)
        validate_variant(variant, original, settings, expected)
        result = publish_variant(db, store.audio_dir, variant, settings, expected)
    assert result["action"] == "imported"
    assert result["selection_changed"] is False
    assert result["child_sources_changed"] is False
    assert result["audio_sha256"] == expected.original_wav_sha256
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM recording_transcription_jobs WHERE recording_id=?", (rid,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["state"] == "ready"
        assert rows[0]["transcript"] == expected_variant.transcript
        assert json.loads(rows[0]["word_segments"]) == expected_variant.word_segments
        assert db.execute(
            "SELECT COUNT(*) FROM recording_transcription_selections WHERE recording_id=?",
            (rid,),
        ).fetchone()[0] == 0
        reread = load_recording(store, rid, db)
    assert [row["text"] for row in reread.rows] == ["First part.", "Second part."]
    assert [row["transcript_version"] for row in reread.rows] == [
        row["transcript_version"] for row in original.rows
    ]
    with store.connect() as db:
        repeated = publish_variant(db, store.audio_dir, variant, settings, expected)
    assert repeated["action"] == "already_present"


@pytest.mark.parametrize(
    "case", ["wrong_recording", "wrong_result_version", "wrong_pipeline", "provenance",
             "bad_anchor"],
)
def test_variant_validation_rejects_changed_identity_pipeline_and_timing(
    app_recording, tmp_path, case,
):
    _client, store, rid, _ids, settings, variant, expected, _source_db = (
        _fixture_variant(app_recording, tmp_path)
    )
    recording = load_recording(store, rid)
    if case == "wrong_recording":
        candidate = replace(variant, recording_id="wrong-id")
    elif case == "wrong_result_version":
        candidate = replace(variant, result_version="0" * 64)
    elif case == "wrong_pipeline":
        candidate = replace(variant, pipeline_fingerprint="0" * 64)
    elif case == "provenance":
        candidate = replace(
            variant,
            provenance=variant.provenance | {"image-id": "sha256:" + "0" * 64},
        )
    else:
        words = [dict(word) for word in variant.word_segments]
        words[1]["start_offset"] = 1
        candidate = replace(variant, word_segments=words)
    with pytest.raises(ImportRefused):
        validate_variant(candidate, recording, settings, expected)


def test_publish_rechecks_child_snapshot_under_write_lock(app_recording, tmp_path):
    _client, store, rid, _ids, settings, variant, expected, _source_db = (
        _fixture_variant(app_recording, tmp_path)
    )
    with store.connect() as db:
        db.execute("UPDATE audio SET text='corrected after qualification' WHERE id=?",
                   (load_recording(store, rid, db).rows[0]["id"],))
        db.commit()
        with pytest.raises(ImportRefused, match="fingerprint"):
            publish_variant(db, store.audio_dir, variant, settings, expected)
        assert db.execute(
            "SELECT COUNT(*) FROM recording_transcription_jobs WHERE recording_id=?", (rid,),
        ).fetchone()[0] == 0


def test_source_map_member_change_is_rejected(app_recording, tmp_path):
    _client, store, rid, _ids, settings, variant, expected, _source_db = (
        _fixture_variant(app_recording, tmp_path)
    )
    changed = replace(variant, source_map=variant.source_map[:-1])
    with pytest.raises(ImportRefused, match="source map"):
        validate_variant(changed, load_recording(store, rid), settings, expected)


def test_existing_different_result_version_is_never_replaced(app_recording, tmp_path):
    _client, store, rid, _ids, settings, variant, expected, _source_db = (
        _fixture_variant(app_recording, tmp_path)
    )
    with store.connect() as db:
        db.execute(
            "INSERT INTO recording_transcription_jobs "
            "(recording_id,input_fingerprint,pipeline_fingerprint,state,total_parts,result_version,"
            "transcript,created_at,updated_at) VALUES(?,?,?,'ready',?,'other-version','kept',?,?)",
            (rid, variant.input_fingerprint, variant.pipeline_fingerprint, variant.total_parts,
             "2026-11-03T00:00:00+00:00", "2026-11-03T00:00:00+00:00"),
        )
    with store.connect() as db:
        with pytest.raises(ImportRefused, match="different result version"):
            publish_variant(db, store.audio_dir, variant, settings, expected)
        row = db.execute(
            "SELECT result_version,transcript FROM recording_transcription_jobs "
            "WHERE recording_id=?", (rid,),
        ).fetchone()
    assert tuple(row) == ("other-version", "kept")


def test_profile_path_requires_explicit_known_scope(tmp_path):
    profile_id = "3894f30b-33c0-40c6-830d-6a648d63a922"
    assert profile_directory(tmp_path, "default", {"default"}) == tmp_path
    assert profile_directory(tmp_path, profile_id, {"default", profile_id}) == (
        tmp_path / "profiles" / profile_id
    )
    with pytest.raises(ImportRefused, match="does not exist"):
        profile_directory(tmp_path, profile_id, {"default"})
    with pytest.raises(ImportRefused, match="UUID"):
        profile_directory(tmp_path, "../default", {"default"})

