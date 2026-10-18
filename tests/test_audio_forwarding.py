"""Official-format binary requests through durable archive, worker and recall APIs."""

import asyncio
import io
import logging
import struct
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.audio_forwarding import PATH, RedactForwardingToken
from omi_local.domain import stamp, wav_duration
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one

TOKEN = "test_receiver_secret_1234567890abcdef"
PCM = struct.pack("<4h", -32768, -500, 500, 32767) * 4000


@pytest.fixture
def receiver(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        worker_enabled=False,
        silence_threshold=0,
        audio_forwarding_enabled=True,
        audio_forwarding_token=TOKEN,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def send(client, pcm=PCM, rate=16000, uid="user123", key=None, **kwargs):
    headers = {"Content-Type": "application/octet-stream"}
    if key is not None:
        headers["Idempotency-Key"] = key
    headers.update(kwargs.pop("headers", {}))
    return client.post(
        PATH,
        params={"token": TOKEN, "sample_rate": rate, "uid": uid},
        content=pcm,
        headers=headers,
        **kwargs,
    )


@pytest.mark.parametrize("rate", [8000, 16000])
def test_playable_sample_rate_duration_and_estimated_provenance(receiver, rate):
    before = datetime.now().astimezone()
    response = send(receiver, rate=rate)
    after = datetime.now().astimezone()
    assert response.status_code == 200
    accepted = response.json()
    assert not accepted["duplicate"]
    row = receiver.get(f"/v1/sources/{accepted['id']}").json()
    assert row["state"] == "queued" and row["text"] is None
    assert row["source_kind"] == "omi_audio_webhook" and row["source_id"] == "user123"
    assert row["timestamp_basis"] == "receipt_end_estimate"
    assert row["source_metadata"]["input_sample_rate"] == rate
    end = datetime.fromisoformat(row["ended_at"])
    start = datetime.fromisoformat(row["started_at"])
    assert before <= end <= after
    assert end - start == timedelta(seconds=len(PCM) / (rate * 2))
    audio = receiver.get(f"/v1/audio/{row['id']}").content
    assert wav_duration(audio) == len(PCM) / (rate * 2)
    with wave.open(io.BytesIO(audio), "rb") as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (
            1,
            2,
            16000,
        )
        samples = struct.unpack("<8h", stream.readframes(8))
    assert (
        samples[:4] == (-32768, -500, 500, 32767)
        if rate == 16000
        else (samples[::2] == (-32768, -500, 500, 32767))
    )
    assert receiver.get("/v1/status").json()["ingestion"]["last_captured_at"] is None
    coverage = receiver.get(
        "/v1/completeness",
        params={
            "start": stamp(start),
            "end": stamp(end),
        },
    ).json()
    assert coverage["counts"]["webhook_receipt_estimates"] == 1
    assert coverage["seconds"]["available_audio"] == 0
    assert coverage["seconds"]["estimated_audio"] > 0
    assert coverage["unknown_count"] == 1


def test_worker_and_existing_playback_rewind_diary_chat(receiver):
    accepted = send(receiver).json()
    memory = receiver.app.state.memory
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=(
                    {"text": "The kitchen arrives Friday"}
                    if request.url.path.endswith("transcriptions")
                    else {
                        "choices": [{"message": {"content": f"Delivery Friday [{accepted['id']}]"}}]
                    }
                ),
            )
        )
    )
    memory.inference.client = http
    assert memory.store.get_audio(accepted["id"])["attempts"] == 0  # No inference on POST.
    assert asyncio.run(process_one(memory))
    source = receiver.get(f"/v1/sources/{accepted['id']}").json()
    assert source["state"] == "done"
    assert receiver.get("/v1/transcripts?q=kitchen").json()["items"][0]["id"] == accepted["id"]
    end = source["ended_at"]
    rewind = receiver.post("/v1/rewind", json={"minutes": 1, "end": end, "summarize": False})
    assert rewind.status_code == 200 and rewind.json()["sources"][0]["id"] == accepted["id"]
    playable = receiver.get("/v1/rewind/audio", params={"minutes": 1, "end": end})
    assert playable.status_code == 200 and wav_duration(playable.content) == 60
    at = datetime.fromisoformat(source["started_at"])
    # Recap selects the actual local calendar day, including midnight boundaries.
    from zoneinfo import ZoneInfo

    day = at.astimezone(ZoneInfo(memory.settings.timezone)).date()
    recap = asyncio.run(memory.recap(day))
    # Receipt-time estimates cannot establish a continuous recorded pause.
    assert recap["summary"] is None
    assert recap["deferred_conversations"] == 1
    assert recap["waiting_for_pause"] == 1
    assert "estimated timestamps" in recap["boundary_note"]
    chat = receiver.post("/v1/chat", json={"question": "When does the kitchen arrive?"})
    assert chat.status_code == 200
    assert accepted["id"] in chat.text
    asyncio.run(http.aclose())


