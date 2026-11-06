"""Zero-duration native envelopes keep text while their timing stays unknown."""

import asyncio
import io
import wave

import httpx
import pytest
from recording_transcription_helpers import pinned_settings

from omi_local.inference import Inference
from omi_local.recording_transcription_client import _validate_response
from omi_local.recording_transcription_store import project_words
from omi_local.settings import Settings


def _headers():
    return {
        "x-homecompute-stt-model-sha256": "a" * 64,
        "x-homecompute-stt-model-snapshot": "model-r1",
        "x-homecompute-stt-runtime-commit": "1234567890ab",
        "x-homecompute-stt-runtime": "whisper.cpp-r1",
    }


@pytest.mark.parametrize("timing_status", ["unknown_zero_duration", "unknown_out_of_bounds"])
def test_response_preserves_exact_text_as_untimed_native_segment(app_recording, timing_status):
    client, _, _, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    text = "before\nunknown phrase\nafter\n"
    segments = [
        {"start": 0.0, "end": 0.2, "text": "before", "start_offset": 0, "end_offset": 6},
        {"start": 0.2, "end": 0.2, "text": "unknown phrase", "start_offset": 7, "end_offset": 21},
        {"start": 0.2, "end": 0.4, "text": "after", "start_offset": 22, "end_offset": 27},
    ]
    if timing_status == "unknown_out_of_bounds":
        segments[1].update(start=None, end=None, timing_status=timing_status)

    result, native, _ = _validate_response(
        _headers(),
        {"text": text, "segments": segments},
        memory.settings,
        1.0,
    )

    assert result == text
    assert [item["text"] for item in native] == [item["text"] for item in segments]
    assert [(item["start"], item["end"]) for item in native] == [
        (0.0, 0.2),
        (None, None),
        (0.2, 0.4),
    ]
    assert native[1]["timing_status"] == timing_status
    assert [(item["start_offset"], item["end_offset"]) for item in native] == [
        (0, 6),
        (7, 21),
        (22, 27),
    ]
    assert all(text[item["start_offset"] : item["end_offset"]] == item["text"] for item in native)


@pytest.mark.parametrize(
    "segments",
    [
        [{"start": 0.3, "end": 0.2, "text": "word"}],
        [
            {"start": 0.2, "end": 0.4, "text": "first"},
            {"start": 0.3, "end": 0.5, "text": "second"},
        ],
        [{"start": 0.0, "end": 1.1, "text": "word"}],
        [{"start": float("nan"), "end": 0.1, "text": "word"}],
        [{"start": None, "end": None, "text": "word"}],
        [{"start": None, "end": None, "text": "word", "timing_status": "other"}],
        [{"start": None, "end": 0.2, "text": "word",
          "timing_status": "unknown_out_of_bounds"}],
        [{"start": 0.0, "end": 0.2, "text": "word",
          "timing_status": "unknown_out_of_bounds"}],
    ],
)
def test_zero_duration_handling_does_not_admit_other_invalid_timings(app_recording, segments):
    client, _, _, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    with pytest.raises(ValueError, match="invalid_segments"):
        _validate_response(
            _headers(),
            {"text": "canonical", "segments": segments},
            memory.settings,
            1.0,
        )


def test_untimed_overrun_requires_verified_character_anchors(app_recording):
    client, _, _, _, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    with pytest.raises(ValueError, match="invalid_segments"):
        _validate_response(_headers(), {
            "text": "actual words", "segments": [{
                "text": "words", "start": None, "end": None,
                "start_offset": 0, "end_offset": 5,
                "timing_status": "unknown_out_of_bounds",
            }],
        }, memory.settings, 1.0)


@pytest.mark.parametrize("timing_status", ["unknown_zero_duration", "unknown_out_of_bounds"])
def test_unknown_native_time_stays_out_of_alignment_and_projection(timing_status):
    text = "first unknown last"
    audio_file = io.BytesIO()
    with wave.open(audio_file, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\0\0" * 16000)
    audio = audio_file.getvalue()
    submitted = []

    class Client:
        async def post(self, _url, *, files, data, **_kwargs):
            submitted.append(data["text"])
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://127.0.0.1:8799/align"),
                json={
                    "status": "aligned",
                    "language": "en",
                    "model": "alignment-r1",
                    "segments": [
                        {
                            "start": 0.05,
                            "end": 0.15,
                            "start_offset": 0,
                            "end_offset": len(data["text"]),
                            "text": data["text"],
                        }
                    ],
                },
            )

    inference = Inference(
        Settings(
            alignment_enabled=True,
            alignment_language="en",
            alignment_base_url="http://127.0.0.1:8799",
            _env_file=None,
        ),
        Client(),
    )
    native = [
        {
            "start": 0.1,
            "end": 0.3,
            "start_offset": 0,
            "end_offset": 5,
            "text": "first",
            "kind": "segment",
        },
        {
            "start": None,
            "end": None,
            "start_offset": 6,
            "end_offset": 13,
            "text": "unknown",
            "kind": "segment",
            "timing_status": timing_status,
        },
        {
            "start": 0.5,
            "end": 0.8,
            "start_offset": 14,
            "end_offset": 18,
            "text": "last",
            "kind": "segment",
        },
    ]

    words, status = asyncio.run(
        inference._align_chunks(
            [(0, audio, text, "en", 0, native)],
            inference.settings,
            2,
        )
    )
    projection = project_words(
        text,
        words or [],
        [{"source_id": "part-a", "start": 0.0, "end": 1.0}],
    )

    assert submitted == ["first", "last"]
    assert status["status"] == "partial"
    assert [item["text"] for item in words or []] == ["first", "last"]
    assert "".join(item["text"] for item in projection) == text
    assert any(item["start"] is None and "unknown" in item["text"] for item in projection)
