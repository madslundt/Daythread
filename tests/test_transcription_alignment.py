"""Optional word timings preserve recognized text and source-local offsets."""

import io
import json
import wave

import httpx
import pytest

import omi_local.transcription as transcription
from omi_local.inference import Inference
from omi_local.settings import Settings


def test_interval_coverage_handles_long_text_without_expanding_characters():
    text = "word " * 400_000
    assert transcription._intervals_cover_tokens(text, [(0, len(text))])
    assert not transcription._intervals_cover_tokens(text, [(0, len(text) - 2)])


def test_interval_coverage_accepts_adjacent_spans_and_rejects_token_gaps():
    text = "one two"
    assert transcription._intervals_cover_tokens(text, [(0, 2), (2, 7)])
    assert not transcription._intervals_cover_tokens(text, [(0, 2), (3, 7)])


def wav(value: int, seconds: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(value.to_bytes(2, "little", signed=True) * 16000 * seconds)
    return output.getvalue()


def ramp_wav(seconds: int) -> bytes:
    import array

    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(array.array("h", range(seconds * 16000)).tobytes())
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


@pytest.mark.anyio
async def test_native_segment_limits_audio_crop_and_rebases_time_and_text_offsets():
    audio = ramp_wav(2)
    text = "noise hi world tail"
    calls = []

    class Client:
        async def post(self, _url, *, files, data, **_kwargs):
            payload = files["file"][1]
            calls.append((payload, data))
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "aligned", "language": "da", "model": "fixture@1",
                "segments": [
                    {"start": 0.3, "end": 0.5, "start_offset": 0, "end_offset": 2,
                     "text": "hi"},
                    {"start": 0.6, "end": 0.9, "start_offset": 3, "end_offset": 8,
                     "text": "world"},
                ],
            })

    inference = Inference(Settings(_env_file=None), Client())
    native = [{"start": 0.5, "end": 1.5, "start_offset": 6, "end_offset": 14,
               "text": "hi world", "kind": "segment"}]
    fragments, status = await inference._align_chunks(
        [(2.0, audio, text, "da", 10, native)], inference.settings, 2,
    )

    assert status == {"status": "partial", "model": "fixture@1", "languages": ["da"]}
    assert len(calls) == 1
    crop, form = calls[0]
    assert form == {"text": "hi world", "language": "da"}
    with wave.open(io.BytesIO(crop), "rb") as stream:
        assert stream.getnframes() == 24000
        samples = stream.readframes(stream.getnframes())
    import array
    values = array.array("h")
    values.frombytes(samples)
    assert values[0] == 4000
    assert values[-1] == 27999
    assert fragments == [
        {"start": 2.55, "end": 2.75, "text": "hi", "start_offset": 16,
         "end_offset": 18, "kind": "word"},
        {"start": 2.85, "end": 3.15, "text": "world", "start_offset": 19,
         "end_offset": 24, "kind": "word"},
    ]


def test_native_segment_text_is_anchored_exactly_across_repeated_words():
    result = Inference._transcription_result({
        "text": "  ja ja  ",
        "segments": [
            {"start": 0.1, "end": 0.2, "text": "  ja"},
            {"start": 0.4, "end": 0.5, "text": " ja  "},
        ],
    }, wav(0))
    assert result["text"] == "ja ja"
    assert result["segments"] == [
        {"start": 0.1, "end": 0.2, "text": "ja", "start_offset": 0,
         "end_offset": 2, "kind": "segment"},
        {"start": 0.4, "end": 0.5, "text": " ja", "start_offset": 2,
         "end_offset": 5, "kind": "segment"},
    ]


def test_native_segment_clips_only_provider_text_outside_canonical_trim():
    result = Inference._transcription_result({
        "text": "  hej verden  ",
        "segments": [
            {"start": 0.1, "end": 0.2, "text": "  hej"},
            {"start": 0.3, "end": 0.4, "text": " verden  "},
        ],
    }, wav(0))
    assert result["text"] == "hej verden"
    assert result["segments"] == [
        {"start": 0.1, "end": 0.2, "text": "hej", "start_offset": 0,
         "end_offset": 3, "kind": "segment"},
        {"start": 0.3, "end": 0.4, "text": " verden", "start_offset": 3,
         "end_offset": 10, "kind": "segment"},
    ]


def test_mismatched_native_segment_text_cannot_become_a_crop_anchor():
    result = Inference._transcription_result({
        "text": "Hej verden", "segments": [
            {"start": 0.1, "end": 0.5, "text": "forkert"},
        ],
    }, wav(0))
    assert result["text"] == "Hej verden"
    assert result["segments"] == [{"start": 0.1, "end": 0.5, "text": "forkert"}]
    assert result["timestamp_status"] == "discarded_invalid"


def test_native_envelope_crop_obeys_service_duration_limit():
    from omi_local.alignment_audio import crop_native_envelope

    cropped, start = crop_native_envelope(wav(1000, 30), 0.1, 29.9)
    with wave.open(io.BytesIO(cropped), "rb") as stream:
        assert stream.getnframes() <= 30 * 16000
        assert stream.getnframes() == 30 * 16000
    assert 0 <= start <= 0.1


