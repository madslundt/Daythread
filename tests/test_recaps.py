"""Daily recaps use completed conversations, independently of audio retention."""

import io
import sys
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.settings import Settings
from omi_local.voice_activity import VERSION
from omi_local.worker import scheduled_recaps

AT = datetime(2026, 10, 16, 12, tzinfo=UTC)


def wav(identity=b""):
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(identity + b"\0" * (32000 - len(identity)))
    return out.getvalue()


class FakeInference:
    settings = None

    def __init__(self):
        self.calls = []

    async def summarize(self, sources, purpose):
        self.calls.append((sources, purpose))
        return "\n".join(f"{s['text']} [{s['id']}]" for s in sources)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT + timedelta(hours=1))
    config = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    with TestClient(create_app(config)) as client:
        client.app.state.memory.inference = FakeInference()
        yield client


def insert(client, at=AT, text="Share the notes tomorrow", kind="", duration=60, activity=None):
    store = client.app.state.memory.store
    recording = uuid4()
    key = str(recording)
    # Separate spoken recordings need distinct audio; observed quiet remains digital zero.
    store.ingest(key, "omi", at, wav(recording.bytes if text else b""), duration, source_kind=kind)
    store.complete(key, text, details={"voice_activity": activity} if activity else None)
    return key


def test_recap_excludes_the_entire_open_conversation_but_keeps_completed_ones(client):
    completed = insert(client, AT - timedelta(hours=1), kind="manual_upload")
    first = insert(client)
    second = insert(client, AT + timedelta(minutes=1), "Yes, Friday at three")
    recap = client.post("/v1/recaps/2026-10-16").json()
    assert recap["summary"]
    assert {s["id"] for s in recap["sources"]} == {completed}
    assert recap["deferred_conversations"] == 1
    assert recap["deferred_sources"] == 2
    assert first not in recap["summary"] and second not in recap["summary"]
    assert recap["included_through"] == stamp(AT - timedelta(minutes=59))


def quiet(client, at, duration=120, kind=""):
    return insert(client, at, "", kind=kind, duration=duration, activity={
        "version": VERSION, "status": "observed", "speech": [],
        "non_speech": [[0, duration]], "method": "test_observation",
    })


def close_after(client, at):
    for offset in (0, 120, 240):
        quiet(client, at + timedelta(seconds=offset))


def test_observed_non_speech_closes_conversation_and_short_answers_keep_context(client):
    first = insert(client, text="When should we send it?")
    second = insert(client, AT + timedelta(minutes=1), "Friday at three")
    close_after(client, AT + timedelta(minutes=2))
    active = insert(client, AT + timedelta(minutes=59), "Current conversation")
    result = client.post("/v1/recaps/2026-10-16").json()
    assert {r["id"] for r in result["sources"]} == {first, second}
    assert "Friday at three" in result["summary"]
    assert active not in result["summary"]
    assert result["deferred_conversations"] == 1


def test_empty_transcript_unknown_audio_and_missing_uploads_do_not_prove_silence(client):
    insert(client)
    # Its unknown detector result must override even digitally silent test WAV bytes.
    insert(client, AT + timedelta(minutes=1), "", duration=120, activity={
        "version": VERSION, "status": "unknown",
    })
    quiet(client, AT + timedelta(minutes=3), 120)
    # A gap prevents two short stretches from becoming a five-minute quiet run.
    quiet(client, AT + timedelta(minutes=7), 120)
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["summary"] is None
    assert result["deferred_conversations"] == 1
    assert client.app.state.memory.inference.calls == []


def test_pending_or_overlapping_speech_blocks_an_apparent_quiet_boundary(client):
    insert(client)
    close_after(client, AT + timedelta(minutes=1))
    key = str(uuid4())
    client.app.state.memory.store.ingest(
        key, "omi", AT + timedelta(minutes=3), wav(), 120,
    )
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["summary"] is None
    assert result["pending_chunks"] == 1


