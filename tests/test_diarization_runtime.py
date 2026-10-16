"""Exercise the optional service without downloading models or importing PyTorch."""

import io
import math
import re
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from scripts import diarization_server as runtime


def wav_bytes(frames=16000, channels=1, rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((channels, 2, rate, 0, "NONE", "PCM"))
        audio.writeframes(b"\x01\x00" * frames * channels)
    return output.getvalue()


@pytest.mark.parametrize("channels,rate", [(2, 16000), (1, 8000)])
def test_runtime_rejects_unexpected_audio_formats(channels, rate):
    with pytest.raises(HTTPException) as error:
        runtime.inspect_wav(io.BytesIO(wav_bytes(channels=channels, rate=rate)))
    assert error.value.status_code == 422


@pytest.mark.parametrize("data", [b"not audio", wav_bytes(0), wav_bytes()[:-2]])
def test_runtime_rejects_invalid_empty_and_truncated_audio(data):
    with pytest.raises(HTTPException) as error:
        runtime.inspect_wav(io.BytesIO(data))
    assert error.value.status_code == 422


def test_runtime_checks_declared_duration_without_allocating_audio(monkeypatch):
    monkeypatch.setattr(runtime, "MAX_DURATION_SECONDS", 0.5)
    with pytest.raises(HTTPException) as error:
        runtime.inspect_wav(io.BytesIO(wav_bytes()))
    assert error.value.status_code == 413


def test_runtime_model_loading_is_lazy_and_error_is_redacted():
    calls = []

    def unavailable():
        calls.append(True)
        raise RuntimeError("private token and model path")

    service = runtime.DiarizationRuntime(unavailable)
    assert not service.health()["ready"] and not calls
    with pytest.raises(HTTPException) as error:
        service.diarize(io.BytesIO(wav_bytes()), None)
    assert calls == [True]
    assert error.value.status_code == 503
    assert "private" not in error.value.detail
    assert not service.health()["ready"]


def test_offline_model_loading_never_enables_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("OMI_DIARIZATION_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    calls = []
    pipeline = SimpleNamespace(to=lambda device: calls.append(device))
    fake = SimpleNamespace(from_pretrained=lambda path, **kw: calls.append((path, kw)) or pipeline)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(device=lambda device: device))
    monkeypatch.setitem(sys.modules, "pyannote", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyannote.audio", SimpleNamespace(Pipeline=fake))
    assert runtime.load_pipeline() is pipeline
    assert calls == [(str(tmp_path), {"token": False}), "cpu"]
    assert runtime.os.environ["HF_HUB_OFFLINE"] == "1"


def test_missing_configuration_cannot_trigger_model_download(monkeypatch):
    monkeypatch.delenv("OMI_DIARIZATION_MODEL_DIR", raising=False)
    monkeypatch.delenv("OMI_DIARIZATION_ALLOW_DOWNLOAD", raising=False)
    monkeypatch.setenv("HF_TOKEN", "some-existing-huggingface-token")
    with pytest.raises(RuntimeError, match="not configured"):
        runtime.load_pipeline()
    assert not runtime.DiarizationRuntime().health()["configured"]


def annotation(*entries):
    return SimpleNamespace(itertracks=lambda **kw: (
        (SimpleNamespace(start=start, end=end), "track", label) for start, end, label in entries
    ))


def test_turns_use_consistent_safe_identifiers_and_bounded_timestamps():
    turns = runtime.normalize_turns(annotation(
        (-0.1, 0.3, "model label 1"), (0.4, 0.6, "other"), (0.7, 1.2, "model label 1")
    ), 1.0)
    assert turns == [
        {"start": 0.0, "end": 0.3, "speaker": "SPEAKER_00"},
        {"start": 0.4, "end": 0.6, "speaker": "SPEAKER_01"},
        {"start": 0.7, "end": 1.0, "speaker": "SPEAKER_00"},
    ]
    with pytest.raises(ValueError):
        runtime.normalize_turns(annotation((0, float("nan"), "bad")), 1.0)


def test_runtime_turn_limit_matches_backend_result_limit():
    repeated = SimpleNamespace(itertracks=lambda **kw: (
        (SimpleNamespace(start=0, end=0.1), "track", "voice") for _ in range(100001)
    ))
    with pytest.raises(ValueError, match="Too many speaker turns"):
        runtime.normalize_turns(repeated, 1.0)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_service_upload_contract_validates_count_and_closes_files(monkeypatch):
    pytest.importorskip("multipart")
    streams = []

    class FakeRuntime(runtime.DiarizationRuntime):
        def diarize(self, stream, num_speakers):
            streams.append(stream)
            assert runtime.inspect_wav(stream) == (16000, 1.0)
            assert num_speakers == 3
            return {"model": runtime.MODEL_ID, "turns": [
                {"start": 0, "end": 1, "speaker": "SPEAKER_00"}
            ]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.create_app(FakeRuntime())),
        base_url="http://local",
    ) as client:
        response = await client.post("/diarize", files={"file": ("meeting.wav", wav_bytes())},
                                     data={"num_speakers": "3"})
        assert response.status_code == 200
        assert response.json()["turns"][0]["speaker"] == "SPEAKER_00"
        assert streams[0].closed
        invalid = await client.post("/diarize", files={"file": ("x.wav", wav_bytes())},
                                    data={"num_speakers": "0"})
        assert invalid.status_code == 422
        monkeypatch.setattr(runtime, "MAX_REQUEST_BYTES", 10)
        oversized = await client.post("/diarize", content=b"x" * 11)
        assert oversized.status_code == 413


@pytest.mark.anyio
async def test_runtime_speaker_count_limit_matches_backend():
    pytest.importorskip("multipart")
    counts = []

    class FakeRuntime(runtime.DiarizationRuntime):
        def diarize(self, stream, num_speakers):
            counts.append(num_speakers)
            return {"model": runtime.MODEL_ID, "turns": []}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.create_app(FakeRuntime())),
        base_url="http://local",
    ) as client:
        valid = await client.post("/diarize", files={"file": ("x.wav", wav_bytes())},
                                  data={"num_speakers": "64"})
        invalid = await client.post("/diarize", files={"file": ("x.wav", wav_bytes())},
                                    data={"num_speakers": "65"})
        assert valid.status_code == 200
        assert invalid.status_code == 422
        assert counts == [64]


