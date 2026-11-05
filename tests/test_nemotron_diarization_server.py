"""The optional native service remains local and keeps model overlap evidence."""

import hashlib
import io
import json
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException

from scripts import nemotron_diarization_server as service
from scripts import nemotron_provenance as provenance


def wav_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        audio.writeframes(b"\x01\x00" * 16000 * seconds)
    return output.getvalue()


def test_result_validation_keeps_overlapping_speaker_tracks():
    turns = service.normalize_segments({"segments": [
        {"start": 0.2, "end": 0.9, "speaker": 1},
        {"start": 0.6, "end": 1.0, "speaker": 2},
    ]}, 1.0)
    assert turns == [
        {"start": 0.2, "end": 0.9, "speaker": "SPEAKER_00"},
        {"start": 0.6, "end": 1.0, "speaker": "SPEAKER_01"},
    ]


@pytest.mark.parametrize("segment", [
    {"start": True, "end": 0.5, "speaker": 1},
    {"start": 0.2, "end": float("nan"), "speaker": 1},
    {"start": -0.1, "end": 0.5, "speaker": 1},
    {"start": 0.2, "end": 1.2, "speaker": 1},
    {"start": 0.5, "end": 0.2, "speaker": 1},
    {"start": 0.2, "end": 0.5, "speaker": 9},
])
def test_result_validation_rejects_malformed_or_unbounded_segments(segment):
    with pytest.raises(ValueError):
        service.normalize_segments({"segments": [segment]}, 1.0)


def test_explicit_local_runtime_path_is_used_with_offline_cpu_flags(tmp_path):
    binary = tmp_path / "nemo-speech"
    model = tmp_path / "model.gguf"
    binary.touch()
    model.touch()
    (tmp_path / "PROVENANCE.txt").touch()
    binary.chmod(0o755)
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        result_file = args[args.index("--output") + 1]
        with open(result_file, "w") as output:
            json.dump({"segments": [{"start": 0.0, "end": 1.0, "speaker": 1}]}, output)
        return subprocess.CompletedProcess(args, 0)

    runtime = service.NemotronRuntime(str(binary), str(model), runner,
                                      verifier=lambda *_: {
                                          "model": service.MODEL_ID,
                                          "model_revision": service.MODEL_REVISION,
                                          "runtime": "NeMo-Speech.cpp",
                                          "runtime_version": service.RUNTIME_VERSION,
                                      })
    response = runtime.diarize(io.BytesIO(wav_bytes()))
    args, options = calls[0]
    assert args[:6] == [str(binary), "diarize", args[2], "--model", str(model), "--device"]
    assert "cpu" in args and "--format" in args and "json" in args
    assert options["env"]["HF_HUB_OFFLINE"] == "1"
    assert options["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert response["model_revision"] == service.MODEL_REVISION
    assert response["turns"] == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]


@pytest.mark.parametrize("encoder_fails", [False, True])
def test_voiceprint_extraction_preserves_original_audio_and_diarization(tmp_path, encoder_fails):
    binary, model = tmp_path / "nemo-speech", tmp_path / "model.gguf"
    binary.touch()
    model.touch()
    (tmp_path / "PROVENANCE.txt").touch()
    captured = []
    vector = {"embedding_model": {"id": "fixture", "revision": "a" * 64,
                                  "dimension": 2, "normalization": "l2-v1"},
              "voices": [{"speaker": "SPEAKER_00", "embedding": [1.0, 0.0],
                          "clean_seconds": 15, "overlap_ratio": 0, "clipped_ratio": 0}]}

    class Embeddings:
        def health(self):
            return {"configured": True, "loaded": True}

        def extract(self, stream, turns):
            stream.seek(0)
            captured.append((stream.read(), turns))
            if encoder_fails:
                raise RuntimeError("Optional encoder failed")
            return vector

    def runner(args, **kwargs):
        output = args[args.index("--output") + 1]
        with open(output, "w") as result:
            json.dump({"segments": [{"start": 0, "end": 20, "speaker": 1}]}, result)
        return subprocess.CompletedProcess(args, 0)

    runtime = service.NemotronRuntime(str(binary), str(model), runner,
                                      verifier=lambda *_: {"model": service.MODEL_ID},
                                      embeddings=Embeddings())
    original = wav_bytes(20)
    result = runtime.diarize(io.BytesIO(original))
    assert captured == [(original, [{"start": 0.0, "end": 20.0, "speaker": "SPEAKER_00"}])]
    assert result["turns"] == captured[0][1]
    if encoder_fails:
        assert result["voices"] == []
        assert "embedding_model" not in result
    else:
        assert result["embedding_model"] == vector["embedding_model"]
        assert result["voices"] == vector["voices"]
    assert runtime.health()["voice_embeddings"]["configured"] is True