def test_midnight_does_not_cut_off_an_open_conversation(client, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT + timedelta(hours=14))
    first = insert(client, AT.replace(hour=23, minute=58))
    second = insert(client, AT + timedelta(hours=12), "Still discussing the plan")
    for day in ("2026-10-16", "2026-10-17"):
        result = client.post(f"/v1/recaps/{day}").json()
        assert result["summary"] is None
        assert result["deferred_sources"] == 2
    assert client.app.state.memory.store.get_audio(first)
    assert client.app.state.memory.store.get_audio(second)


def test_conversation_notes_are_reused_and_silence_does_not_regenerate_recap(client):
    insert(client, kind="manual_upload")
    memory = client.app.state.memory
    first = client.post("/v1/recaps/2026-10-16").json()
    assert len(memory.inference.calls) == 2  # One preparation, one daily synthesis.
    quiet(client, AT + timedelta(minutes=5))
    cached = client.get("/v1/recaps/2026-10-16").json()
    assert cached["stale"] is False
    again = client.post("/v1/recaps/2026-10-16").json()
    assert again["output_id"] == first["output_id"]
    assert len(memory.inference.calls) == 2
    insert(client, AT + timedelta(minutes=10), "Book the service", kind="manual_upload")
    client.post("/v1/recaps/2026-10-16")
    assert len(memory.inference.calls) == 4  # Only the new conversation plus synthesis.


def test_low_value_fragments_are_cached_without_inventing_a_daily_event(client):
    from omi_local.recaps import NO_CONTENT

    class Fragments(FakeInference):
        async def summarize(self, sources, purpose):
            self.calls.append((sources, purpose))
            return NO_CONTENT

    memory = client.app.state.memory
    memory.inference = Fragments()
    key = insert(client, text="Hmm, uh", kind="manual_upload")
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["summary"] is None and result["no_recap_content"] is True
    assert client.get(f"/v1/audio/{key}").status_code == 200
    client.post("/v1/recaps/2026-10-16")
    assert len(memory.inference.calls) == 1
    assert client.get("/v1/recaps/2026-10-16").json()["no_recap_content"] is True


def test_corrections_and_deletions_purge_prepared_text(client):
    key = insert(client, kind="manual_upload")
    client.post("/v1/recaps/2026-10-16")
    memory = client.app.state.memory
    client.patch(f"/v1/transcripts/{key}", json={"text": "Send the revised plan"})
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM recap_summaries").fetchone()[0] == 0
    revised = client.post("/v1/recaps/2026-10-16").json()
    assert "revised plan" in revised["summary"]
    client.delete(f"/v1/audio/{key}")
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM recap_summaries").fetchone()[0] == 0


def test_late_speech_during_preparation_cannot_publish_a_cut_off_conversation(client):
    insert(client)
    close_after(client, AT + timedelta(minutes=1))
    memory = client.app.state.memory

    class LateSpeech(FakeInference):
        async def summarize(self, sources, purpose):
            insert(client, AT + timedelta(minutes=3), "Actually, let us reconsider", duration=120)
            return await super().summarize(sources, purpose)

    memory.inference = LateSpeech()
    assert client.post("/v1/recaps/2026-10-16").status_code == 409
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM recap_summaries").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM diary_versions").fetchone()[0] == 0


def test_late_speech_reopens_conversation_and_suppresses_its_saved_recap(client):
    first = insert(client)
    close_after(client, AT + timedelta(minutes=1))
    assert client.post("/v1/recaps/2026-10-16").json()["summary"]
    insert(client, AT + timedelta(minutes=3), "Reconsider the plan", duration=120)
    current = client.get("/v1/recaps/2026-10-16").json()
    assert current["summary"] is None and current["deferred_conversations"] == 1
    assert client.get("/v1/diary").json()["items"][0]["summary"] is None
    assert first not in client.get("/v1/diary/2026-10-16.md").text
    assert client.post("/v1/recaps/2026-10-16").json()["summary"] is None
    assert client.get("/v1/recaps/2026-10-16").json()["summary"] is None


def test_legacy_amplitude_gate_is_not_treated_as_evidence_of_no_speech(client):
    first = insert(client)
    store = client.app.state.memory.store
    for offset in (60, 180, 300):
        key = str(uuid4())
        store.ingest(key, "omi", AT + timedelta(seconds=offset), wav(), 120)
        store.complete(key, "", outcome="silence_gate")
    # Backfill can observe the actual digital silence in these original files.
    result = client.post("/v1/recaps/2026-10-16").json()
    # The test WAVs are only one second long: duration metadata cannot invent
    # five minutes of observed silence from those short recordings.
    assert result["summary"] is None
    assert result["deferred_sources"] >= 1
    assert store.get_audio(first)["text"]