@pytest.mark.anyio
async def test_service_busy_requests_fail_without_running_second_model():
    pytest.importorskip("multipart")
    started, finish = threading.Event(), threading.Event()

    class BlockingRuntime(runtime.DiarizationRuntime):
        def diarize(self, stream, num_speakers):
            started.set()
            assert finish.wait(5)
            return {"model": runtime.MODEL_ID, "turns": []}

    app = runtime.create_app(BlockingRuntime())

    def first_request():
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            return client.post("/diarize", files={"file": ("meeting.wav", wav_bytes())})

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(first_request)
        assert started.wait(5)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://local") as client:
                health = (await client.get("/health")).json()
                assert health["busy"] is True
                second = await client.post("/diarize", content=b"not even parsed")
                assert second.status_code == 429
        finally:
            finish.set()
        assert first.result().status_code == 200


@pytest.mark.anyio
async def test_chunked_body_limit_closes_partial_multipart_files(monkeypatch):
    pytest.importorskip("multipart")
    from starlette import formparsers

    opened = []
    temporary_file = formparsers.SpooledTemporaryFile

    def track_file(*args, **kwargs):
        result = temporary_file(*args, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", track_file)
    monkeypatch.setattr(runtime, "MAX_REQUEST_BYTES", 20000)
    encoded = httpx.Request("POST", "http://local/diarize",
                            files={"file": ("x.wav", wav_bytes())}).read()

    async def chunks():
        for start in range(0, len(encoded), 1024):
            yield encoded[start:start + 1024]

    # No Content-Length: the streaming bound must still apply after a temporary
    # upload has been opened, and the multipart error path must close that file.
    # Reuse the actual boundary from the already-encoded body.
    boundary = encoded.split(b"\r\n", 1)[0][2:].decode()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.create_app()),
                                 base_url="http://local") as client:
        response = await client.post(
            "/diarize", content=chunks(),
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
    assert response.status_code == 413
    assert opened and all(stream.closed for stream in opened)


def test_regular_overlap_subtraction_preserves_canonical_speaker_mapping():
    labels = {}
    runtime.normalize_turns(annotation((0, 20, "z"), (20, 40, "a")), 40, labels)
    # Regular diarization can list labels in a different order; a third speaker
    # fully suppressed in exclusive output must still block contaminated audio.
    regular = runtime.normalize_turns(annotation(
        (18, 40, "a"), (0, 22, "z"), (4, 7, "overlap-only"), (5, 6, "overlap-only")
    ), 40, labels)
    clean, overlap = runtime.clean_intervals(regular, "SPEAKER_00")
    assert clean == [(0, 4), (7, 18)]
    assert overlap == pytest.approx(7 / 22)
    assert runtime.clean_intervals(regular, "SPEAKER_01")[0] == [(22, 40)]


def test_clean_window_selection_has_collars_no_padding_and_bounded_cost():
    windows = runtime.voice_windows([(1, 1.5), (3, 9), (20, 200)])
    assert sum(b - a for a, b in windows) == 60 * 16000
    assert all(3 * 16000 <= b - a <= 10 * 16000 for a, b in windows)
    assert windows[0][0] == math.ceil(20.15 * 16000)
    assert all(20.15 * 16000 <= a < b <= 199.85 * 16000 for a, b in windows)


@pytest.mark.parametrize("vector", [[0, 0], [float("nan"), 1], [float("inf"), 1], [1]])
def test_invalid_voice_vectors_are_never_serialized(vector):
    with pytest.raises(ValueError):
        runtime.unit_vector(vector, 2)


class FakeMask:
    def __init__(self, positions):
        self.positions = positions

    def __or__(self, other):
        return FakeMask(self.positions | other.positions)

    def sum(self):
        return len(self.positions)


class FakePCM:
    def __init__(self, clipped=(), first=0, last=16000 * 100):
        self.clipped, self.first, self.last = set(clipped), first, last

    def __getitem__(self, key):
        return FakePCM(self.clipped, key.start, key.stop)

    def __ge__(self, threshold):
        return FakeMask({p for p in self.clipped if self.first <= p < self.last})

    def __le__(self, threshold):
        return FakeMask(set())


class FakeWaveform:
    def __init__(self, bounds=None):
        self.bounds = bounds

    def __getitem__(self, key):
        return FakeWaveform((key[1].start, key[1].stop))

    def unsqueeze(self, dimension):
        return self if self.bounds is None else self.bounds

    def __truediv__(self, divisor):
        return self


def test_embeddings_use_only_clean_audio_and_measure_clipping_on_used_windows():
    calls = []

    def extractor(bounds):
        calls.append(bounds)
        return [[3, 4]]

    regular = [{"start": 0, "end": 30, "speaker": "A"},
               {"start": 10, "end": 15, "speaker": "B"}]
    voices = runtime.extract_voices(extractor, 2, FakeWaveform(),
                                     FakePCM(range(16000)), regular, {"A", "B"})
    assert [v["speaker"] for v in voices] == ["A"]
    assert voices[0]["embedding"] == pytest.approx([0.6, 0.8])
    assert voices[0]["clean_seconds"] == pytest.approx(24.4)
    assert voices[0]["overlap_ratio"] == pytest.approx(5 / 30)
    assert voices[0]["clipped_ratio"] == pytest.approx(0.85 / 24.4)
    assert all(b <= 10 * 16000 or a >= 15 * 16000 for a, b in calls)
    bad = runtime.extract_voices(lambda bounds: [[0, 0]], 2, FakeWaveform(), FakePCM(),
                                  regular, {"A"})
    assert bad == []


def test_fingerprint_hashes_actual_weights_config_and_implementation_without_paths(
    tmp_path, monkeypatch,
):
    source = tmp_path / "implementation.py"
    source.write_text("implementation one")

    class FakeTensor:
        dtype, shape = "float32", [2]
        data = b"actual weights"

        def __getattr__(self, name):
            if name == "tobytes":
                return lambda: self.data
            return lambda *a: self

    tensor = FakeTensor()
    model = SimpleNamespace(hparams={"sample_rate": 16000}, eval=lambda: None,
                            state_dict=lambda: {"weights": tensor})

    class Adapter:
        sample_rate, dimension, model_ = 16000, 2, model

    module_name = "pyannote.audio.pipelines.speaker_verification"
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(
        PyannoteAudioPretrainedSpeakerEmbedding=Adapter,
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(uint8="byte"))
    monkeypatch.setattr(runtime, "version", lambda p: "4.0.4" if p == "pyannote-audio" else "1")
    monkeypatch.setattr(runtime, "distribution", lambda p: SimpleNamespace(
        files=["pyannote/audio/implementation.py"], locate_file=lambda item: source,
    ))
    pipeline = SimpleNamespace(_embedding=Adapter())
    identity = runtime.embedding_adapter(pipeline)[1]
    assert re.fullmatch(r"[0-9a-f]{64}", identity["revision"])
    monkeypatch.setenv("HF_TOKEN", "private-access-credential")
    assert runtime.embedding_adapter(pipeline)[1] == identity
    assert str(tmp_path) not in str(identity) and "private-access-credential" not in str(identity)
    tensor.data = b"changed weights"
    changed_weights = runtime.embedding_adapter(pipeline)[1]
    assert changed_weights["revision"] != identity["revision"]
    model.hparams["sample_rate"] = 8000
    changed_config = runtime.embedding_adapter(pipeline)[1]
    assert changed_config["revision"] != changed_weights["revision"]
    source.write_text("implementation two")
    assert runtime.embedding_adapter(pipeline)[1]["revision"] != changed_config["revision"]
    monkeypatch.setattr(runtime, "version", lambda p: "unsupported")
    assert runtime.embedding_adapter(pipeline) is None


@pytest.mark.parametrize("forced", [None, 1])
def test_optional_voice_failures_or_forced_counts_preserve_diarization(monkeypatch, forced):
    calls = []

    def unsupported(pipeline):
        calls.append(True)
        raise RuntimeError("private model path or access credential")

    monkeypatch.setattr(runtime, "embedding_adapter", unsupported)
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        frombuffer=lambda *a, **kw: FakePCM(),
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        tensor=lambda *a, **kw: FakeWaveform(), float32="float32", inference_mode=nullcontext,
    ))
    service = runtime.DiarizationRuntime()
    service.pipeline = lambda *a, **kw: SimpleNamespace(
        exclusive_speaker_diarization=annotation((0, 12, "original")),
    )
    result = service.diarize(io.BytesIO(wav_bytes(12 * 16000)), forced)
    assert result["turns"] == [{"start": 0, "end": 12, "speaker": "SPEAKER_00"}]
    assert result["voices"] == []
    assert "embedding_model" not in result
    assert calls == ([True] if forced is None else [])


