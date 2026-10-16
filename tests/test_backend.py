import io
import wave
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.audio import render_window
from omi_local.domain import ButtonEvent, HintPolicy, project_events, stamp, wav_duration
from omi_local.inference import Inference
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one, scheduled_recaps

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


def wav(samples: list[int]) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(x.to_bytes(2, "little", signed=True) for x in samples))
    return output.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, worker_enabled=False, silence_threshold=0, _env_file=None)


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session


def put_audio(client, key, at=AT, data=None):
    return client.put(
        f"/v1/audio/{key}",
        params={"device_id": "omi", "started_at": stamp(at)},
        content=data or wav([7] * 16000),
        headers={"Content-Type": "audio/wav"},
    )


def test_api_is_accessible_without_authentication(client):
    assert "Authorization" not in client.headers
    assert put_audio(client, uuid4()).status_code == 200
    assert client.get("/v1/status").status_code == 200


def test_audio_retry_is_idempotent_and_conflict_does_not_replace_recording(client):
    key = uuid4()
    assert put_audio(client, key).status_code == 200
    assert put_audio(client, key).status_code == 200
    assert put_audio(client, key, data=wav([9] * 16000)).status_code == 409
    assert client.get("/v1/status").json()["audio"] == {"queued": 1}


def test_invalid_audio_and_timestamps_are_rejected(client):
    assert put_audio(client, uuid4(), data=b"bad-wav").status_code == 422
    response = client.put(
        f"/v1/audio/{uuid4()}",
        params={"device_id": "omi", "started_at": "2026-10-15T12:00:00"},
        content=wav([1]),
    )
    assert response.status_code == 422
    truncated = wav([1] * 16000)[:-10]
    with pytest.raises(ValueError):
        wav_duration(truncated)


def test_jobs_survive_restart_and_transcript_is_searchable(store):
    key = uuid4()
    store.ingest(key, "omi", AT, wav([1]), 1 / 16000)
    claimed = store.claim()
    assert claimed["id"] == str(key)
    restarted = Store(store.root)
    assert restarted.claim() is None
    restarted.recover()
    assert restarted.claim()["id"] == str(key)
    restarted.complete(str(key), "Vi aftalte leveringen af køkkenet fredag")
    assert restarted.search('køkkenet " OR *', 10)[0]["id"] == str(key)


def test_retry_does_not_lose_audio(store):
    key = uuid4()
    store.ingest(key, "omi", AT, wav([1]), 1 / 16000)
    job = store.claim()
    store.retry(job, "Inference temporarily unavailable")
    assert store.claim() is None
    row = store.window(AT, AT + timedelta(seconds=1))[0]
    assert row["state"] == "queued"
    assert row["attempts"] == 1
    assert row["error"] == "Inference temporarily unavailable"


def event(store, at, action="meeting_toggle", key=None):
    data = ButtonEvent(event_id=key or uuid4(), device_id="omi", occurred_at=at, action=action)
    store.append_event(data, 10, 60)
    return data


def test_delayed_button_events_replay_in_capture_order_and_deduplicate(store):
    stop = event(store, AT + timedelta(minutes=5))
    event(store, AT)
    store.append_event(stop, 999, 999)
    state = project_events(store.events(), AT + timedelta(minutes=7))
    assert len(state["meetings"]) == 1
    assert state["meetings"][0]["ended_at"] == stamp(AT + timedelta(minutes=5))
    assert state["active_meeting"] is None


def test_meeting_auto_stop_survives_restart_and_next_toggle_starts_new_meeting(store):
    event(store, AT)
    expired = project_events(Store(store.root).events(), AT + timedelta(minutes=11))
    assert expired["meetings"][0]["end_reason"] == "timeout"
    assert expired["meetings"][0]["ended_at"] == stamp(AT + timedelta(minutes=10))
    event(store, AT + timedelta(minutes=12))
    assert project_events(store.events(), AT + timedelta(minutes=13))["active_meeting"]


def test_marker_contains_configured_previous_context(store):
    event(store, AT, "mark")
    mark = project_events(store.events(), AT)["markers"][0]
    assert mark["context_start"] == stamp(AT - timedelta(seconds=60))


def test_marker_cooldown_uses_last_accepted_capture_time_and_survives_restart(store):
    # Delayed uploads arrive in reverse order; ignored clicks must not extend the cooldown.
    for seconds in (120, 119, 61, 60, 59, 20, 0):
        event(store, AT + timedelta(seconds=seconds), "mark")
    restarted = Store(store.root)
    markers = project_events(restarted.events(), AT + timedelta(minutes=3))["markers"]
    assert [mark["occurred_at"] for mark in markers] == [
        stamp(AT + timedelta(seconds=seconds)) for seconds in (0, 60, 120)
    ]
    assert len(restarted.events()) == 7  # Preserve raw history for deterministic replay.


