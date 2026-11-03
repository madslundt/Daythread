import json
from uuid import uuid4

from omi_local.merged_recordings import load_recording
from omi_local.recording_transcription_store import (
    pipeline_fingerprint,
    recording_source_map,
    source_fingerprint,
)


def pinned_settings(memory):
    memory.settings.recording_transcription_expected_model_sha256 = "a" * 64
    memory.settings.recording_transcription_expected_model_snapshot = "model-r1"
    memory.settings.recording_transcription_expected_runtime_commit = "1234567890ab"
    memory.settings.recording_transcription_enabled = True


def make_ready_variant(store, memory, rid, ids, text):
    recording = load_recording(store, rid)
    input_fingerprint = source_fingerprint(recording)
    pipeline = pipeline_fingerprint(memory.settings)
    store.enqueue_recording_transcription(rid, input_fingerprint, pipeline, len(ids))
    version = str(uuid4())
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET state='ready',result_version=?,transcript=?,"
            "source_map=?,provenance=? WHERE recording_id=? AND input_fingerprint=? "
            "AND pipeline_fingerprint=?",
            (version, text, json.dumps(recording_source_map(recording)),
             json.dumps({"runtime": "test"}), rid, input_fingerprint, pipeline),
        )
    return recording, version
