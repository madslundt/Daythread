import asyncio
import io
import threading
import wave
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from omi_local.api import create_app
from omi_local.blob_storage import LocalAudioStorage
from omi_local.settings import Settings
from omi_local.store import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cancelled_audio_request_joins_inflight_storage_write(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowStorage(LocalAudioStorage):
        def write(self, audio_id, data):
            entered.set()
            release.wait(5)
            super().write(audio_id, data)

    store = Store(tmp_path, storage=SlowStorage(tmp_path / "audio"))
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    app.state.profiles = SimpleNamespace(runtimes={
        "default": SimpleNamespace(memory=SimpleNamespace(store=store)),
    })
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        recording.writeframes(b"\x10\x10" * 16000)
    body, key = output.getvalue(), uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://archive") as client:
        task = asyncio.create_task(client.put("/v1/audio/" + str(key), content=body, params={
            "device_id": "phone", "started_at": "2026-10-17T10:00:00Z",
        }))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.03)
            assert not task.done()
            task.cancel()  # Repeated cancellation must not abandon the write either.
            await asyncio.sleep(0.03)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.get_audio(str(key)) is not None
            assert store.read_audio(str(key)) == body
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