@pytest.mark.parametrize("seconds,accepted", [(0, False), (59.999, False), (60, True), (61, True)])
def test_meeting_minimum_uses_unpadded_duration(store, seconds, accepted):
    event(store, AT)
    event(store, AT + timedelta(seconds=seconds))
    state = project_events(store.events(), AT + timedelta(minutes=5))
    assert bool(state["meetings"]) is accepted
    assert state["active_meeting"] is None


def test_active_meeting_is_provisional_for_first_minute(store):
    event(store, AT)
    early = project_events(store.events(), AT + timedelta(seconds=30))
    assert early["meetings"] == []
    assert early["active_meeting"]["status"] == "provisional"
    assert early["active_meeting"]["qualifies_at"] == stamp(AT + timedelta(seconds=60))
    confirmed = project_events(store.events(), AT + timedelta(seconds=60))
    assert len(confirmed["meetings"]) == 1
    assert confirmed["active_meeting"]["status"] == "active"


def test_short_accidental_meeting_does_not_affect_next_meeting(store):
    event(store, AT)
    event(store, AT + timedelta(seconds=20))
    start = event(store, AT + timedelta(seconds=30))
    event(store, AT + timedelta(seconds=120))
    meetings = project_events(store.events(), AT + timedelta(minutes=5))["meetings"]
    assert [meeting["id"] for meeting in meetings] == [str(start.event_id)]
    assert meetings[0]["timing"] == "approximate"
    assert meetings[0]["context_start"] == stamp(AT - timedelta(seconds=30))
    assert meetings[0]["context_end"] == stamp(AT + timedelta(seconds=180))


@pytest.mark.anyio
async def test_summary_receives_surrounding_hints_and_meeting_context(client, store, settings):
    class FakeInference:
        async def summarize(self, sources, purpose):
            assert all(source["important"] for source in sources)
            assert all(source["meeting_hint_ids"] for source in sources)
            return "Approximate meeting context"

    start = event(store, AT)
    event(store, AT + timedelta(minutes=2))
    event(store, AT, "mark")
    for at in (AT - timedelta(seconds=30), AT + timedelta(seconds=30)):
        key = uuid4()
        store.ingest(key, "omi", at, wav([1]), 1 / 16000)
        store.complete(str(key), "Context near the button press")
    memory = Memory(store, settings, FakeInference())
    client.app.state.memory.inference = FakeInference()
    result = await memory.window(AT - timedelta(minutes=1), AT + timedelta(minutes=3), "recap")
    assert len(result["sources"]) == 2
    assert result["markers"][0]["context_end"] == stamp(AT + timedelta(seconds=60))
    assert result["meetings"][0]["id"] == str(start.event_id)
    # API uses the padded retrieval range, rather than the literal button timestamps.
    response = client.post(f"/v1/meetings/{start.event_id}/recap")
    assert response.status_code == 200
    assert response.json()["start"] == stamp(AT - timedelta(seconds=60))
    assert len(response.json()["sources"]) == 2


def test_ignored_meeting_has_no_recap_endpoint(client, store):
    start = event(store, AT)
    event(store, AT + timedelta(seconds=20))
    assert client.post(f"/v1/meetings/{start.event_id}/recap").status_code == 404


def test_hint_configuration_is_captured_with_event_for_replay(store):
    policy = HintPolicy(
        marker_cooldown_seconds=90,
        marker_after_seconds=30,
        meeting_min_seconds=90,
        meeting_context_seconds=120,
    )
    for seconds in (0, 60, 90):
        data = ButtonEvent(
            event_id=uuid4(),
            device_id="omi",
            occurred_at=AT + timedelta(seconds=seconds),
            action="mark",
        )
        store.append_event(data, 10, 45, policy)
    markers = project_events(Store(store.root).events(), AT + timedelta(minutes=5))["markers"]
    assert len(markers) == 2
    assert markers[0]["context_start"] == stamp(AT - timedelta(seconds=45))
    assert markers[0]["context_end"] == stamp(AT + timedelta(seconds=30))


def test_existing_database_events_migrate_without_losing_history(tmp_path):
    import sqlite3

    key = str(uuid4())
    with sqlite3.connect(tmp_path / "omi.sqlite3") as db:
        db.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, "
            "occurred_at TEXT NOT NULL, action TEXT NOT NULL, max_minutes INTEGER NOT NULL, "
            "context_seconds INTEGER NOT NULL)"
        )
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?)", (key, "omi", stamp(AT), "mark", 10, 60)
        )
    migrated = Store(tmp_path)
    assert migrated.events()[0]["hint_policy"] == "{}"
    assert project_events(migrated.events(), AT)["markers"][0]["event_id"] == key
    assert len(Store(tmp_path).events()) == 1


