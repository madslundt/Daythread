import pytest
from recording_transcription_helpers import pinned_settings

from omi_local.recording_transcription_client import _validate_response


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

def test_response_uses_authoritative_newline_segment_anchors_without_text_search(app_recording):
    client, _, _, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    headers = {
        "x-homecompute-stt-model-sha256": "a" * 64,
        "x-homecompute-stt-model-snapshot": "model-r1",
        "x-homecompute-stt-runtime-commit": "1234567890ab",
        "x-homecompute-stt-runtime": "whisper.cpp-r1",
    }
    text = "  same word\n\nsame word  "
    segments = [
        {"start": 0.1, "end": 0.8, "text": "  same word", "start_offset": 0,
         "end_offset": 11},
        {"start": 1.0, "end": 1.8, "text": "same word  ", "start_offset": 13,
         "end_offset": 24},
    ]
    result_text, native, _ = _validate_response(
        headers, {"text": text, "segments": segments}, memory.settings, 2,
    )
    assert result_text == text
    assert [(item["start_offset"], item["end_offset"]) for item in native] == [
        (0, 11), (13, 24),
    ]
    assert all(text[item["start_offset"]:item["end_offset"]] == item["text"]
               for item in native)

def test_response_anchor_mismatch_keeps_canonical_text_but_disables_native_anchors(app_recording):
    client, _, _, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    headers = {
        "x-homecompute-stt-model-sha256": "a" * 64,
        "x-homecompute-stt-model-snapshot": "model-r1",
        "x-homecompute-stt-runtime-commit": "1234567890ab",
        "x-homecompute-stt-runtime": "whisper.cpp-r1",
    }
    text = " repeat\nrepeat "
    _, native, _ = _validate_response(
        headers,
        {"text": text, "segments": [
            {"start": 0, "end": 0.3, "text": " repeat", "start_offset": 1,
             "end_offset": 8},
            {"start": 0.4, "end": 0.8, "text": "repeat ", "start_offset": 9,
             "end_offset": 16},
        ]},
        memory.settings,
        1,
    )
    assert [(item["start_offset"], item["end_offset"]) for item in native] == [
        (None, None), (None, None),
    ]
