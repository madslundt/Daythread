"""Alternative recordings retain evidence while contributing one conversation."""

import asyncio
import io
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
SHARED = (
    "We reviewed the launch proposal and agreed to send the revised budget to Maria "
    "before Friday afternoon so that the team can confirm the final delivery date."
)


def wav(level=1000):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(level.to_bytes(2, "little", signed=True) * 16000)
    return output.getvalue()


class Inference:
    def __init__(self):
        self.transcriptions = 0
        self.summaries = []

    async def transcribe(self, data):
        self.transcriptions += 1
        return SHARED

    async def summarize(self, sources, purpose):
        self.summaries.append((sources, purpose))
        return "\n".join(f"{row['text']} [{row['id']}]" for row in sources)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT + timedelta(hours=2))
    settings = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        client.app.state.memory.inference = Inference()
        yield client


def upload(client, *, at=AT, key=None, level=1000, name="meeting.wav", **metadata):
    return client.put(f"/v1/audio/{key or uuid4()}", params={
        "device_id": "manual-web", "started_at": stamp(at),
        "source_kind": "manual_upload", "source_id": name, **metadata,
    }, content=wav(level), headers={"Content-Type": "audio/wav"})


def done(client, text=SHARED, *, at=AT, **metadata):
    response = upload(client, at=at, **metadata)
    assert response.status_code == 200, response.text
    key = response.json()["id"]
    client.app.state.memory.store.complete(key, text)
    return key


def groups(client):
    return client.app.state.memory.recaps.groups(AT + timedelta(hours=2))


def test_reupload_reuses_recognition_but_preserves_receipts_and_audio(client):
    first = upload(client).json()
    second = upload(client, name="renamed.wav").json()
    memory = client.app.state.memory
    asyncio.run(process_one(memory))
    asyncio.run(process_one(memory))
    assert memory.inference.transcriptions == 1
    assert len(groups(client)) == 1
    assert {r["id"] for r in groups(client)[0].sources} == {first["id"], second["id"]}
    assert client.get(f"/v1/audio/{second['id']}").content == wav()
    assert upload(client, key=second["id"], name="renamed.wav").json()["id"] == second["id"]


def test_reupload_with_changed_time_matches_legacy_web_upload(client):
    first = upload(client, device_id="web", source_kind="", source_id="").json()
    second = upload(client, at=AT + timedelta(minutes=7)).json()
    memory = client.app.state.memory
    asyncio.run(process_one(memory))
    asyncio.run(process_one(memory))
    assert memory.inference.transcriptions == 1
    assert len(groups(client)) == 1
    assert {row["id"] for row in groups(client)[0].sources} == {first["id"], second["id"]}
    assert memory.store.get_audio(second["id"])["started_at"] == second["started_at"]


def test_timeline_collapses_identical_uploads_before_pagination(client):
    first = done(client)
    second = done(client, at=AT + timedelta(minutes=7), name="renamed.wav")
    different = done(client, at=AT + timedelta(minutes=10), level=2000)
    params = {"day": "2026-10-15", "deduplicate": True, "limit": 1}
    page = client.get("/v1/transcripts", params=params).json()
    assert page["total"] == 2
    assert page["items"][0]["id"] == different
    page = client.get("/v1/transcripts", params=params | {"offset": 1}).json()
    assert page["items"][0]["id"] == first
    assert [row["id"] for row in page["items"][0]["alternate_sources"]] == [second]
    assert client.get("/v1/transcripts").json()["total"] == 3
    assert client.get(f"/v1/sources/{second}").status_code == 200


def test_existing_changed_time_uploads_are_matched_after_upgrade(client):
    first = done(client, device_id="web", source_kind="", source_id="")
    second = done(client, at=AT + timedelta(minutes=7))
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute("DELETE FROM source_matches")
        db.execute("UPDATE source_match_checked SET version="
                   "(SELECT source_version FROM audio WHERE audio.id=source_match_checked.id)")
    assert len(groups(client)) == 2
    Store(store.root)
    assert len(groups(client)) == 1
    assert {row["id"] for row in groups(client)[0].sources} == {first, second}


def test_timeline_matches_queued_legacy_upload_without_changing_receipts(client):
    first = upload(client, device_id="web", source_kind="", source_id="").json()
    second = upload(client, at=AT + timedelta(minutes=7)).json()
    page = client.get("/v1/transcripts?day=2026-10-15&deduplicate=true").json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == first["id"]
    alternative = page["items"][0]["alternate_sources"][0]
    assert alternative["started_at"] == second["started_at"]
    assert alternative["state"] == "queued"


