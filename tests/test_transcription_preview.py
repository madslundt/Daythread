"""Preview admission/cancellation exercises real HTTP routes without publishing transcripts."""

import asyncio
import io
import json
import wave

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omi_local.inference import Inference
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.transcription_preview import transcription_preview_routes


def wav(seconds=1):
    data = io.BytesIO()
    with wave.open(data, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        writer.writeframes(b"\x10\x10" * int(seconds * 16000))
    return data.getvalue()


class FakeSTT:
    def __init__(self):
        self.calls = []

    async def transcribe_result(self, data):
        self.calls.append(data)
        return {"text": "A temporary preview."}


@pytest.fixture
def preview(tmp_path):
    memory = Memory(Store(tmp_path), Settings(
        data_dir=tmp_path, stt_preview_enabled=True, worker_enabled=False, _env_file=None,
    ), FakeSTT())
    app = FastAPI()
    app.include_router(transcription_preview_routes(lambda: memory))
    return app, memory


def test_preview_is_optional_ephemeral_and_does_not_create_archive_records(preview):
    app, memory = preview
    with TestClient(app) as client:
        memory.settings.stt_preview_enabled = False
        assert client.get("/v1/transcription-preview").json()["enabled"] is False
        assert client.post("/v1/transcription-preview", content=wav(),
                           headers={"Content-Type": "audio/wav"}).status_code == 409
        assert memory.inference.calls == []
        memory.settings.stt_preview_enabled = True
        response = client.post("/v1/transcription-preview", content=wav(),
                               headers={"Content-Type": "audio/wav"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "text": "A temporary preview.", "provisional": True, "saved": False,
        }
    with memory.store.connect() as db:
        for table in ("audio", "diary", "recap_summaries", "usage_events"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("content,content_type,status", [
    (b"not wav", "audio/wav", 422), (wav(16), "audio/wav", 413),
    (wav(), "application/json", 415), (b"x" * 490_000, "audio/wav", 413),
])
def test_preview_bounds_are_checked_before_recognition(preview, content, content_type, status):
    app, memory = preview
    with TestClient(app) as client:
        assert client.post("/v1/transcription-preview", content=content,
                           headers={"Content-Type": content_type}).status_code == status
    assert memory.inference.calls == []


def test_admission_has_no_queue_and_retains_cooldown_after_completion(preview):
    app, memory = preview

    async def check():
        started, release = asyncio.Event(), asyncio.Event()

        async def recognize(data):
            started.set()
            await release.wait()
            return {"text": "Temporary"}
        memory.inference.transcribe_result = recognize
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/transcription-preview", content=wav(),
                                                    headers={"Content-Type": "audio/wav"}))
            await started.wait()
            second = await client.post("/v1/transcription-preview", content=wav(),
                                       headers={"Content-Type": "audio/wav"})
            assert second.status_code == 429 and second.headers["retry-after"] == "10"
            release.set()
            assert (await first).status_code == 200
            assert (await client.post("/v1/transcription-preview", content=wav(),
                                      headers={"Content-Type": "audio/wav"})).status_code == 429
    asyncio.run(check())


def test_deadline_cancels_provider_and_never_saves_partial_text(preview, monkeypatch):
    app, memory = preview
    monkeypatch.setattr("omi_local.transcription_preview.TIMEOUT_SECONDS", 0.02)
    cancelled = []

    async def recognize(data):
        try:
            await asyncio.sleep(100)
        finally:
            cancelled.append(True)
    memory.inference.transcribe_result = recognize
    with TestClient(app) as client:
        response = client.post("/v1/transcription-preview", content=wav(),
                               headers={"Content-Type": "audio/wav"})
        assert response.status_code == 504
    assert cancelled == [True]
    assert memory.store.diary("2026-11-01") is None


def test_http_disconnect_cancels_the_actual_provider_task(preview):
    app, memory = preview

    async def check():
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls, sent = [], []

        async def recognize(data):
            started.set()
            try:
                await asyncio.sleep(100)
            finally:
                cancelled.set()
        memory.inference.transcribe_result = recognize

        async def receive():
            if not calls:
                calls.append(True)
                return {"type": "http.request", "body": wav(), "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "POST", "scheme": "http", "path": "/v1/transcription-preview",
                 "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"audio/wav")],
                 "server": ("test", 80), "client": ("test", 1)}
        await asyncio.wait_for(app(scope, receive, send), 1)
        assert cancelled.is_set()
        assert next(message["status"] for message in sent
                    if message["type"] == "http.response.start") == 499
    asyncio.run(check())


def test_real_stt_reuses_vocabulary_and_records_only_provider_attempts(preview):
    app, memory = preview
    memory.settings.stt_filter_speech = False
    memory.settings.stt_enhancement_mode = "compare"
    memory.settings.stt_vocabulary = ["Daythread"]
    memory.settings.stt_vocabulary_mode = "prompt"
    submitted = []

    def provider(request):
        submitted.append(request.content)
        return httpx.Response(200, json={"text": "Daythread"})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
            memory.inference = Inference(memory.settings, provider_client)
            memory.inference.usage_recorder = memory.usage.record
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://test") as client:
                result = await client.post("/v1/transcription-preview", content=wav(),
                                           headers={"Content-Type": "audio/wav"})
                assert result.status_code == 200 and result.json()["text"] == "Daythread"
    asyncio.run(check())
    assert len(submitted) == 1 and b"Daythread" in submitted[0]
    assert memory.settings.stt_enhancement_mode == "compare"
    with memory.store.connect() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM usage_events")]
    assert len(events) == 1 and events[0]["kind"] == "stt"
    assert events[0]["task"] == "transcription_preview" and "recording_id" not in events[0]
