import io
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings


@pytest.fixture
def app_recording(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, timezone="UTC", _env_file=None)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.memory.store
        rid, at = str(uuid4()), datetime(2026, 10, 20, 12, tzinfo=UTC)
        ids, texts, pcm_parts = [], [], []
        for index, text in enumerate(("First part.", "Second part.")):
            source_id = str(uuid4())
            pcm = bytes((index + 1, 0)) * 32000
            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
                wav.writeframes(pcm)
            store.ingest(
                source_id,
                "manual-web",
                at + timedelta(seconds=index * 2),
                audio.getvalue(),
                2,
                source_kind="manual_upload",
                source_id="recording.wav",
                source_metadata={"recording_id": rid, "part_index": index, "part_count": 2},
            )
            store.complete(source_id, text)
            ids.append(source_id)
            texts.append(text)
            pcm_parts.append(pcm)
        yield client, store, rid, ids, texts, b"".join(pcm_parts)