def test_repeated_wearable_audio_at_different_times_stays_separate(client):
    for at in (AT, AT + timedelta(minutes=7)):
        done(client, at=at, device_id="omi", source_kind="", source_id="")
    page = client.get("/v1/transcripts?deduplicate=true").json()
    assert page["total"] == 2
    assert all(not row["alternate_sources"] for row in page["items"])


def test_deduplicated_timeline_respects_search_and_day_filters(client):
    done(client, "First reading of the meeting")
    second = done(client, "Corrected unique phrase", at=AT + timedelta(minutes=7))
    done(client, at=AT + timedelta(days=1))
    params = {"day": "2026-10-15", "deduplicate": True, "q": "unique phrase"}
    page = client.get("/v1/transcripts", params=params).json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == second
    assert client.get("/v1/transcripts", params=params | {"q": "missing"}).json()["total"] == 0


def test_repeated_audio_in_different_parts_is_not_hidden_as_a_reupload(client):
    recording = str(uuid4())
    for index in range(2):
        upload(client, at=AT + timedelta(seconds=index), recording_id=recording,
               part_index=index, part_count=2)
    page = client.get("/v1/transcripts?deduplicate=true").json()
    assert page["total"] == 2


def test_reuse_does_not_copy_a_manual_correction_or_cross_a_model_change(client):
    first = upload(client).json()["id"]
    memory = client.app.state.memory
    asyncio.run(process_one(memory))
    client.patch(f"/v1/transcripts/{first}", json={"text": "Reviewed correction"})
    upload(client)
    asyncio.run(process_one(memory))
    assert memory.inference.transcriptions == 2
    memory.settings.stt_model = "another-model"
    upload(client)
    asyncio.run(process_one(memory))
    assert memory.inference.transcriptions == 3


def test_identical_audio_with_short_speech_is_still_one_conversation(client):
    done(client, "Please send the budget tomorrow")
    done(client, "Please send the budget tomorrow", name="another-name.wav")
    assert len(groups(client)) == 1


def test_partial_alternative_transcripts_make_one_recap_with_all_details(client):
    first = done(client, "The room booking is confirmed. " + SHARED)
    second = done(client, SHARED.lower().replace("budget", "estimate") +
                  " Thomas will send the accessibility checklist.", level=2000)
    assert len(groups(client)) == 1
    result = client.post("/v1/recaps/2026-10-15").json()
    assert {row["id"] for row in result["sources"]} == {first, second}
    assert result["evidence"]["pipeline"]["conversation_count"] == 1
    preparation = client.app.state.memory.inference.summaries[0]
    assert any("room booking" in s["text"] for s in preparation[0])
    assert any("accessibility checklist" in s["text"] for s in preparation[0])
    assert "once" in preparation[1] and "unique" in preparation[1]


@pytest.mark.parametrize("text,at", [
    (SHARED, AT + timedelta(days=1)),
    ("We reviewed the launch proposal but nothing else matches this unrelated discussion.", AT),
    ("Yes thanks", AT),
])
def test_unrelated_or_different_day_meetings_stay_separate(client, text, at):
    done(client)
    done(client, text, at=at, level=2000)
    # Include the next day in projection for the different-day case.
    values = client.app.state.memory.recaps.groups(AT + timedelta(days=2))
    assert len(values) == 2


def test_all_parts_form_one_conversation_and_wait_for_missing_parts(client):
    recording = str(uuid4())
    first = done(client, recording_id=recording, part_index=0, part_count=3)
    last = done(client, "The final point is a new contract.", at=AT + timedelta(seconds=2),
                recording_id=recording, part_index=2, part_count=3)
    assert len(groups(client)) == 1 and not groups(client)[0].ready
    middle = done(client, "Middle of this meeting", at=AT + timedelta(seconds=1),
                  recording_id=recording, part_index=1, part_count=3)
    assert len(groups(client)) == 1 and groups(client)[0].ready
    assert {r["id"] for r in groups(client)[0].sources} == {first, middle, last}


def test_explicit_conversation_choice_combines_complementary_text(client):
    first = done(client)
    second = done(client, "Entirely different notes from the same meeting.",
                  conversation_source_id=first, level=2000)
    assert len(groups(client)) == 1
    assert {r["id"] for r in groups(client)[0].sources} == {first, second}
    conflict = upload(client, key=second, level=2000, conversation_source_id=str(uuid4()))
    assert conflict.status_code == 409


