"""Filtering affects provider requests while retaining original samples and offsets."""

import io
import wave

import pytest

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


def test_padded_spans_merge_nearby_speech_without_concatenating_distant_audio():
    data, raw = recording(16000 * 5)
    result = filter_transcription_audio(data, activity=observed(
        [[1, 1.02], [1.8, 1.82], [4, 4.02]],
        [[0, 1], [1.02, 1.8], [1.82, 4], [4.02, 5]],
    ))
    assert result["status"] == "filtered"
    assert result["original_seconds"] == 5
    assert result["detected_speech_seconds"] == pytest.approx(0.06)
    assert result["retained_seconds"] == pytest.approx(2.04)
    assert [offset for offset, _ in result["spans"]] == [0.7, 3.7]
    assert pcm(result["spans"][0][1]) == raw[11200 * 2:33920 * 2]
    assert pcm(result["spans"][1][1]) == raw[59200 * 2:69120 * 2]
    assert pcm(data) == raw


def test_one_voiced_frame_is_retained_at_both_recording_boundaries():
    data, raw = recording(32000)
    result = filter_transcription_audio(data, activity=observed(
        [[0, 0.02], [1.98, 2]], [[0.02, 1.98]],
    ))
    assert [offset for offset, _ in result["spans"]] == [0, 1.68]
    assert pcm(result["spans"][0][1]) == raw[:5120 * 2]
    assert pcm(result["spans"][1][1]) == raw[26880 * 2:]
    assert result["detected_speech_seconds"] == pytest.approx(0.04)


def test_incomplete_vad_frame_is_retained_with_context_even_without_detected_speech():
    data, raw = recording(16001)
    result = filter_transcription_audio(data, activity=observed([], [[0, 1]]))
    assert result["status"] == "filtered"
    assert result["spans"][0][0] == 0.7
    assert pcm(result["spans"][0][1]) == raw[11200 * 2:]
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
