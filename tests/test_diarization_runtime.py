"""Exercise the optional service without downloading models or importing PyTorch."""

import io
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
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