@pytest.mark.anyio
async def test_diary_refreshes_for_late_hint_outside_calendar_day(store, settings):
    class FakeInference:
        calls = 0

        async def summarize(self, sources, purpose):
            self.calls += 1
            return f"important={sources[0]['important']} [{sources[0]['id']}]"

    ai = FakeInference()
    memory = Memory(store, settings.model_copy(update={"timezone": "UTC"}), ai)
    midnight, _ = memory.day_window(AT.date())
    key = uuid4()
    store.ingest(
        key, "omi", midnight + timedelta(seconds=20), wav([1]), 1 / 16000, source_kind="omi_api"
    )
    store.complete(str(key), "Speech shortly after midnight")
    event(store, midnight - timedelta(seconds=10), "mark")
    assert (await memory.recap(AT.date()))["summary"] == f"important=True [{key}]"
    # This earlier accepted flag suppresses the later one and its context ends before this speech.
    event(store, midnight - timedelta(seconds=50), "mark")
    assert (await memory.recap(AT.date()))["summary"] == f"important=False [{key}]"
    assert ai.calls == 4  # Conversation preparation and daily synthesis, before and after the hint.


def test_rewind_audio_clips_chunks_and_preserves_gap_timing(store):
    store.ingest(uuid4(), "omi", AT, wav([100] * 32000), 2)
    store.ingest(uuid4(), "omi", AT + timedelta(seconds=3), wav([200] * 16000), 1)
    start, end = AT + timedelta(seconds=1), AT + timedelta(seconds=4)
    data, gaps = render_window(store.window(start, end), start, end)
    with wave.open(io.BytesIO(data)) as result:
        assert result.getnframes() == 48000
        pcm = result.readframes(48000)
    assert pcm[:32000] == (100).to_bytes(2, "little", signed=True) * 16000
    assert pcm[32000:64000] == bytes(32000)
    assert pcm[64000:] == (200).to_bytes(2, "little", signed=True) * 16000
    assert gaps == [
        {"start": stamp(AT + timedelta(seconds=2)), "end": stamp(AT + timedelta(seconds=3))}
    ]


@pytest.mark.anyio
async def test_diary_uses_local_calendar_boundaries_and_refreshes_after_late_audio(
    store, settings, monkeypatch
):
    class FakeInference:
        calls = 0

        async def summarize(self, sources, purpose):
            self.calls += 1
            return f"Diary version {self.calls} [{sources[0]['id']}]"

    ai = FakeInference()
    memory = Memory(store, settings, ai)
    # Copenhagen's 2026-10-25 is 25 hours long because daylight saving ends.
    start, end = memory.day_window(date(2026, 10, 25))
    assert end - start == timedelta(hours=25)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: end + timedelta(hours=1))
    key = uuid4()
    store.ingest(key, "omi", start, wav([1]), 1 / 16000, source_kind="manual_upload")
    store.complete(str(key), "Morning conversation")
    first = await memory.recap(date(2026, 10, 25))
    assert (await memory.recap(date(2026, 10, 25)))["summary"] == first["summary"]
    late = uuid4()
    store.ingest(
        late, "omi", end - timedelta(minutes=1), wav([1]), 1 / 16000, source_kind="manual_upload"
    )
    store.complete(str(late), "Late upload")
    assert (await memory.recap(date(2026, 10, 25)))["summary"] == f"Diary version 4 [{key}]"
    assert ai.calls == 4  # The unchanged first conversation preparation is reused.
    assert (store.diary_dir / "2026-10-25.md").exists()


@pytest.mark.anyio
async def test_local_stt_wire_contract(settings):
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:4000/v1/audio/transcriptions"
        assert b"stt-danish" in request.content
        assert b"audio/wav" in request.content
        return httpx.Response(200, json={"text": "Hej fra Omi"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ai = Inference(settings, client)
        assert await ai.transcribe(wav([1])) == "Hej fra Omi"


def test_empty_rewind_and_chat_do_not_invent_content(client):
    rewind = client.post("/v1/rewind", json={"minutes": 10})
    assert rewind.status_code == 200
    assert rewind.json()["sources"] == []
    assert rewind.json()["summary"] is None
    chat = client.post("/v1/chat", json={"question": "What did we decide about the kitchen?"})
    assert chat.status_code == 200
    assert chat.json()["sources"] == []
    assert "No matching" in chat.json()["answer"]


@pytest.mark.anyio
async def test_complete_audio_to_local_diary_and_chat_flow(store, settings):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "The kitchen delivery is on Friday"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "The delivery is on Friday. [" + str(key) + "]"}}
                ]
            },
        )

    key = uuid4()
    store.ingest(key, "omi", AT, wav([1] * 16000), 1, source_kind="manual_upload")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        memory = Memory(store, settings, Inference(settings, http))
        assert await process_one(memory)
        await scheduled_recaps(memory)
        assert store.diary("2026-10-15")
        assert str(key) in (await memory.chat("kitchen", 10))["answer"]
        assert store.status() == {"done": 1}
    assert "/v1/audio/transcriptions" in requests
    assert "/v1/chat/completions" in requests


