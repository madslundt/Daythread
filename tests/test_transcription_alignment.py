"""Optional word timings preserve recognized text and source-local offsets."""

import io
import wave

import httpx
import pytest

import omi_local.transcription as transcription
from omi_local.inference import Inference
from omi_local.settings import Settings


def wav(value: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(value.to_bytes(2, "little", signed=True) * 16000)
    return output.getvalue()


@pytest.mark.anyio
async def test_filtered_span_offsets_are_restored_without_changing_recognition(monkeypatch):
    audio_a, audio_b = wav(4000), wav(5000)
    monkeypatch.setattr(transcription, "filter_transcription_audio", lambda *args, **kwargs: {
        "spans": [(0.5, audio_a), (3.5, audio_b)], "status": "filtered",
        "version": 1, "original_seconds": 5, "retained_seconds": 2,
        "detected_speech_seconds": 2,
    })

    call = 0

    async def reply(request):
        nonlocal call
        text = ("følelse verden", "æ øå")[call]
        call += 1
        starts = [(0.1, 0.4), (0.5, 0.9)]
        import re
        segments = [
            {"start": starts[index][0], "end": starts[index][1],
             "start_offset": match.start(), "end_offset": match.end(), "text": match.group()}
            for index, match in enumerate(re.finditer(r"\S+", text))
        ]
        return httpx.Response(200, json={"status": "aligned", "language": "da",
                                         "model": "fixture@1",
                                         "segments": segments})

    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    inference = Inference(Settings(
        alignment_enabled=True, stt_enhancement_mode="off", stt_max_gain_db=0,
        _env_file=None,
    ), client)

    async def recognize(data, *_args, **_kwargs):
        text = "følelse verden" if data == audio_a else "æ øå"
        return {"text": text, "segments": [], "recognition": {"recognition_model": "local"}}

    inference._transcribe_chunk = recognize
    result = await inference._transcribe_filtered(wav(6000) * 1, None, None)
    assert result["text"] == "følelse verden\næ øå"
    assert result["processing"]["alignment"] == {
        "status": "aligned", "model": "fixture@1", "languages": ["da"],
    }
    assert result["segments"] == [
        {"start": 0.6, "end": 0.9, "text": "følelse", "start_offset": 0,
         "end_offset": 7, "kind": "word"},
        {"start": 1.0, "end": 1.4, "text": "verden", "start_offset": 8,
         "end_offset": 14, "kind": "word"},
        {"start": 3.6, "end": 3.9, "text": "æ", "start_offset": 15,
         "end_offset": 16, "kind": "word"},
        {"start": 4.0, "end": 4.4, "text": "øå", "start_offset": 17,
         "end_offset": 19, "kind": "word"},
    ]
    await client.aclose()


@pytest.mark.anyio
async def test_partial_alignment_keeps_only_character_anchored_fragments():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"status": "partial", "language": "da", "model": "fixture@1",
                   "segments": [
            {"start": 0.2, "end": 0.4, "start_offset": 0, "end_offset": 4,
             "text": "navn"},
        ]},
    )))
    inference = Inference(Settings(_env_file=None), client)
    parts = [(2.0, wav(3000), "navn ukendt", "da", 15)]
    fragments, status = await inference._align_chunks(parts, inference.settings, 2)
    assert status == {"status": "partial", "model": "fixture@1", "languages": ["da"]}
    assert fragments == [{"start": 2.2, "end": 2.4, "text": "navn",
                          "start_offset": 15, "end_offset": 19, "kind": "word"}]
    await client.aclose()


@pytest.mark.anyio
async def test_repeated_words_keep_chunk_identity_through_final_newline_join():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"status": "aligned", "language": "da", "model": "fixture@1",
                   "segments": [
            {"start": 0.1, "end": 0.3, "start_offset": 0, "end_offset": 4,
             "text": "navn"},
            {"start": 0.4, "end": 0.6, "start_offset": 5, "end_offset": 9,
             "text": "navn"},
        ]},
    )))
    inference = Inference(Settings(_env_file=None), client)
    parts = [
        (1.0, wav(3000), "navn navn", "da", 0),
        (20.0, wav(3000), "navn navn", "da", 10),
    ]
    fragments, status = await inference._align_chunks(parts, inference.settings, 2)
    assert status["status"] == "aligned"
    assert [(item["start_offset"], item["end_offset"]) for item in fragments] == [
        (0, 4), (5, 9), (10, 14), (15, 19),
    ]
    assert [item["start"] for item in fragments] == [1.1, 1.4, 20.1, 20.4]
    await client.aclose()


@pytest.mark.anyio
async def test_invalid_time_is_discarded_without_touching_text():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"status": "aligned", "language": "da", "model": "fixture@1",
                   "segments": [
            {"start": 0.9, "end": 0.4, "start_offset": 0, "end_offset": 4,
             "text": "navn"},
        ]},
    )))
    inference = Inference(Settings(_env_file=None), client)
    fragments, status = await inference._align_chunks(
        [(0.0, wav(3000), "navn", "da", 0)], inference.settings, 2,
    )
    assert status == {"status": "invalid", "model": "fixture@1", "languages": ["da"]}
    assert fragments == []
    await client.aclose()