def test_unconfigured_and_forced_count_never_start_runtime(tmp_path):
    calls = []
    runtime = service.NemotronRuntime(run=lambda *args, **kwargs: calls.append(args))
    assert runtime.health()["status"] == "unconfigured"
    with pytest.raises(HTTPException) as error:
        runtime.diarize(io.BytesIO(wav_bytes()))
    assert error.value.status_code == 503
    binary, model = tmp_path / "nemo-speech", tmp_path / "model.gguf"
    binary.touch()
    binary.chmod(0o755)
    model.touch()
    runtime = service.NemotronRuntime(str(binary), str(model),
                                      run=lambda *args, **kwargs: calls.append(args))
    with pytest.raises(HTTPException) as error:
        runtime.diarize(io.BytesIO(wav_bytes()), 2)
    assert error.value.status_code == 422
    assert calls == []


def test_inference_remains_serialized_when_http_waiter_cancels(tmp_path):
    binary, model = tmp_path / "nemo-speech", tmp_path / provenance.MODEL_FILE
    binary.touch()
    binary.chmod(0o755)
    model.touch()
    (tmp_path / "PROVENANCE.txt").touch()
    started, finish = threading.Event(), threading.Event()
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        started.set()
        assert finish.wait(5)
        output = args[args.index("--output") + 1]
        with open(output, "w") as result:
            json.dump({"segments": []}, result)
        return subprocess.CompletedProcess(args, 0)

    metadata = {"model": service.MODEL_ID, "model_revision": service.MODEL_REVISION,
                "runtime": "NeMo-Speech.cpp", "runtime_version": service.RUNTIME_VERSION}
    runtime = service.NemotronRuntime(str(binary), str(model), runner,
                                      verifier=lambda *_: metadata)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(runtime.diarize, io.BytesIO(wav_bytes()))
        assert started.wait(2)
        with pytest.raises(HTTPException) as error:
            runtime.diarize(io.BytesIO(wav_bytes()))
        assert error.value.status_code == 429
        finish.set()
        assert first.result()["turns"] == []
    assert len(calls) == 1


def test_provenance_requires_matching_manifest_and_actual_file_hashes(tmp_path, monkeypatch):
    binary = tmp_path / "nemo-speech"
    model = tmp_path / provenance.MODEL_FILE
    manifest = tmp_path / "PROVENANCE.txt"
    binary.write_bytes(b"pinned binary")
    binary.chmod(0o755)
    model.write_bytes(b"pinned model")
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    model_sha = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setitem(provenance.PLATFORMS, "Linux:x86_64", {
        "archive": "runtime.tar.gz", "archive_sha256": "archive-sha",
        "binary_sha256": binary_sha,
    })
    monkeypatch.setattr(provenance, "MODEL_SHA256", model_sha)
    manifest.write_text("\n".join([
        "Runtime: NVIDIA/NeMo-Speech.cpp v0.3.0",
        f"Runtime source commit: {provenance.RUNTIME_COMMIT}",
        "Runtime license: Apache-2.0", "Runtime archive: runtime.tar.gz",
        "Runtime archive SHA-256: archive-sha",
        f"Runtime executable SHA-256: {binary_sha}",
        f"Model: {provenance.MODEL_ID}", f"Model revision: {provenance.MODEL_REVISION}",
        "Model license: openmdw-1.1", f"Model file: {provenance.MODEL_FILE}",
        f"Model SHA-256: {model_sha}",
    ]))
    assert provenance.verify_installation(binary, model, manifest, "Linux:x86_64")
    model.write_bytes(b"different model bytes")
    assert provenance.verify_installation(binary, model, manifest, "Linux:x86_64") is None


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_http_contract_rejects_invalid_counts_and_preserves_overlap():
    pytest.importorskip("multipart")

    class FakeRuntime(service.NemotronRuntime):
        def diarize(self, stream, num_speakers=None):
            assert service.inspect_wav(stream) == 1.0
            assert num_speakers is None
            return {"model": service.MODEL_ID, "turns": [
                {"start": 0.0, "end": 0.8, "speaker": "SPEAKER_00"},
                {"start": 0.5, "end": 1.0, "speaker": "SPEAKER_01"},
            ]}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service.create_app(FakeRuntime())),
        base_url="http://local",
    ) as client:
        good = await client.post("/diarize", files={"file": ("audio.wav", wav_bytes())})
        bad = await client.post("/diarize", files={"file": ("audio.wav", wav_bytes())},
                                data={"num_speakers": "9"})
    assert good.status_code == 200
    assert good.json()["turns"][0]["end"] > good.json()["turns"][1]["start"]
    assert bad.status_code == 422