def test_new_transcripts_update_recaps_without_waiting_for_interval(client, monkeypatch):
    import asyncio

    now = AT + timedelta(hours=1)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: now)
    monkeypatch.setattr("omi_local.worker.utc_now", lambda: now)
    monkeypatch.setattr("omi_local.store.utc_now", lambda: now)
    memory = client.app.state.memory
    insert(client, kind="manual_upload")
    asyncio.run(scheduled_recaps(memory))
    first = client.get("/v1/recaps/2026-10-16").json()
    assert first["summary"]
    now += timedelta(minutes=10)
    second = insert(client, AT + timedelta(minutes=5), "Call the shop", kind="manual_upload")
    asyncio.run(scheduled_recaps(memory))
    assert len(memory.inference.calls) == 4  # New conversation and immediate daily synthesis.
    assert client.get("/v1/recaps/2026-10-16").json()["output_id"] != first["output_id"]
    now += timedelta(minutes=50)
    asyncio.run(scheduled_recaps(memory))
    assert len(memory.inference.calls) == 4
    assert second in client.get("/v1/recaps/2026-10-16").json()["summary"]


def test_legacy_schedule_does_not_delay_automatic_recaps(client, monkeypatch):
    import asyncio

    now = AT + timedelta(hours=1)
    for module in ("service", "worker", "store"):
        monkeypatch.setattr(f"omi_local.{module}.utc_now", lambda: now)
    client.patch("/v1/settings", json={"recap_interval_minutes": 0, "recap_hour": 22})
    memory = client.app.state.memory
    insert(client, kind="manual_upload")
    asyncio.run(scheduled_recaps(memory))
    assert len(memory.inference.calls) == 2
    assert client.get("/v1/recaps/2026-10-16").json()["summary"]
    now = AT.replace(hour=22)
    asyncio.run(scheduled_recaps(memory))
    assert client.get("/v1/recaps/2026-10-16").json()["summary"]