def test_silent_parts_do_not_leave_finished_recording_pending(client):
    recording = str(uuid4())
    first = done(client, recording_id=recording, part_index=0, part_count=2)
    done(client, "", at=AT + timedelta(seconds=1),
         recording_id=recording, part_index=1, part_count=2)
    assert len(groups(client)) == 1 and groups(client)[0].ready
    assert [s["id"] for s in client.app.state.memory.recaps.sources(groups(client)[0])] == [first]


def test_deleted_part_does_not_block_retained_evidence(client):
    recording = str(uuid4())
    first = done(client, recording_id=recording, part_index=0, part_count=2)
    second = done(client, "A closing agreement.", at=AT + timedelta(seconds=1),
                  recording_id=recording, part_index=1, part_count=2)
    client.delete(f"/v1/audio/{first}")
    assert len(groups(client)) == 1 and groups(client)[0].ready
    assert groups(client)[0].sources[0]["id"] == second


def test_complete_reupload_can_replace_an_abandoned_partial_upload(client):
    bounds = {"recording_started_at": stamp(AT),
              "recording_ended_at": stamp(AT + timedelta(seconds=2))}
    done(client, recording_id=str(uuid4()), part_index=0, part_count=2, **bounds)
    assert not groups(client)[0].ready
    replacement = str(uuid4())
    done(client, recording_id=replacement, part_index=0, part_count=2, **bounds)
    done(client, "The last point", at=AT + timedelta(seconds=1),
         recording_id=replacement, part_index=1, part_count=2, **bounds)
    assert len(groups(client)) == 1 and groups(client)[0].ready


def test_matching_updates_after_correction_deletion_and_restart(client):
    first = done(client)
    second = done(client, SHARED + " Extra closing point.", level=2000)
    assert len(groups(client)) == 1
    store = client.app.state.memory.store
    Store(store.root)
    assert len(groups(client)) == 1
    client.patch(f"/v1/transcripts/{second}", json={"text": "An unrelated recording"})
    assert len(groups(client)) == 2
    client.patch(f"/v1/transcripts/{second}", json={"text": SHARED})
    assert len(groups(client)) == 1
    client.delete(f"/v1/audio/{first}")
    assert len(groups(client)) == 1
    assert groups(client)[0].sources[0]["id"] == second


@pytest.mark.parametrize("import_first", [True, False])
def test_hosted_transcript_and_uploaded_audio_match_in_either_order(client, import_first):
    remote = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json=[{
            "id": "hosted-meeting", "started_at": stamp(AT),
            "finished_at": stamp(AT + timedelta(seconds=1)),
            "transcript_segments": [{"start": 0, "end": 1, "text": SHARED,
                                     "speaker_id": 1}],
        }],
    )))
    client.app.state.omi.client = remote
    client.app.state.omi.key = "test-only"
    try:
        if not import_first:
            done(client)
        response = client.post("/v1/integrations/omi/import", json={})
        assert response.status_code == 200, response.text
        if import_first:
            done(client)
        assert len(groups(client)) == 1
        assert {r["source_kind"] for r in groups(client)[0].sources} == {"manual_upload", "omi_api"}
    finally:
        asyncio.run(remote.aclose())


def test_late_alternative_source_refreshes_recap_and_keeps_its_new_detail(client):
    done(client)
    assert client.post("/v1/recaps/2026-10-15").status_code == 200
    done(client, SHARED + " A unique closing commitment is to call the supplier.", level=2000)
    assert client.get("/v1/recaps/2026-10-15").json()["stale"] is True
    updated = client.post("/v1/recaps/2026-10-15").json()
    assert updated["evidence"]["pipeline"]["conversation_count"] == 1
    assert "call the supplier" in updated["summary"]


def test_transcript_expiry_keeps_remaining_part_usable(client):
    recording = str(uuid4())
    first = done(client, recording_id=recording, part_index=0, part_count=2)
    second = done(client, "The final decision.", at=AT + timedelta(seconds=1),
                  recording_id=recording, part_index=1, part_count=2)
    store = client.app.state.memory.store
    store.apply_retention(0, 1, AT + timedelta(days=1, seconds=1, microseconds=1))
    assert store.get_audio(first)["state"] == "expired"
    assert store.get_audio(second)["state"] == "done"
    assert len(groups(client)) == 1 and groups(client)[0].ready


def test_invalid_group_metadata_does_not_ingest_audio(client):
    assert upload(client, recording_id=str(uuid4()), part_index=2, part_count=2).status_code == 422
    assert upload(client, conversation_source_id=str(uuid4())).status_code == 409
    assert client.get("/v1/status").json()["audio"] == {}