@pytest.mark.anyio
async def test_invalid_whole_recording_anchor_never_falls_back_to_full_audio():
    calls = []

    class Client:
        async def post(self, *_args, **_kwargs):
            calls.append(True)
            raise AssertionError("invalid native span must not be submitted")

    inference = Inference(Settings(_env_file=None), Client())
    native = [{"start": 0.1, "end": 0.5, "start_offset": 0, "end_offset": 4,
               "text": "wrong", "kind": "segment"}]
    fragments, status = await inference._align_chunks(
        [(0.0, wav(3000, 31), "right", "da", 0, native)], inference.settings, 2,
    )
    assert fragments == []
    assert status["status"] == "partial"
    assert not calls


@pytest.mark.anyio
async def test_pipeline_prefers_refined_words_and_aligns_prepared_audio_only(monkeypatch):
    from omi_local import transcription
    from omi_local.alignment_audio import crop_native_envelope

    original = ramp_wav(2)
    prepared = wav(8000, 2)
    monkeypatch.setattr(transcription, "filter_transcription_audio", lambda *_a, **_k: {
        "spans": [(0.0, original)], "status": "disabled", "version": 1,
        "original_seconds": 2, "retained_seconds": 2, "detected_speech_seconds": None,
    })
    monkeypatch.setattr(transcription, "amplify_quiet_audio", lambda *_a, **_k: (prepared, 6.0))
    calls = []

    class Client:
        async def post(self, _url, *, files, data, **_kwargs):
            calls.append((files["file"][1], data))
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "aligned", "language": "da", "model": "fixture@1",
                "segments": [
                    {"start": 0.3, "end": 0.5, "start_offset": 0, "end_offset": 2,
                     "text": "hi"},
                    {"start": 0.6, "end": 0.9, "start_offset": 3, "end_offset": 8,
                     "text": "world"},
                ],
            })

    inference = Inference(Settings(
        alignment_enabled=True, stt_enhancement_mode="off", stt_max_gain_db=6,
        _env_file=None,
    ), Client())

    async def recognize(data, *_args, **_kwargs):
        assert data == prepared
        return Inference._transcription_result({
            "text": "hi world", "segments": [
                {"start": 0.5, "end": 1.5, "text": "hi world"},
            ],
        }, data)

    inference._transcribe_chunk = recognize
    result = await inference._transcribe_filtered(original, None, None)
    expected_crop, crop_start = crop_native_envelope(prepared, 0.5, 1.5)
    assert calls == [(expected_crop, {"text": "hi world", "language": "da"})]
    assert result["text"] == "hi world"
    assert result["processing"]["alignment"]["status"] == "aligned"
    assert result["segments"] == [
        {"start": crop_start + 0.3, "end": crop_start + 0.5, "text": "hi",
         "start_offset": 0, "end_offset": 2, "kind": "word"},
        {"start": crop_start + 0.6, "end": crop_start + 0.9, "text": "world",
         "start_offset": 3, "end_offset": 8, "kind": "word"},
    ]


@pytest.mark.anyio
async def test_transcribe_result_projection_keeps_partial_words_and_unknown_gaps(monkeypatch):
    from omi_local import transcription
    from omi_local.speaker_projection import project_utterances

    audio = wav(2500, 2)
    text = "prefix alpha beta gamma delta epsilon"
    native_text = text[6:]
    monkeypatch.setattr(transcription, "filter_transcription_audio", lambda *_a, **_k: {
        "spans": [(0.0, audio)], "status": "disabled", "version": 1,
        "original_seconds": 2, "retained_seconds": 2, "detected_speech_seconds": None,
    })

    class Client:
        async def post(self, _url, *, data, **_kwargs):
            assert data["text"] == native_text
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "partial", "language": "da", "model": "fixture@1",
                "segments": [
                    {"start": 0.2, "end": 0.4, "start_offset": 1, "end_offset": 6,
                     "text": "alpha"},
                ],
            })

    inference = Inference(Settings(
        alignment_enabled=True, stt_enhancement_mode="off", _env_file=None,
    ), Client())

    async def recognize(_data, *_args, **_kwargs):
        return Inference._transcription_result({
            "text": text, "segments": [{
                "start": 0.1, "end": 1.8, "start_offset": 6,
                "end_offset": len(text), "text": native_text, "kind": "segment",
            }],
        }, audio)

    inference._transcribe_chunk = recognize
    result = await inference.transcribe_result(audio, recording_id="fixture")
    assert result["text"] == text
    row = {
        "id": "fixture", "text": result["text"],
        "segments": json.dumps(result["segments"]),
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:02+00:00",
    }
    turns = [{"source_id": "fixture", "start": 0, "end": 2, "speaker_id": "speaker"}]
    pieces = project_utterances(
        turns, [row], True, [{"id": "speaker", "label": "A", "name": None}],
    )
    assert "".join(piece["text"] for piece in pieces) == text
    assert [(piece["text"], piece["attribution"]) for piece in pieces] == [
        ("prefix ", "unknown"), ("alpha", "attributed"),
        (" beta gamma delta epsilon", "unknown"),
    ]
    assert [(piece["start"], piece["end"]) for piece in pieces] == [
        (None, None), (0.2, 0.4), (None, None),
    ]


