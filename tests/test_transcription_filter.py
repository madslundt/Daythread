"""Filtering affects provider requests while retaining original samples and offsets."""

import io
import wave
from email.parser import BytesParser

import httpx
import pytest

from omi_local.audio import transcription_chunks
from omi_local.inference import Inference
from omi_local.settings import Settings
from omi_local.transcription_filter import filter_transcription_audio


def recording(frames=16000):
    raw = b"".join((index % 30000 + 1).to_bytes(2, "little") for index in range(frames))
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(raw)
    return output.getvalue(), raw


def pcm(data):
    with wave.open(io.BytesIO(data)) as stream:
        return stream.readframes(stream.getnframes())


def observed(speech, non_speech):
    return {"status": "observed", "method": "webrtcvad", "speech": speech,
            "non_speech": non_speech}


@pytest.mark.anyio
@pytest.mark.parametrize("chunk_seconds", [10, 30])
async def test_vad_islands_keep_the_same_request_audio_gain_and_offsets_as_full_audio(
    tmp_path, chunk_seconds,
):
    data, _ = recording(16000 * 60)
    activity = observed([[0, 1.58], [3.28, 60]], [[1.58, 3.28]])
    requests = []

    def provider(request):
        message = BytesParser().parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
        )
        requests.append(next(
            part.get_payload(decode=True) for part in message.get_payload()
            if part.get_param("name", header="content-disposition") == "file"
        ))
        return httpx.Response(200, json={
            "text": "word", "segments": [{"start": .1, "end": .5, "text": "word"}],
        })

    settings = Settings(data_dir=tmp_path, _env_file=None, stt_filter_speech=False,
                        stt_chunk_seconds=chunk_seconds, stt_enhancement_mode="off")
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        baseline = await Inference(settings, client).transcribe_result(
            data, voice_activity=activity,
        )
        baseline_audio = list(requests)
        requests.clear()
        filtering = settings.model_copy(update={"stt_filter_speech": True})
        filtered = await Inference(filtering, client).transcribe_result(
            data, voice_activity=activity,
        )
    assert requests == baseline_audio
    assert filtered["segments"] == baseline["segments"]
    assert filtered["processing"]["chunks"] == baseline["processing"]["chunks"]
    assert filtered["processing"]["filtering"]["retained_seconds"] == 60


def test_padded_speech_selects_original_windows_without_concatenating_distant_audio():
    data, raw = recording(16000 * 8)
    result = filter_transcription_audio(data, chunk_seconds=1, boundary_search_seconds=0,
                                       activity=observed(
        [[1, 1.02], [1.8, 1.82], [6, 6.02]],
        [[0, 1], [1.02, 1.8], [1.82, 6], [6.02, 8]],
    ))
    assert result["status"] == "filtered"
    assert result["original_seconds"] == 8
    assert result["detected_speech_seconds"] == pytest.approx(0.06)
    assert result["retained_seconds"] == 5
    assert [offset for offset, _ in result["spans"]] == [0, 1, 2, 5, 6]
    for offset, span in result["spans"]:
        first = round(offset * 16000)
        assert pcm(span) == raw[first * 2:(first + 16000) * 2]
    assert pcm(data) == raw


@pytest.mark.parametrize("boundary_search_seconds", [0, 2])
def test_window_selection_keeps_the_configured_pause_boundary_without_duplicate_samples(
    boundary_search_seconds,
):
    _, raw = recording(16000 * 20)
    raw = raw[:9 * 32000] + b"\0" * 32000 + raw[10 * 32000:]
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(raw)
    data = output.getvalue()
    windows = list(transcription_chunks(data, 10, boundary_search_seconds))
    assert [offset for offset, _ in windows] == (
        [0, 9.5, 19.5] if boundary_search_seconds else [0, 10]
    )
    result = filter_transcription_audio(
        data, chunk_seconds=10, boundary_search_seconds=boundary_search_seconds,
        activity=observed([[1, 1.02], [12, 12.02]], [[0, 1], [1.02, 12], [12.02, 20]]),
    )
    assert result["spans"] == windows[:2]
    retained = 19.5 if boundary_search_seconds else 20
    assert b"".join(pcm(span) for _, span in result["spans"]) == raw[:round(retained * 32000)]
    assert result["retained_seconds"] == retained


