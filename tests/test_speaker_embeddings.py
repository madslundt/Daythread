"""Offline extraction contracts with synthetic PCM; no recognition accuracy claims."""

import hashlib
import io
import math
import wave

import pytest

from scripts.speaker_embeddings import SpeakerEmbeddings


def wav(seconds=80, amplitude=100):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(amplitude.to_bytes(2, "little", signed=True) * (16000 * seconds))
    output.seek(0)
    return output


def encoder(tmp_path, compute=None):
    model = tmp_path / "local.onnx"
    model.write_bytes(b"software-only model")
    checksum = hashlib.sha256(model.read_bytes()).hexdigest()
    calls = []

    def loader(path):
        assert path == model

        def encode(samples):
            calls.append(samples)
            return compute(samples) if compute else [3.0, 4.0]

        return encode, 2, {"runtime": "software-only", "version": "fixture"}

    return SpeakerEmbeddings(str(model), checksum, loader), calls, model


def test_unconfigured_or_wrong_checksum_never_loads_a_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("OMI_SPEAKER_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OMI_SPEAKER_EMBEDDING_SHA256", raising=False)
    instance = SpeakerEmbeddings(loader=lambda _: pytest.fail("Unexpected encoder load"))
    assert instance.health() == {"configured": False, "loaded": False}
    assert instance.extract(wav(), [{"start": 0, "end": 80, "speaker": "a"}]) == {"voices": []}
    model = tmp_path / "wrong.onnx"
    model.write_bytes(b"wrong model")
    wrong = SpeakerEmbeddings(str(model), "a" * 64,
                              lambda _: pytest.fail("Unexpected encoder load"))
    assert wrong.health() == {"configured": False, "loaded": False}


def test_verified_local_encoder_returns_bounded_normalized_voice_and_provenance(tmp_path):
    instance, calls, _ = encoder(tmp_path)
    assert instance.health() == {"configured": True, "loaded": False}
    stream = wav()
    result = instance.extract(stream, [{"start": 0, "end": 80, "speaker": "a"}])
    assert len(result["embedding_model"]["revision"]) == 64
    assert result["embedding_model"]["normalization"] == "l2-v1"
    assert result["embedding_model"]["dimension"] == 2
    assert instance.health() == {"configured": True, "loaded": True}
    assert len(calls) == 6
    assert all(3 * 16000 <= len(samples) <= 10 * 16000 for samples in calls)
    voice = result["voices"][0]
    assert voice["speaker"] == "a"
    assert voice["embedding"] == pytest.approx([0.6, 0.8])
    assert math.hypot(*voice["embedding"]) == pytest.approx(1)
    assert voice["clean_seconds"] == 60
    assert voice["overlap_ratio"] == voice["clipped_ratio"] == 0
    assert stream.tell() == 0


def test_overlap_is_excluded_and_short_speakers_never_get_a_voiceprint(tmp_path):
    instance, calls, _ = encoder(tmp_path)
    result = instance.extract(wav(), [
        {"start": 0, "end": 40, "speaker": "a"},
        {"start": 20, "end": 30, "speaker": "b"},
        {"start": 50, "end": 59, "speaker": "c"},
    ])
    assert [voice["speaker"] for voice in result["voices"]] == ["a"]
    assert result["voices"][0]["overlap_ratio"] == pytest.approx(0.25)
    assert result["voices"][0]["clean_seconds"] == pytest.approx(29.4)
    assert sum(len(samples) for samples in calls) == 29.4 * 16000


def test_clipping_is_measured_on_exactly_the_samples_used_by_the_encoder(tmp_path):
    instance, _, _ = encoder(tmp_path)
    result = instance.extract(wav(amplitude=32767), [{"start": 0, "end": 40, "speaker": "a"}])
    assert result["voices"][0]["clipped_ratio"] == 1


@pytest.mark.parametrize("values", [[float("nan"), 1.0], [0.0, 0.0], [1.0]])
def test_invalid_encoder_vectors_are_omitted_without_fabricated_fallback(tmp_path, values):
    instance, _, _ = encoder(tmp_path, lambda _: values)
    result = instance.extract(wav(), [{"start": 0, "end": 40, "speaker": "a"}])
    assert result["voices"] == []


def test_silence_and_changed_weights_never_reuse_a_previous_voiceprint(tmp_path):
    instance, calls, model = encoder(tmp_path)
    turns = [{"start": 0, "end": 40, "speaker": "a"}]
    assert instance.extract(wav(amplitude=0), turns)["voices"] == []
    assert calls == []
    assert instance.extract(wav(), turns)["voices"]
    model.write_bytes(b"different model")
    assert instance.extract(wav(), turns) == {"voices": []}
    assert instance.health() == {"configured": False, "loaded": False}