def test_audio_download_and_public_schema(client):
    key = uuid4()
    original = wav([42] * 16000)
    assert put_audio(client, key, data=original).status_code == 200
    response = client.get(f"/v1/audio/{key}")
    assert response.status_code == 200
    assert response.content == original
    assert client.get(f"/v1/audio/{uuid4()}").status_code == 404
    assert "/v1/events" in client.get("/v1/openapi").json()["paths"]


def test_semantic_hold_toggle_and_conflicting_event_retry(client):
    key = str(uuid4())
    event_data = {
        "event_id": key,
        "device_id": "omi",
        "occurred_at": stamp(AT),
        "action": "meeting_toggle",
    }
    assert client.post("/v1/events", json=event_data).status_code == 200
    assert client.post("/v1/events", json=event_data).status_code == 200
    assert len(client.get("/v1/timeline").json()["meetings"]) == 1
    event_data["action"] = "mark"
    assert client.post("/v1/events", json=event_data).status_code == 409
    assert client.post(f"/v1/meetings/{key}/recap").status_code == 200


@pytest.mark.anyio
async def test_provider_failure_stays_queued_and_does_not_leak_response_body(store, settings):
    key = uuid4()
    store.ingest(key, "omi", AT, wav([1]), 1 / 16000)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, text="private provider diagnostics")
        )
    ) as http:
        memory = Memory(store, settings, Inference(settings, http))
        await process_one(memory)
    row = store.get_audio(str(key))
    assert row["state"] == "queued"
    assert "private" not in row["error"]


def test_local_settings_reject_remote_providers(settings):
    with pytest.raises(ValueError):
        Settings(llm_base_url="https://example.com/v1", _env_file=None)
    assert "language" not in Settings.model_fields


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_event_receipt_identifies_suppressed_event_and_persisted_capture_policy(client):
    payload = {
        "event_id": str(uuid4()), "device_id": "pendant", "occurred_at": stamp(AT),
        "action": "mark", "meeting_max_minutes": 80, "meeting_min_seconds": 90,
        "marker_cooldown_seconds": 120,
    }
    assert client.post("/v1/events", json=payload).status_code == 200
    # The second mark is suppressed, but its actual persistence must still be acknowledged.
    payload = payload | {"event_id": str(uuid4()), "occurred_at": stamp(AT + timedelta(seconds=1))}
    receipt = client.post("/v1/events", json=payload)
    assert receipt.status_code == 200
    assert all(receipt.json()[field] == value for field, value in payload.items())
    assert len(receipt.json()["markers"]) == 1
    assert client.patch("/v1/settings", json={"meeting_max_minutes": 10}).status_code == 200
    # An older client may omit policy on retries; acknowledge the original persisted values.
    minimal = {
        field: payload[field] for field in ("event_id", "device_id", "occurred_at", "action")
    }
    retry = client.post("/v1/events", json=minimal)
    assert retry.status_code == 200
    assert all(retry.json()[field] == value for field, value in payload.items())


@pytest.mark.parametrize("audio_days,transcript_days", [(1, 0), (0, 1)])
def test_retry_after_lost_acknowledgement_preserves_expired_recording_receipt(
    client, audio_days, transcript_days
):
    key = uuid4()
    original = put_audio(client, key).json()
    store = client.app.state.memory.store
    store.apply_retention(audio_days, transcript_days, AT + timedelta(days=3))
    response = put_audio(client, key)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["state"] == "expired"
    for field in ("id", "device_id", "started_at", "ended_at"):
        assert receipt[field] == original[field]
    # A retry acknowledges the durable retention decision and never queues STT again.
    assert store.claim() is None
    assert bool(receipt["audio_available"]) is (not audio_days)
    assert (store.audio_dir / f"{key}.wav").exists() is (not audio_days)


def test_event_receipt_only_is_bounded_and_skips_history_projection(client, monkeypatch):
    payload = {
        "event_id": str(uuid4()), "device_id": "pendant", "occurred_at": stamp(AT),
        "action": "meeting_toggle", "meeting_max_minutes": 80, "meeting_min_seconds": 90,
        "marker_cooldown_seconds": 120,
    }
    normal = client.post("/v1/events", json=payload).json()
    assert {field: normal[field] for field in payload} == payload
    assert "markers" in normal and "meetings" in normal

    def unexpected_history_projection():
        raise AssertionError("A compact receipt must not replay the historical timeline")

    monkeypatch.setattr(client.app.state.memory, "timeline", unexpected_history_projection)
    receipt = client.post("/v1/events?receipt_only=true", json=payload)
    assert receipt.status_code == 200
    assert receipt.json() == payload