@pytest.mark.parametrize(
    "pcm,rate,headers,status",
    [
        (b"", 16000, {}, 422),
        (b"\x01", 16000, {}, 422),
        (PCM, 44100, {}, 422),
        (PCM, 0, {}, 422),
        (PCM, 16000, {"Content-Type": "audio/wav"}, 415),
        (PCM, 16000, {"Content-Encoding": "gzip"}, 415),
        (b"\x00\x00" * (16000 * 120 + 1), 16000, {}, 413),
        (b"\x00\x00" * (8000 * 120 + 1), 8000, {}, 413),
        (PCM, 16000, {"Content-Length": "4000001"}, 413),
    ],
)
def test_invalid_payloads_do_not_create_files_or_jobs(receiver, pcm, rate, headers, status):
    assert send(receiver, pcm, rate=rate, headers=headers).status_code == status
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 0
    assert list(receiver.app.state.memory.store.audio_dir.iterdir()) == []


def test_streaming_request_is_bounded_without_content_length(receiver):
    def parts():
        for _ in range(130):
            yield b"\x00\x00" * 16000

    assert send(receiver, parts()).status_code == 413
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"uid": "../other", "sample_rate": "16000"},
        {"uid": "", "sample_rate": "16000"},
        {"uid": "x" * 129, "sample_rate": "16000"},
        {"uid": "u"},
        {"sample_rate": "16000"},
        [("uid", "one"), ("uid", "two"), ("sample_rate", "16000")],
    ],
)
def test_malformed_query_metadata(receiver, params):
    assert (
        receiver.post(
            PATH,
            params=[("token", TOKEN)]
            + (list(params.items()) if isinstance(params, dict) else params),
            content=PCM,
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 422
    )


def test_token_enablement_retired_pause_and_sanitized_settings(receiver, tmp_path):
    assert receiver.post(PATH, content=PCM).status_code == 401
    assert TOKEN not in receiver.get("/v1/settings").text
    assert TOKEN not in receiver.get(PATH + "/status").text
    assert receiver.patch("/v1/settings", json={"capture_paused": True}).status_code == 200
    assert send(receiver).status_code == 200  # Legacy shared pause no longer controls devices.
    receiver.patch(
        "/v1/settings", json={"capture_paused": False, "audio_forwarding_enabled": False}
    )
    assert send(receiver).status_code == 404
    settings = Settings(
        data_dir=tmp_path / "unconfigured",
        worker_enabled=False,
        audio_forwarding_enabled=True,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as bare:
        assert send(bare).status_code == 503
        assert bare.get(PATH + "/status").json()["configured"] is False
    settings = Settings(data_dir=tmp_path / "default", worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as default:
        assert send(default).status_code == 404


def test_keyed_retry_survives_restart_conflicts_and_deletion(receiver):
    first = send(receiver, key="delivery-1").json()
    memory = receiver.app.state.memory
    memory.store = Store(memory.store.root)
    retry = send(receiver, key="delivery-1").json()
    assert retry["id"] == first["id"] and retry["duplicate"]
    assert retry["accepted_at"] == first["accepted_at"]
    assert send(receiver, pcm=b"\x01\x00", key="delivery-1").status_code == 409
    assert send(receiver, rate=8000, key="delivery-1").status_code == 409
    assert receiver.delete(f"/v1/audio/{first['id']}").status_code == 200
    assert send(receiver, key="delivery-1").json()["duplicate"]
    assert receiver.get(f"/v1/audio/{first['id']}").status_code == 404
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 1


def test_unkeyed_identical_audio_is_preserved_and_users_are_separate(receiver):
    one, two = send(receiver).json(), send(receiver).json()
    assert one["id"] != two["id"]
    a = send(receiver, uid="one", key="same-delivery").json()
    b = send(receiver, uid="two", key="same-delivery").json()
    arow = receiver.get(f"/v1/sources/{a['id']}").json()
    brow = receiver.get(f"/v1/sources/{b['id']}").json()
    assert arow["device_id"] != brow["device_id"]
    assert arow["source_id"] == "one" and brow["source_id"] == "two"


def test_concurrent_identical_retries_accept_once(receiver):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: send(receiver, key="concurrent"), range(4)))
    assert all(result.status_code == 200 for result in results)
    assert len({result.json()["id"] for result in results}) == 1
    assert sum(not result.json()["duplicate"] for result in results) == 1
    assert len(list(receiver.app.state.memory.store.audio_dir.iterdir())) == 1


def test_concurrent_users_and_chunks_do_not_merge(receiver):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: send(receiver, uid=f"user{i}", key="same"), range(4)))
    assert all(result.status_code == 200 for result in results)
    rows = [receiver.get(f"/v1/sources/{result.json()['id']}").json() for result in results]
    assert len({row["device_id"] for row in rows}) == 4
    assert len({row["id"] for row in rows}) == 4