@pytest.mark.anyio
async def test_native_alignment_deadline_keeps_prior_fragments_and_marks_timeout():
    calls = 0

    class Client:
        async def post(self, _url, *, data, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                import asyncio
                await asyncio.sleep(0.1)
            token = data["text"]
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "aligned", "language": "da", "model": "fixture@1",
                "segments": [{"start": 0.1, "end": 0.2, "start_offset": 0,
                              "end_offset": len(token), "text": token}],
            })

    inference = Inference(Settings(_env_file=None), Client())
    parts = [(0.0, wav(3000), "one two", "da", 0, [
        {"start": 0.1, "end": 0.3, "start_offset": 0, "end_offset": 3,
         "text": "one", "kind": "segment"},
        {"start": 0.4, "end": 0.6, "start_offset": 4, "end_offset": 7,
         "text": "two", "kind": "segment"},
    ])]
    fragments, status = await inference._align_chunks(parts, inference.settings, 0.02)
    assert status == {"status": "timeout", "model": "fixture@1", "languages": ["da"]}
    assert fragments == [{"start": 0.1, "end": 0.2, "text": "one", "start_offset": 0,
                          "end_offset": 3, "kind": "word"}]


@pytest.mark.anyio
async def test_invalid_native_anchor_uses_unmodified_whole_chunk_fallback():
    audio = wav(3000, 2)
    observed = []

    class Client:
        async def post(self, _url, *, files, data, **_kwargs):
            observed.append((files["file"][1], data["text"]))
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "aligned", "language": "da", "model": "fixture@1",
                "segments": [
                    {"start": 0.1, "end": 0.2, "start_offset": 0, "end_offset": 4,
                     "text": "safe"},
                    {"start": 0.3, "end": 0.4, "start_offset": 5, "end_offset": 10,
                     "text": "text."},
                ],
            })

    inference = Inference(Settings(_env_file=None), Client())
    bad_anchor = [{"start": 0.5, "end": 1.0, "start_offset": 0, "end_offset": 4,
                   "text": "wrong", "kind": "segment"}]
    fragments, status = await inference._align_chunks(
        [(0, audio, "safe text.", "da", 0, bad_anchor)], inference.settings, 2,
    )
    assert observed == [(audio, "safe text.")]
    assert status == {"status": "partial", "model": "fixture@1", "languages": ["da"]}
    assert [item["text"] for item in fragments] == ["safe", "text."]


@pytest.mark.anyio
async def test_native_segment_can_be_cropped_from_whole_recording_audio():
    calls = []

    class Client:
        async def post(self, _url, *, files, data, **_kwargs):
            payload = files["file"][1]
            with wave.open(io.BytesIO(payload), "rb") as stream:
                calls.append((stream.getnframes(), data["text"]))
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "aligned", "language": "da", "model": "fixture@1",
                "segments": [{"start": 0.3, "end": 0.5, "start_offset": 0,
                              "end_offset": 4, "text": "long"}],
            })

    inference = Inference(Settings(_env_file=None), Client())
    fragments, status = await inference._align_chunks(
        [(0, wav(3000, 121), "long", "da", 0, [
            {"start": 120.1, "end": 120.6, "start_offset": 0, "end_offset": 4,
             "text": "long", "kind": "segment"},
        ])], inference.settings, 2,
    )
    assert status == {"status": "aligned", "model": "fixture@1", "languages": ["da"]}
    assert calls == [(16000, "long")]
    assert len(fragments) == 1
    assert fragments[0] == {"start": pytest.approx(120.15), "end": pytest.approx(120.35),
                            "text": "long", "start_offset": 0, "end_offset": 4,
                            "kind": "word"}


@pytest.mark.anyio
async def test_overlapping_native_context_does_not_publish_nonmonotonic_word_times():
    calls = 0

    class Client:
        async def post(self, _url, *, data, **_kwargs):
            nonlocal calls
            calls += 1
            first, last, token = ((0.3, 0.45, "one") if calls == 1
                                  else (0.05, 0.1, "two"))
            return httpx.Response(200, request=httpx.Request("POST", "http://test/align"), json={
                "status": "aligned", "language": "da", "model": "fixture@1",
                "segments": [{"start": first, "end": last, "start_offset": 0,
                              "end_offset": len(token), "text": token}],
            })

    inference = Inference(Settings(_env_file=None), Client())
    fragments, status = await inference._align_chunks(
        [(0, wav(3000, 2), "one two", "da", 0, [
            {"start": 0.4, "end": 0.6, "start_offset": 0, "end_offset": 3,
             "text": "one", "kind": "segment"},
            {"start": 0.61, "end": 0.81, "start_offset": 4, "end_offset": 7,
             "text": "two", "kind": "segment"},
        ])], inference.settings, 2,
    )
    assert status["status"] == "partial"
    assert fragments == [{"start": pytest.approx(0.45), "end": pytest.approx(0.6),
                          "text": "one", "start_offset": 0, "end_offset": 3,
                          "kind": "word"}]