def test_one_voiced_frame_is_retained_at_both_recording_boundaries():
    data, raw = recording(32000)
    result = filter_transcription_audio(data, chunk_seconds=1, boundary_search_seconds=0,
                                       activity=observed(
        [[0, 0.02], [1.98, 2]], [[0.02, 1.98]],
    ))
    assert [offset for offset, _ in result["spans"]] == [0, 1]
    assert b"".join(pcm(span) for _, span in result["spans"]) == raw
    assert result["detected_speech_seconds"] == pytest.approx(0.04)


def test_incomplete_vad_frame_is_retained_with_context_even_without_detected_speech():
    data, raw = recording(16001)
    result = filter_transcription_audio(data, chunk_seconds=1, boundary_search_seconds=0,
                                       activity=observed([], [[0, 1]]))
    assert result["status"] == "filtered"
    assert [offset for offset, _ in result["spans"]] == [0, 1]
    assert b"".join(pcm(span) for _, span in result["spans"]) == raw
    assert result["detected_speech_seconds"] == 0


def test_recording_shorter_than_a_vad_frame_is_never_dropped():
    data, _ = recording(100)
    result = filter_transcription_audio(data, activity=observed([], []))
    assert result["spans"] == [(0, data)]
    assert result["retained_seconds"] == 100 / 16000


def test_reliable_all_non_speech_detection_skips_provider_audio():
    data, _ = recording()
    result = filter_transcription_audio(data, activity=observed([], [[0, 1]]))
    assert result["spans"] == []
    assert result["retained_seconds"] == result["detected_speech_seconds"] == 0
    assert result["status"] == "filtered"


@pytest.mark.parametrize("activity", [
    {"status": "unknown"},
    observed([[0.5, 0.4]], [[0, 1]]),
    observed([[0.4, 1.1]], [[0, 0.4]]),
    observed([[float("nan"), 1]], [[0, 0.4]]),
    observed([[0.4, float("inf")]], [[0, 0.4]]),
    observed([[True, 1]], [[0, 0.4]]),
    observed([[0.4, 0.5]], [[0, 0.6], [0.5, 1]]),
    observed([], [[0, 0.8]]),
    {"status": "observed", "speech": []},
    observed("malformed", [[0, 1]]),
    {"status": "observed", "method": "digital_silence", "speech": [],
     "non_speech": [[0, 1]]},
])
def test_unknown_or_invalid_activity_fails_open(activity):
    data, _ = recording()
    result = filter_transcription_audio(data, activity=activity)
    assert result["spans"] == [(0, data)]
    assert result["status"] == "unavailable"
    assert result["retained_seconds"] == result["original_seconds"] == 1
    assert result["detected_speech_seconds"] is None


def test_disabled_filter_never_runs_detector(monkeypatch):
    def fail(_):
        raise AssertionError("Detector must not run")

    monkeypatch.setattr("omi_local.transcription_filter.analyze_voice", fail)
    data, _ = recording()
    result = filter_transcription_audio(data, enabled=False)
    assert result["status"] == "disabled"
    assert result["spans"] == [(0, data)]


def test_detector_exception_and_invalid_audio_fail_open(monkeypatch):
    def fail(_):
        raise RuntimeError("Unavailable detector")

    monkeypatch.setattr("omi_local.transcription_filter.analyze_voice", fail)
    data, _ = recording()
    assert filter_transcription_audio(data)["spans"] == [(0, data)]
    result = filter_transcription_audio(b"invalid wav")
    assert result["status"] == "unavailable"
    assert result["spans"] == [(0, b"invalid wav")]


def test_real_digital_silence_also_skips_incomplete_final_frame():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\0\0" * 321)
    result = filter_transcription_audio(output.getvalue())
    assert result["status"] == "filtered"
    assert result["spans"] == []


def test_unsupported_or_truncated_wav_is_never_filtered():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((2, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\0" * 64000)
    valid, _ = recording()
    for data in (output.getvalue(), valid[:-2]):
        result = filter_transcription_audio(data, activity=observed([], [[0, 1]]))
        assert result["status"] == "unavailable"
        assert result["spans"] == [(0, data)]
        assert result["detected_speech_seconds"] is None
