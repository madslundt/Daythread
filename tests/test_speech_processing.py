"""Signal/pipeline invariants, not speech-recognition quality measurements."""

import asyncio
import io
import json
import math
import shutil
import sys
import wave
from array import array
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.audio import amplify_quiet_audio, contains_speech_energy, transcription_chunks
from omi_local.inference import Inference
from omi_local.service import public_source
from omi_local.settings import Settings
from omi_local.speech_processing import EnhancementError, enhance_audio
from omi_local.store import Store
from omi_local.worker import process_one


@pytest.fixture
def anyio_backend():
    return "asyncio"


def wav(samples):
    pcm = array("h", samples)
    if sys.byteorder != "little":
        pcm.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(pcm.tobytes())
    return output.getvalue()


def samples(data):
    with wave.open(io.BytesIO(data)) as stream:
        values = array("h", stream.readframes(stream.getnframes()))
    if sys.byteorder != "little":
        values.byteswap()
    return values


def tone(seconds=1, amplitude=20):
    # A faint component plus a second simultaneous voice-like component; synthetic only.
    return wav(round(amplitude * (math.sin(i * 0.1) + 0.2 * math.sin(i * 0.07)))
               for i in range(round(seconds * 16000)))


@pytest.mark.anyio
@pytest.mark.parametrize("preserve,expect_stt", [(True, True), (False, False)])
async def test_subthreshold_signal_reaches_stt_by_default(tmp_path, preserve, expect_stt):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, stt_preserve_quiet=preserve, worker_enabled=False
    )
    store = Store(tmp_path)
    data = tone()
    assert not contains_speech_energy(data, 35)
    assert contains_speech_energy(data, None)
    key = str(uuid4())
    store.ingest(key, "omi", datetime.now(UTC), data, 1)
    calls = []

    async def transcribe(data):
        calls.append(data)
        return "Quiet voice"

    memory = SimpleNamespace(
        store=store, settings=settings, inference=SimpleNamespace(transcribe=transcribe)
    )
    await process_one(memory)
    assert bool(calls) == expect_stt
    row = store.get_audio(key)
    assert Path(row["path"]).read_bytes() == data
    assert row["text"] == ("Quiet voice" if expect_stt else "")


def test_gain_is_uniform_bounded_and_never_clips_or_amplifies_silence():
    data = tone(amplitude=2)
    prepared, db = amplify_quiet_audio(data, 12)
    assert db == 12
    before, after = samples(data), samples(prepared)
    assert len(before) == len(after)
    assert all(abs(new - old * 10 ** (12 / 20)) <= 0.5 for old, new in zip(before, after))
    assert amplify_quiet_audio(wav([0] * 16000), 12)[1] == 0
    assert amplify_quiet_audio(data, 0) == (data, 0)
    # Peak headroom wins even when one loud speaker sits over a quiet background.
    mixture = wav([100] * 15999 + [-32768])
    assert amplify_quiet_audio(mixture, 24) == (mixture, 0)
    assert not contains_speech_energy(wav([0] * 16000), None)
    assert contains_speech_energy(wav([0] * 15999 + [1]), None)
    assert contains_speech_energy(wav([0] * 16000), 0)  # Legacy gate-disable contract.


def test_quiet_boundaries_preserve_all_samples_and_source_offsets():
    original = samples(tone(2.8, 3000)) + array("h", [0] * 6400)
    original += samples(tone(4, 3000))
    data = wav(original)
    chunks = list(transcription_chunks(data, seconds=3, boundary_search_seconds=0.5))
    assert 2.8 < chunks[1][0] < 3
    recovered = array("h")
    for offset, chunk in chunks:
        assert offset == len(recovered) / 16000
        decoded = samples(chunk)
        assert 0 < len(decoded) <= 48000
        recovered.extend(decoded)
    assert recovered == original
    # An entirely faint window is not enough evidence to cut inside quiet speech.
    faint = list(transcription_chunks(tone(3), seconds=1, boundary_search_seconds=0.5))
    assert [offset for offset, _ in faint] == [0, 1, 2]


@pytest.mark.anyio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Optional FFmpeg not installed")
@pytest.mark.parametrize("frames", [1, 399, 400, 401, 16000, 48000])
async def test_real_full_filter_chain_preserves_length_and_end_events(frames):
    signal = [0] * frames
    signal[0] = signal[-1] = 10000
    enhanced = samples(await enhance_audio(wav(signal)))
    assert len(enhanced) == frames
    # Both onset and tail stay at their original sample positions after compensation.
    assert abs(enhanced[0]) > 1000
    assert abs(enhanced[-1]) > 1000
    if frames > 800:
        assert max(abs(s) for s in enhanced[400:-400]) < 100


