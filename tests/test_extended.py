import io
import sqlite3
import wave
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.inference import Inference, InferenceError, validate_citations
from omi_local.retrieval import date_scope
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


def audio_data(amplitude=1500):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(amplitude.to_bytes(2, "little", signed=True) * 16000)
    return output.getvalue()


class FakeInference:
    settings = None
    calls = 0

    async def summarize(self, sources, purpose):
        self.calls += 1
        return f"Summary {self.calls}: {sources[0]['text']} [{sources[0]['id']}]"

    async def chat(self, question, sources, history=None):
        return f"{sources[0]['text']} [{sources[0]['id']}]"


@pytest.fixture
def config(tmp_path):
    return Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as session:
        session.app.state.memory.inference = FakeInference()
        yield session


def insert(client, at=AT, text="Kitchen delivery Friday", *, source_kind=""):
    key = uuid4()
    memory = client.app.state.memory
    memory.store.ingest(key, "omi", at, audio_data(), 1, source_kind=source_kind)
    memory.store.complete(str(key), text)
    return str(key)


def test_settings_are_validated_persisted_and_redacted(client, config):
    changed = client.patch(
        "/v1/settings",
        json={
            "capture_paused": True,
            "summary_language": "da",
            "recap_hour": 21,
            "marker_cooldown_seconds": 90,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["capture_paused"] is True
    assert "inference_token" not in changed.json()
    assert client.get("/v1/status").json()["capture_paused"] is True
    with TestClient(create_app(config)) as restarted:
        assert restarted.get("/v1/settings").json()["summary_language"] == "da"
        assert restarted.get("/v1/settings").json()["marker_cooldown_seconds"] == 90
    for patch in (
        {"api_token": "value"},
        {"timezone": "NoSuch/Zone"},
        {"meeting_min_seconds": 1},
        {"recap_hour": 24},
        {"summary_language": None},
    ):
        assert client.patch("/v1/settings", json=patch).status_code == 422
    assert client.get("/v1/settings").json()["recap_hour"] == 21


def test_default_retention_expires_audio_before_transcripts(client):
    settings = client.get("/v1/settings").json()
    assert settings["audio_retention_days"] == 7
    assert settings["transcript_retention_days"] == 30
    recent = insert(client, AT - timedelta(days=6), "Recent evidence")
    transcript_only = insert(client, AT - timedelta(days=8), "Older evidence")
    expired = insert(client, AT - timedelta(days=31), "Expired evidence")
    store = client.app.state.memory.store

    store.apply_retention(
        settings["audio_retention_days"], settings["transcript_retention_days"], now=AT
    )

    assert client.get(f"/v1/audio/{recent}").status_code == 200
    assert client.get(f"/v1/audio/{transcript_only}").status_code == 404
    assert store.search("Older", 10)[0]["id"] == transcript_only
    assert client.get(f"/v1/audio/{expired}").status_code == 404
    assert store.get_audio(expired)["text"] is None
    assert store.search("Expired", 10) == []


@pytest.mark.parametrize("audio_days,transcript_days", [(13, 47), (0, 0)])
def test_retention_choices_survive_restart_and_reject_invalid_days(
    client, config, audio_days, transcript_days
):
    choices = {"audio_retention_days": audio_days, "transcript_retention_days": transcript_days}
    saved = client.patch("/v1/settings", json=choices)
    assert saved.status_code == 200
    for field, days in choices.items():
        assert saved.json()[field] == days
        for invalid in (-1, 36501):
            assert client.patch("/v1/settings", json={field: invalid}).status_code == 422

    with TestClient(create_app(config)) as restarted:
        settings = restarted.get("/v1/settings").json()
        for field, days in choices.items():
            assert settings[field] == days


def test_transcript_pagination_search_calendar_and_auditable_correction(client):
    first = insert(client, text="A literal 100% kitchen promise")
    insert(client, AT + timedelta(days=1), "Next day")
    result = client.get("/v1/transcripts", params={"day": "2026-10-15", "q": "100%"})
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["id"] == first
    assert client.get("/v1/transcripts?limit=1&offset=1").json()["items"][0]["id"] == first
    revised = client.patch(f"/v1/transcripts/{first}", json={"text": "Delivery is Monday"})
    assert revised.status_code == 200
    assert revised.json()["text"] == "Delivery is Monday"
    assert client.app.state.memory.store.search("kitchen", 10) == []
    assert client.app.state.memory.store.search("Monday", 10)[0]["id"] == first
    revision = client.get(f"/v1/transcripts/{first}/revisions").json()["items"][0]
    assert revision["previous_text"] == "A literal 100% kitchen promise"
    assert revision["text"] == "Delivery is Monday"
    assert client.patch(f"/v1/transcripts/{uuid4()}", json={"text": "x"}).status_code == 404


def test_cached_diary_reads_generate_only_on_post_and_keep_versions(client):
    key = insert(client, source_kind="manual_upload")
    ai = client.app.state.memory.inference
    before = client.get("/v1/recaps/2026-10-15").json()
    assert before["summary"] is None and before["generated_at"] is None
    assert ai.calls == 0
    assert client.get("/v1/diary/2026-10-15.md").status_code == 404
    assert client.post("/v1/recaps/2026-10-15").status_code == 200
    assert ai.calls == 2
    assert client.get("/v1/recaps/2026-10-15").json()["summary"].startswith("Summary 2")
    assert len(client.get("/v1/diary").json()["items"]) == 1
    assert key in client.get("/v1/diary/2026-10-15.md").text
    insert(client, AT + timedelta(hours=1), "Late evidence", source_kind="manual_upload")
    assert client.get("/v1/recaps/2026-10-15").json()["stale"] is True
    assert ai.calls == 2
    assert client.post("/v1/recaps/2026-10-15").status_code == 200
    assert ai.calls == 4  # Reuse the first conversation; prepare the late upload and synthesize.
    assert len(client.get("/v1/diary/2026-10-15/versions").json()["items"]) == 2
    client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected delivery date"})
    assert client.get("/v1/diary").json()["items"] == []
    assert client.get("/v1/diary/2026-10-15.md").status_code == 404


def test_date_scoped_chat_persists_evidence_and_conversation(client, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT)
    today_id = insert(client, text="Kitchen today")
    yesterday_id = insert(client, AT - timedelta(days=1), "Kitchen yesterday")
    answer = client.post("/v1/chat", json={"question": "What about kitchen yesterday?"}).json()
    assert [source["id"] for source in answer["sources"]] == [yesterday_id]
    assert today_id not in answer["answer"]
    session_id = answer["session_id"]
    followup = client.post("/v1/chat", json={"question": "kitchen", "session_id": session_id})
    assert followup.status_code == 200
    assert followup.json()["sources"][0]["id"] == yesterday_id
    sessions = client.get("/v1/chat/sessions").json()["items"]
    assert sessions[0]["id"] == session_id
    detail = client.get(f"/v1/chat/sessions/{session_id}").json()
    assert [row["role"] for row in detail["messages"]] == ["user", "assistant"] * 2
    assert detail["messages"][1]["sources"][0]["id"] == yesterday_id
    explicit = client.post("/v1/chat", json={"question": "kitchen", "day": "2026-10-15"}).json()
    assert explicit["sources"][0]["id"] == today_id
    assert (
        client.post("/v1/chat", json={"question": "x", "session_id": str(uuid4())}).status_code
        == 404
    )


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Hvad skete der i går?", (date(2026, 10, 14), date(2026, 10, 14))),
        ("What happened last Tuesday?", (date(2026, 10, 13), date(2026, 10, 13))),
        ("Sidste torsdag", (date(2026, 10, 8), date(2026, 10, 8))),
        ("2026-10-01 to 2026-10-03", (date(2026, 10, 1), date(2026, 10, 3))),
        ("sidste uge", (date(2026, 10, 5), date(2026, 10, 11))),
    ],
)
def test_calendar_scoping_handles_english_danish_and_explicit_dates(question, expected):
    assert date_scope(question, AT.date()) == expected