def test_storage_failure_is_not_acknowledged_and_retry_can_succeed(receiver, monkeypatch):
    import omi_local.forwarding_store as persistence

    original = persistence.os.fsync

    def no_space(_):
        raise OSError("sensitive path detail")

    monkeypatch.setattr(persistence.os, "fsync", no_space)
    response = send(receiver, key="disk-failed")
    assert response.status_code == 503 and "sensitive" not in response.text
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 0
    assert list(receiver.app.state.memory.store.audio_dir.iterdir()) == []
    monkeypatch.setattr(persistence.os, "fsync", original)
    assert send(receiver, key="disk-failed").status_code == 200


def test_acceptance_survives_restart_and_model_failure(receiver):
    accepted = send(receiver).json()
    memory = receiver.app.state.memory
    memory.store = Store(memory.store.root)

    class Unavailable:
        async def transcribe(self, _):
            raise RuntimeError("private model response")

    memory.inference = Unavailable()
    assert asyncio.run(process_one(memory))
    row = memory.store.get_audio(accepted["id"])
    assert row["state"] == "queued" and row["error_code"] == "provider_error"
    assert Path(row["path"]).is_file()
    assert "private" not in row["error"]
    assert send(receiver).status_code == 200


def test_native_capture_is_unchanged(receiver):
    from omi_local.audio_forwarding import pcm_wav

    key = uuid4()
    started = datetime.now().astimezone() - timedelta(seconds=2)
    response = receiver.put(
        f"/v1/audio/{key}",
        content=pcm_wav(PCM, 16000),
        params={"device_id": "native", "started_at": stamp(started)},
    )
    assert response.status_code == 200 and response.json()["source_kind"] == ""
    assert response.json()["timestamp_basis"] == "capture"
    assert send(receiver).status_code == 200
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 1


def test_query_secret_removed_before_access_logging(caplog):
    async def downstream(scope, receive, send):
        assert scope["state"]["forwarding_token"] == TOKEN
        logging.getLogger("uvicorn.access").info("%s?%s", scope["path"], scope["query_string"])

    caplog.set_level(logging.INFO)
    scope = {
        "type": "http",
        "path": PATH,
        "query_string": f"token={TOKEN}&sample_rate=16000&uid=user123".encode(),
    }
    asyncio.run(RedactForwardingToken(downstream)(scope, None, None))
    assert TOKEN not in caplog.text and "sample_rate" in caplog.text


def test_four_inflight_requests_apply_backpressure_before_buffering(receiver):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        readers = 0

        async def body():
            nonlocal readers
            readers += 1
            if readers == 4:
                started.set()
            await release.wait()
            yield PCM

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=receiver.app), base_url="http://testserver"
        ) as http:

            async def post(index):
                return await http.post(
                    PATH,
                    params={"token": TOKEN, "sample_rate": 16000, "uid": f"busy{index}"},
                    content=body(),
                    headers={"Content-Type": "application/octet-stream"},
                )

            tasks = [asyncio.create_task(post(i)) for i in range(4)]
            await asyncio.wait_for(started.wait(), timeout=2)
            try:
                rejected = await post(5)
                assert rejected.status_code == 429
                assert readers == 4  # The fifth body was never requested.
            finally:
                release.set()
            assert all(result.status_code == 200 for result in await asyncio.gather(*tasks))

    asyncio.run(exercise())
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 4


def test_body_timeout_accepts_nothing(receiver, monkeypatch):
    async def timeout(coroutine, timeout):
        coroutine.close()
        raise TimeoutError

    monkeypatch.setattr("omi_local.audio_forwarding.asyncio.wait_for", timeout)
    assert send(receiver, key="timed-out").status_code == 408
    assert receiver.get(PATH + "/status").json()["accepted_requests"] == 0


@pytest.mark.parametrize("key", ["", "invalid space", "../bad", "x" * 129])
def test_malformed_delivery_keys_rejected(receiver, key):
    assert send(receiver, key=key).status_code == 422


def test_receipts_and_provenance_survive_backup_restore_without_secret(receiver, tmp_path):
    import runpy
    import shutil
    import zipfile

    archive_tools = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/archive.py"))
    accepted = send(receiver, key="backup-retry").json()
    store = receiver.app.state.memory.store
    backup = store.export_archive()
    try:
        with zipfile.ZipFile(backup) as archive:
            assert TOKEN.encode() not in archive.read("omi.sqlite3")
        restored_dir = tmp_path / "restored"
        archive_tools["restore"](backup, restored_dir, max_bytes=10_000_000)
        restored = Store(restored_dir)
        assert restored.forwarding_status()["accepted_requests"] == 1
        row = restored.get_audio(accepted["id"])
        assert row["source_kind"] == "omi_audio_webhook" and row["source_id"] == "user123"
        assert Path(row["path"]).is_file()
        receiver.app.state.memory.store = restored
        assert send(receiver, key="backup-retry").json()["id"] == accepted["id"]
    finally:
        shutil.rmtree(backup.parent)