@pytest.mark.parametrize("regular_available", [True, False])
def test_runtime_voice_response_uses_regular_labels_and_never_exclusive_purity(
    monkeypatch, regular_available,
):
    identity = {"id": "actual-model", "revision": "a" * 64, "dimension": 2,
                "normalization": "l2-v1"}

    def extractor(bounds):
        return [[1, 0]] if bounds[1] <= 18 * 16000 else [[0, 1]]

    monkeypatch.setattr(runtime, "embedding_adapter", lambda pipeline: (extractor, identity))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        frombuffer=lambda *a, **kw: FakePCM(),
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        tensor=lambda *a, **kw: FakeWaveform(), float32="float32", inference_mode=nullcontext,
    ))
    output = SimpleNamespace(exclusive_speaker_diarization=annotation(
        (0, 20, "first"), (20, 40, "second")), speaker_embeddings=[[99, 99], [99, 99]],
    )
    if regular_available:
        output.speaker_diarization = annotation((18, 40, "second"), (0, 22, "first"))
    service = runtime.DiarizationRuntime()
    service.pipeline = lambda *a, **kw: output
    result = service.diarize(io.BytesIO(wav_bytes(40 * 16000)), None)
    assert len(result["turns"]) == 2
    if regular_available:
        assert result["embedding_model"] == identity
        assert [voice["speaker"] for voice in result["voices"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert [voice["embedding"] for voice in result["voices"]] == [[1, 0], [0, 1]]
        assert all(voice["clean_seconds"] == 17.7 for voice in result["voices"])
    else:
        assert result["voices"] == []