def test_voice_detector_observes_silence_and_failure_stays_unknown(monkeypatch):
    from omi_local.voice_activity import analyze_voice

    result = analyze_voice(wav())
    assert result["status"] == "observed"
    assert result["non_speech"] == [[0, 1]] and result["speech"] == []
    data = io.BytesIO()
    with wave.open(data, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\0" * 16000)
    # Exercise the installed C extension on this project's Python version.
    assert analyze_voice(data.getvalue())["status"] == "observed"
    monkeypatch.setitem(sys.modules, "webrtcvad", None)
    assert analyze_voice(data.getvalue())["status"] == "unknown"


def test_webhook_receipt_estimates_cannot_prove_a_conversation_ended(client):
    current = insert(client, kind="omi_audio_webhook")
    for offset in (60, 180, 300):
        quiet(client, AT + timedelta(seconds=offset), kind="omi_audio_webhook")
    finished = insert(client, AT - timedelta(hours=1), kind="omi_api")
    result = client.post("/v1/recaps/2026-10-16").json()
    assert {r["id"] for r in result["sources"]} == {finished}
    assert current not in result["summary"]
    assert result["deferred_conversations"] == 1
    assert "import finished Omi" in result["boundary_note"]


def test_blocked_count_describes_excluded_audio_and_retention_preserves_originals(client):
    completed = insert(client, kind="manual_upload")
    store = client.app.state.memory.store
    key = str(uuid4())
    store.ingest(key, "omi", AT + timedelta(minutes=10), wav(), 1)
    claimed = store.claim()
    store.retry(claimed, "Missing audio", blocked=True)
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["evidence"]["blocked_chunks"] == 1
    assert client.get(f"/v1/audio/{completed}").status_code == 200
    store.apply_retention(0, 1, now=AT + timedelta(days=2))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM recap_summaries").fetchone()[0] == 0


def test_previous_day_legacy_web_upload_can_generate_and_reload_recap(client):
    # The original web uploader sent device_id=web without source_kind.
    previous = AT - timedelta(days=1)
    key = str(uuid4())
    receipt = client.put(
        f"/v1/audio/{key}",
        params={"device_id": "web", "started_at": stamp(previous)},
        content=wav(), headers={"Content-Type": "audio/wav"},
    )
    assert receipt.status_code == 200
    pending = client.post("/v1/recaps/2026-10-15").json()
    assert pending["summary"] is None and pending["deferred_sources"] == 1
    client.app.state.memory.store.complete(key, "Send the project notes on Friday")
    current = insert(client, text="Today's ongoing conversation")

    result = client.post("/v1/recaps/2026-10-15").json()
    assert result["summary"] and key in result["summary"]
    assert {source["id"] for source in result["sources"]} == {key}
    assert result["deferred_conversations"] == 0
    assert current not in result["summary"]
    saved = client.get("/v1/recaps/2026-10-15").json()
    assert saved["summary"] == result["summary"] and not saved["stale"]
    assert client.get("/v1/recaps/2026-10-16").json()["deferred_conversations"] == 1
    # Classification must not rewrite the original upload/retry metadata.
    source = client.app.state.memory.store.get_audio(key)
    assert source["device_id"] == "web" and source["source_kind"] == ""


def test_late_historical_transcript_updates_only_its_recap(client, monkeypatch):
    import asyncio

    monkeypatch.setattr("omi_local.worker.utc_now", lambda: AT + timedelta(hours=1))
    memory = client.app.state.memory
    old = insert(client, AT - timedelta(days=3), "Send the notes", kind="manual_upload")
    current = insert(client, kind="manual_upload")
    asyncio.run(scheduled_recaps(memory))
    original = client.get("/v1/recaps/2026-10-13").json()
    today = client.get("/v1/recaps/2026-10-16").json()
    late = insert(
        client, AT - timedelta(days=3, minutes=-5), "Book the service", kind="manual_upload",
    )
    asyncio.run(scheduled_recaps(memory))
    updated = client.get("/v1/recaps/2026-10-13").json()
    assert updated["output_id"] != original["output_id"]
    assert {s["id"] for s in updated["sources"]} == {old, late}
    assert current not in updated["summary"]
    assert client.get("/v1/recaps/2026-10-16").json()["output_id"] == today["output_id"]
    calls = len(memory.inference.calls)
    asyncio.run(scheduled_recaps(memory))
    assert len(memory.inference.calls) == calls
    assert client.get("/v1/recaps/2026-10-13").json()["output_id"] == updated["output_id"]
    assert "State each fact or decision only once" in memory.inference.calls[-1][1]


def test_daily_format_upgrade_refreshes_saved_recap_without_reprocessing_notes(client, monkeypatch):
    import asyncio

    import omi_local.recaps as recaps

    monkeypatch.setattr("omi_local.worker.utc_now", lambda: AT + timedelta(hours=1))
    insert(client, text="Milk frother. Omi recording. Roadster discussion.", kind="manual_upload")
    memory = client.app.state.memory
    asyncio.run(scheduled_recaps(memory))
    previous = client.get("/v1/recaps/2026-10-16").json()
    assert len(memory.inference.calls) == 2

    monkeypatch.setattr(recaps, "DAY_PROMPT_VERSION", "next-topic-format")
    assert client.get("/v1/recaps/2026-10-16").json()["stale"]
    asyncio.run(scheduled_recaps(memory))
    updated = client.get("/v1/recaps/2026-10-16").json()
    assert updated["output_id"] != previous["output_id"]
    assert not updated["stale"]
    assert len(memory.inference.calls) == 3  # Reuse notes; rewrite only the daily recap.
    purpose = memory.inference.calls[-1][1]
    assert "fewest meaningful topic sections" in purpose
    assert "one coherent summary per topic" in purpose
    assert "Do not create separate short and long summaries" in purpose
    assert "do not mix in unrelated subjects" in purpose
    assert "without a recap title, date heading" in purpose
    assert "do not create separate decisions/commitments or open-questions sections" in purpose
    assert "Rewrite, rename, merge or reorganize" in purpose
    asyncio.run(scheduled_recaps(memory))
    assert len(memory.inference.calls) == 3
