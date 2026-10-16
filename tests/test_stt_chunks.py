"""Long uploads must fit the speech provider without changing archive identity."""

import asyncio
import io
import json
import wave
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from omi_local.audio import transcription_chunks
from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one


@pytest.fixture
def anyio_backend():
    return "asyncio"


def recording(seconds):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\xff\x01" * round(seconds * 16000))
    return output.getvalue()


@pytest.mark.parametrize("seconds", [1, 30, 30 + 1 / 16000, 120])
def test_pieces_preserve_every_sample_and_short_requests(seconds):
    data = recording(seconds)
    pieces = list(transcription_chunks(data))
    captured = []
    for offset, piece in pieces:
        with wave.open(io.BytesIO(piece)) as stream:
            assert stream.getnframes() <= 30 * 16000
            assert offset == len(b"".join(captured)) / 32000
            captured.append(stream.readframes(stream.getnframes()))
    with wave.open(io.BytesIO(data)) as stream:
        assert b"".join(captured) == stream.readframes(stream.getnframes())
    if seconds <= 30:
        assert pieces == [(0, data)]


@pytest.mark.anyio
async def test_pieces_share_one_deadline(tmp_path):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, inference_timeout_seconds=1,
        stt_max_gain_db=0, stt_boundary_search_seconds=0,
    )
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.6)
        return httpx.Response(200, json={"text": "Speech"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        with pytest.raises(InferenceError, match="deadline"):
            await inference.transcribe_result(recording(60))
        assert inference.health["stt"]["state"] == "unavailable"
    assert len(calls) == 2


@pytest.mark.anyio
async def test_configured_request_duration_is_used(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_chunk_seconds=12)
    durations = []

    def handler(request):
        message = BytesParser().parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
            + request.content
        )
        audio = next(
            part.get_payload(decode=True) for part in message.get_payload()
            if part.get_param("name", header="content-disposition") == "file"
        )
        with wave.open(io.BytesIO(audio)) as stream:
            durations.append(stream.getnframes() / stream.getframerate())
        return httpx.Response(200, json={"text": "Speech"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Inference(settings, client).transcribe_result(recording(35))
    assert durations == [12, 12, 11]
    assert result["text"] == "Speech\nSpeech\nSpeech"


@pytest.mark.anyio
@pytest.mark.parametrize("fail_part", [None, 3])
async def test_worker_transcribes_105_second_upload_atomically(tmp_path, fail_part):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, stt_segment_timestamps=True, stt_max_gain_db=0
    )
    store = Store(tmp_path)
    key = str(uuid4())
    data = recording(104.725375)
    at = datetime(2026, 10, 16, 21, 10, tzinfo=UTC)
    store.ingest(key, "web", at, data, 104.725375)
    durations, captured = [], []

    def handler(request):
        message = BytesParser().parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
            + request.content
        )
        audio = next(
            part.get_payload(decode=True) for part in message.get_payload()
            if part.get_param("name", header="content-disposition") == "file"
        )
        with wave.open(io.BytesIO(audio)) as stream:
            duration = stream.getnframes() / stream.getframerate()
            captured.append(stream.readframes(stream.getnframes()))
        durations.append(duration)
        if duration > 30:
            return httpx.Response(413, json={"error": "Audio duration exceeds the limit"})
        if len(durations) == fail_part:
            return httpx.Response(503)
        text = f"Part {len(durations)}"
        return httpx.Response(200, json={
            "text": text, "segments": [{"start": 0, "end": duration, "text": text}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        memory = SimpleNamespace(
            store=store, settings=settings, inference=Inference(settings, client)
        )
        assert await process_one(memory)
    result = store.get_audio(key)
    assert Path(result["path"]).read_bytes() == data
    assert result["started_at"] == at.isoformat(timespec="microseconds")
    if fail_part:
        assert durations == [30, 30, 30]
        assert result["state"] == "queued"
        assert not result["text"]  # Never publish an incomplete recording transcript.
        assert result["error_code"] == "provider_error"
    else:
        assert result["state"] == "done"
        assert result["text"] == "Part 1\nPart 2\nPart 3\nPart 4"
        assert durations == [30, 30, 30, 14.725375]
        with wave.open(io.BytesIO(data)) as stream:
            assert b"".join(captured) == stream.readframes(stream.getnframes())
        segments = json.loads(result["segments"])
        assert [segment["start"] for segment in segments] == [0, 30, 60, 90]
        assert [segment["end"] for segment in segments] == [30, 60, 90, 104.725375]