@pytest.mark.anyio
@pytest.mark.parametrize("output", ["short", "long", "failure", "hang"])
async def test_invalid_or_cancelled_filter_cleans_up_process(tmp_path, monkeypatch, output):
    fake = tmp_path / "ffmpeg"
    body = {
        "short": "sys.stdout.buffer.write(b'\\0' * 10)",
        "long": "sys.stdout.buffer.write(b'\\0' * 100000)",
        "failure": "sys.exit(1)",
        "hang": "time.sleep(60)",
    }[output]
    fake.write_text(f"#!{sys.executable}\nimport sys, time\nsys.stdin.buffer.read()\n{body}\n")
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    created = []
    launch = asyncio.create_subprocess_exec

    async def record_process(*args, **kwargs):
        process = await launch(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_process)
    with pytest.raises((EnhancementError, TimeoutError)):
        async with asyncio.timeout(0.2):
            await enhance_audio(tone())
    assert created and created[0].returncode is not None


@pytest.mark.anyio
@pytest.mark.parametrize("original", ["", "The quiet speaker said Friday"])
async def test_cleanup_disagreement_is_review_only_and_never_indexed(
    tmp_path, monkeypatch, original
):
    async def fake_enhance(data):
        return data

    monkeypatch.setattr("omi_local.inference.enhance_audio", fake_enhance)
    settings = Settings(
        data_dir=tmp_path, _env_file=None, worker_enabled=False,
        stt_enhancement_mode="compare", stt_segment_timestamps=True,
    )
    store = Store(tmp_path)
    key = str(uuid4())
    data = tone(2)
    at = datetime.now(UTC) - timedelta(days=2)
    store.ingest(key, "omi", at, data, 2)
    calls = []

    def handler(request):
        calls.append(request)
        text = original if len(calls) == 1 else "Unreviewed cleanup possibility"
        return httpx.Response(200, json={
            "text": text, "segments": [{"start": 0.1, "end": 1.9, "text": text}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        memory = SimpleNamespace(
            store=store, settings=settings, inference=Inference(settings, client)
        )
        await process_one(memory)
    row = store.get_audio(key)
    assert row["text"] == original
    assert Path(row["path"]).read_bytes() == data
    details = public_source(row)["transcription_details"]
    chunk = details["chunks"][0]
    assert chunk["candidate"]["text"] == "Unreviewed cleanup possibility"
    assert chunk["selected"] == "original"
    assert store.search("Unreviewed", 10) == []
    # Audited correction is the only way a candidate becomes evidence.
    store.correct_transcript(key, "Reviewed Friday")
    assert store.search("Reviewed", 10)
    assert public_source(store.get_audio(key))["transcription_details"] == {}
    # Candidate text must not outlive the transcript retention policy.
    store.complete(key, original, details=details)
    store.apply_retention(0, 1)
    assert json.loads(store.get_audio(key)["transcription_details"]) == {}


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["missing", "timeout", "provider", "malformed"])
async def test_optional_failure_never_discards_completed_original(tmp_path, monkeypatch, fault):
    async def fake_enhance(data):
        if fault == "missing":
            raise FileNotFoundError("private path")
        if fault == "timeout":
            await asyncio.sleep(60)
        return data

    monkeypatch.setattr("omi_local.inference.enhance_audio", fake_enhance)
    settings = Settings(
        data_dir=tmp_path, _env_file=None, stt_enhancement_mode="compare",
        stt_enhancement_timeout_seconds=0.05,
    )
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(503) if fault == "provider" else httpx.Response(200, json=[])
        return httpx.Response(200, json={"text": "Original quiet words"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result = await inference.transcribe_result(tone())
    assert result["text"] == "Original quiet words"
    chunk = result["processing"]["chunks"][0]
    assert chunk["enhancement_status"] == {
        "missing": "unavailable", "timeout": "timeout", "provider": "failed", "malformed": "failed"
    }[fault]
    assert "private path" not in json.dumps(result)
    assert inference.health["stt"]["state"] == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("mode,original,count", [
    ("off", "", 1), ("on_empty", "", 2), ("on_empty", "Speech", 1),
    ("compare", "Speech", 2),
])
async def test_cleanup_policy_controls_optional_requests(
    tmp_path, monkeypatch, mode, original, count
):
    async def unchanged(data):
        return data

    monkeypatch.setattr("omi_local.inference.enhance_audio", unchanged)
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enhancement_mode=mode)
    calls, progress = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": original if len(calls) == 1 else "Alternative"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Inference(settings, client).transcribe_result(
            tone(), on_progress=lambda *update: progress.append(update),
        )
    assert len(calls) == count
    expected = [("transcribing", 0, 1), ("transcribing", 1, 1)]
    if count == 2:
        expected += [("enhancing", 0, 1), ("enhancing", 1, 1)]
    assert progress == expected
    assert result["text"] == original  # Even empty-original alternatives stay out of evidence.


@pytest.mark.anyio
async def test_all_originals_precede_cleanup_with_one_shared_optional_budget(tmp_path, monkeypatch):
    calls = []

    async def slow_enhance(data):
        calls.append("enhance")
        await asyncio.sleep(0.04)
        return data

    monkeypatch.setattr("omi_local.inference.enhance_audio", slow_enhance)
    settings = Settings(
        data_dir=tmp_path, _env_file=None, stt_chunk_seconds=1,
        stt_enhancement_mode="compare", stt_enhancement_timeout_seconds=0.06,
    )

    def handler(request):
        calls.append("stt")
        return httpx.Response(200, json={"text": "Go go"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Inference(settings, client).transcribe_result(tone(3))
    assert calls[:3] == ["stt"] * 3
    assert result["text"] == "Go go\nGo go\nGo go"  # Never deduplicate actual repeated speech.
    assert [c["enhancement_status"] for c in result["processing"]["chunks"]] == [
        "complete", "timeout", "timeout",
    ]


def test_settings_are_validated_described_and_persisted(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    with TestClient(create_app(settings)) as client:
        config = client.get("/v1/configuration").json()
        assert config["values"]["stt_preserve_quiet"] is True
        changes = {"stt_enhancement_mode": "compare", "stt_max_gain_db": 6}
        saved = client.patch("/v1/configuration", json={
            "revision": config["revision"], "changes": changes,
        })
        assert saved.status_code == 200
        for change in (
            {"stt_enhancement_mode": "unknown"}, {"stt_max_gain_db": 25},
            {"stt_boundary_search_seconds": 1}, {"stt_enhancement_timeout_seconds": 0},
        ):
            assert client.patch("/v1/configuration", json={
                "revision": saved.json()["revision"], "changes": change,
            }).status_code == 422
    with TestClient(create_app(settings)) as client:
        values = client.get("/v1/configuration").json()["values"]
        assert all(values[key] == value for key, value in changes.items())


@pytest.mark.parametrize("outcome,allowed", [
    ("silence_gate", True), ("empty_stt", True), ("corrected", False), ("text", False),
])
def test_explicit_retry_can_recover_old_empty_recordings_only(tmp_path, outcome, allowed):
    settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.memory.store
        key = str(uuid4())
        data = tone()
        store.ingest(key, "omi", datetime.now(UTC), data, 1)
        store.complete(key, "" if outcome != "text" else "Keep words", outcome=outcome,
                       details={"chunks": [{"candidate": {"text": "Stale candidate"}}]})
        response = client.post(f"/v1/audio/{key}/retry")
        assert response.status_code == (200 if allowed else 409)
        row = store.get_audio(key)
        assert Path(row["path"]).read_bytes() == data
        if allowed:
            assert row["state"] == "queued"
            assert json.loads(row["transcription_details"]) == {}
        else:
            assert row["state"] == "done"


@pytest.mark.anyio
async def test_candidates_never_enter_chat_or_summary_model_prompts(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    source = {
        "id": str(uuid4()), "started_at": datetime.now(UTC).isoformat(),
        "text": "Verified original speech",
        "transcription_details": {"chunks": [{"candidate": {"text": "Unreviewed secret"}}]},
    }
    prompts = []

    def handler(request):
        prompts.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{
            "message": {"content": f"Original [{source['id']}]"}, "finish_reason": "stop",
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        await inference.chat("What was said?", [source])
        await inference.summarize([source], "summary")
    assert all("Unreviewed secret" not in prompt for prompt in prompts)
    assert all("Verified original speech" in prompt for prompt in prompts)


@pytest.mark.anyio
async def test_correction_during_cleanup_cannot_be_overwritten(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, worker_enabled=False, stt_enhancement_mode="compare"
    )
    store = Store(tmp_path)
    key = str(uuid4())
    store.ingest(key, "omi", datetime.now(UTC), tone(), 1)

    async def corrected_while_enhancing(data):
        store.correct_transcript(key, "Owner correction")
        return data

    monkeypatch.setattr("omi_local.inference.enhance_audio", corrected_while_enhancing)
    def handler(request):
        return httpx.Response(200, json={"text": "Automatic words"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await process_one(SimpleNamespace(
            settings=settings, store=store, inference=Inference(settings, client)
        ))
    row = store.get_audio(key)
    assert row["text"] == "Owner correction"
    assert json.loads(row["transcription_details"]) == {}