def test_broad_danish_day_query_retrieves_recorded_evidence(client, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT)
    key = insert(client, AT - timedelta(days=1))
    response = client.post("/v1/chat", json={"question": "Hvad skete der i går?"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["id"] == key


def test_unknown_model_citations_are_rejected_without_persisting_answer(client):
    insert(client)
    unknown = str(uuid4())

    class InvalidInference(FakeInference):
        async def chat(self, question, sources, history=None):
            return f"Unsupported claim [{unknown}]"

    client.app.state.memory.inference = InvalidInference()
    response = client.post("/v1/chat", json={"question": "kitchen"})
    assert response.status_code == 502
    assert unknown not in response.text
    session = client.get("/v1/chat/sessions").json()["items"][0]
    assert client.get(f"/v1/chat/sessions/{session['id']}").json()["messages"] == []
    with pytest.raises(InferenceError):
        validate_citations(f"[{unknown}]", [])


def test_capture_gaps_survive_retries_and_are_visible_in_rewind(client):
    gap = {
        "gap_id": str(uuid4()),
        "device_id": "omi",
        "started_at": stamp(AT),
        "ended_at": stamp(AT + timedelta(seconds=10)),
        "reason": "BLE sequence discontinuity",
    }
    assert client.post("/v1/gaps", json=gap).status_code == 200
    assert client.post("/v1/gaps", json=gap).status_code == 200
    assert len(client.get("/v1/timeline").json()["capture_gaps"]) == 1
    response = client.post(
        "/v1/rewind", json={"end": stamp(AT + timedelta(minutes=1)), "minutes": 1}
    )
    assert response.json()["capture_gaps"][0]["gap_id"] == gap["gap_id"]
    gap["reason"] = "Different reason"
    assert client.post("/v1/gaps", json=gap).status_code == 409
    gap["gap_id"] = str(uuid4())
    gap["ended_at"] = gap["started_at"]
    assert client.post("/v1/gaps", json=gap).status_code == 422


def test_audio_retention_keeps_transcripts_but_disables_audio_playback(client):
    key = insert(client)
    store = client.app.state.memory.store
    result = store.apply_retention(1, 0, AT + timedelta(days=3))
    assert result == {"audio_files_removed": 1, "transcripts_removed": 0}
    assert client.get(f"/v1/audio/{key}").status_code == 404
    assert store.search("kitchen", 10)[0]["text"] == "Kitchen delivery Friday"
    assert client.get("/v1/transcripts").json()["items"][0]["audio_available"] is False
    assert (
        client.get(
            "/v1/rewind/audio", params={"end": stamp(AT + timedelta(minutes=1)), "minutes": 1}
        ).status_code
        == 404
    )


def test_transcript_retention_purges_revisions_search_and_derived_memory(client):
    key = insert(client)
    client.patch(f"/v1/transcripts/{key}", json={"text": "Kitchen commitment Monday"})
    client.post("/v1/recaps/2026-10-15")
    client.post("/v1/chat", json={"question": "kitchen"})
    store = client.app.state.memory.store
    result = store.apply_retention(0, 1, AT + timedelta(days=3))
    assert result["transcripts_removed"] == 1
    assert store.search("kitchen", 10) == []
    assert store.transcript_revisions(key) == []
    assert store.diaries() == [] and store.diary_versions("2026-10-15") == []
    assert store.chat_sessions() == []
    assert client.get(f"/v1/audio/{key}").status_code == 200


def test_delete_propagates_and_inflight_stt_cannot_restore_deleted_or_corrected_text(client):
    key = insert(client)
    client.post("/v1/recaps/2026-10-15")
    store = client.app.state.memory.store
    assert client.delete(f"/v1/audio/{key}").status_code == 200
    store.complete(key, "Should never return", claimed=True)
    assert store.search("return", 10) == []
    assert client.get("/v1/diary").json()["items"] == []
    key = insert(client)
    store.correct_transcript(key, "Human correction")
    store.complete(key, "Old STT result", claimed=True)
    assert store.get_audio(key)["text"] == "Human correction"


def test_spa_assets_and_deep_links_do_not_capture_missing_api_routes(config, tmp_path):
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("<html>Omi application</html>")
    (root / "app.js").write_text("window.omi = true")
    config = config.model_copy(update={"web_dir": root})
    with TestClient(create_app(config)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/diary/2026-10-15").text == "<html>Omi application</html>"
        for path in ("/today", "/today/2026-10-15", "/settings/connections", "/labels/person-id"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.text == "<html>Omi application</html>"
            assert response.headers["cache-control"] == "no-cache"
        assert client.get("/app.js").text == "window.omi = true"
        assert client.get("/v1/unknown").status_code == 404
        assert client.get("/missing.js").status_code == 404
        assert client.get("/%2e%2e/omi.sqlite3").status_code == 404


def test_archive_snapshot_is_portable_when_restored_under_a_new_directory(client, tmp_path):
    key = insert(client)
    store = client.app.state.memory.store
    archive_path = store.export_archive()
    target = tmp_path / "restored"
    target.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(target)
        restored = Store(target)
        row = restored.get_audio(key)
        assert Path(row["path"]).parent == target / "audio"
        assert Path(row["path"]).read_bytes() == audio_data()
        assert restored.search("kitchen", 10)[0]["id"] == key
        with sqlite3.connect(target / "omi.sqlite3") as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        archive_path.unlink()
        archive_path.parent.rmdir()


@pytest.mark.anyio
async def test_silence_gate_preserves_recording_without_calling_stt(config):
    store = Store(config.data_dir)
    key = uuid4()
    store.ingest(key, "omi", AT, audio_data(0), 1)
    memory = type("Memory", (), {"store": store, "settings": config, "inference": object()})()
    assert await process_one(memory)
    assert store.get_audio(str(key))["state"] == "done"
    assert store.get_audio(str(key))["text"] == ""
    assert Path(store.get_audio(str(key))["path"]).exists()


@pytest.mark.anyio
async def test_stt_auto_language_segments_and_timeout_contract(config):
    config = config.model_copy(
        update={"stt_segment_timestamps": True, "inference_timeout_seconds": 17,
                "stt_filter_speech": False}
    )

    def handler(request):
        assert b'name="summary_language"' not in request.content
        assert b"verbose_json" in request.content
        assert request.extensions["timeout"]["read"] == 17
        return httpx.Response(
            200, json={"text": "Hej", "segments": [{"start": 0.1, "end": 0.9, "text": "Hej"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(config, http)
        result = await inference.transcribe_result(audio_data())
        assert result["text"] == "Hej"
        assert result["segments"] == [{"start": 0.1, "end": 0.9, "text": "Hej"}]
        assert inference.health["stt"]["state"] == "ready"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_inflight_summary_cannot_restore_deleted_recordings(client):
    key = insert(client, source_kind="manual_upload")
    store = client.app.state.memory.store

    class DeletingInference(FakeInference):
        async def summarize(self, sources, purpose):
            store.delete_audio(key)
            return f"Old private evidence [{key}]"

    client.app.state.memory.inference = DeletingInference()
    assert client.post("/v1/recaps/2026-10-15").status_code == 409
    assert store.diaries() == [] and store.diary_versions("2026-10-15") == []
    assert list(store.diary_dir.glob("*.md")) == []


def test_inflight_chat_cannot_save_outdated_corrected_evidence(client):
    key = insert(client)
    store = client.app.state.memory.store

    class CorrectingInference(FakeInference):
        async def chat(self, question, sources, history=None):
            store.correct_transcript(key, "Corrected evidence")
            return f"Outdated claim [{key}]"

    client.app.state.memory.inference = CorrectingInference()
    assert client.post("/v1/chat", json={"question": "kitchen"}).status_code == 409
    assert store.chat_session(store.chat_sessions()[0]["id"])["messages"] == []


def test_retention_prevents_expired_queued_audio_from_transcribing(client):
    store = client.app.state.memory.store
    key = uuid4()
    store.ingest(key, "omi", AT, audio_data(), 1)
    result = store.apply_retention(0, 1, AT + timedelta(days=3))
    assert result["transcripts_removed"] == 1
    assert store.claim() is None
    assert store.get_audio(str(key))["state"] == "expired"


def test_archive_backup_ignores_orphans_and_rejects_corrupt_referenced_audio(client):
    key = insert(client)
    store = client.app.state.memory.store
    orphan = store.audio_dir / "unreferenced.wav"
    orphan.write_bytes(audio_data())
    archive_path = store.export_archive()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert "audio/unreferenced.wav" not in archive.namelist()
    finally:
        archive_path.unlink()
        archive_path.parent.rmdir()
    (store.audio_dir / f"{key}.wav").write_bytes(b"corrupt recording")
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.export_archive()


def test_chat_followup_reloads_current_original_evidence(client):
    key = insert(client)
    first = client.post("/v1/chat", json={"question": "kitchen"}).json()
    client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected Friday delivery"})
    second = client.post(
        "/v1/chat", json={"question": "What did they decide?", "session_id": first["session_id"]}
    )
    assert second.status_code == 200
    assert "Corrected Friday delivery" in second.json()["answer"]
    assert client.post("/v1/chat", json={"question": "kitchen on 2026-13-01"}).status_code == 422


def test_native_offline_policy_is_acknowledged_without_overriding_fixed_behavior(client):
    assert (
        client.patch(
            "/v1/settings",
            json={
                "meeting_max_minutes": 1,
                "meeting_min_seconds": 60,
                "marker_cooldown_seconds": 10,
            },
        ).status_code
        == 200
    )
    captured = {"meeting_max_minutes": 5, "meeting_min_seconds": 90, "marker_cooldown_seconds": 120}
    events = []
    for seconds in (0, 60, 120):
        payload = {
            "event_id": str(uuid4()),
            "device_id": "omi",
            "occurred_at": stamp(AT + timedelta(seconds=seconds)),
            "action": "mark",
            **captured,
        }
        events.append(payload)
        assert client.post("/v1/events", json=payload).status_code == 200
    start = {
        "event_id": str(uuid4()),
        "device_id": "omi",
        "occurred_at": stamp(AT + timedelta(minutes=10)),
        "action": "meeting_toggle",
        **captured,
    }
    stop = start | {"event_id": str(uuid4()), "occurred_at": stamp(AT + timedelta(minutes=12))}
    assert client.post("/v1/events", json=start).status_code == 200
    state = client.post("/v1/events", json=stop).json()
    assert [row["occurred_at"] for row in state["markers"]] == [
        stamp(AT),
        stamp(AT + timedelta(seconds=60)),
        stamp(AT + timedelta(seconds=120)),
    ]
    assert len(state["meetings"]) == 1
    meeting = state["meetings"][0]
    assert meeting["end_reason"] == "button"
    assert meeting["auto_stop_at"] is None
    assert meeting["qualifies_at"] == stamp(AT + timedelta(minutes=11))
    assert client.post("/v1/events", json=events[0]).status_code == 200
    assert (
        client.post("/v1/events", json=events[0] | {"marker_cooldown_seconds": 121}).status_code
        == 409
    )
    legacy_retry = {key: value for key, value in events[0].items() if key not in captured}
    assert client.post("/v1/events", json=legacy_retry).status_code == 200


@pytest.mark.parametrize(
    "override",
    [
        {"meeting_max_minutes": 0},
        {"meeting_max_minutes": 481},
        {"meeting_min_seconds": 59},
        {"meeting_min_seconds": 3601},
        {"marker_cooldown_seconds": -1},
        {"marker_cooldown_seconds": 3601},
    ],
)
def test_captured_button_policy_is_validated_at_api_boundary(client, override):
    payload = {
        "event_id": str(uuid4()),
        "device_id": "omi",
        "occurred_at": stamp(AT),
        "action": "mark",
        **override,
    }
    assert client.post("/v1/events", json=payload).status_code == 422
