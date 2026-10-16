"""Timeline grouping preserves every original while paging sessions before their members."""

import io
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings

AT = datetime(2026, 10, 16, 9, tzinfo=UTC)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(
        data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None,
    ))) as session:
        yield session


def recording(client, at=AT, *, device="omi", kind="", text="Recorded discussion", level=1):
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(level.to_bytes(2, "little", signed=True) * 160)
    key = str(uuid4())
    store = client.app.state.memory.store
    store.ingest(key, device, at, audio.getvalue(), 60, source_kind=kind)
    store.complete(key, text)
    return key


def listing(client, **params):
    result = client.get("/v1/recording-groups", params={"day": "2026-10-16", **params})
    assert result.status_code == 200, result.text
    return result.json()


def members(client, key, **params):
    result = client.get(f"/v1/recording-groups/{key}/sources", params={
        "day": "2026-10-16", **params,
    })
    assert result.status_code == 200, result.text
    return result.json()


def test_nearby_recordings_form_one_group_before_pagination(client):
    ids = [recording(client, AT + timedelta(minutes=index)) for index in range(40)]
    later = recording(client, AT + timedelta(hours=2))
    first = listing(client, limit=1)
    assert first["total"] == 2 and first["total_recordings"] == 41
    assert first["items"][0]["sources"][0]["id"] == later
    group = listing(client, limit=1, offset=1)["items"][0]
    assert group["id"] == ids[0] and group["recording_count"] == 40
    assert group["sources"] == []  # Bodies load only when the group is opened.
    loaded = []
    for offset in (0, 15, 30):
        page = members(client, group["id"], limit=15, offset=offset)
        assert page["total"] == 40
        loaded.extend(source["id"] for source in page["items"])
    assert loaded == ids
    assert client.get(f"/v1/transcripts/{ids[-1]}").json()["text"] == "Recorded discussion"


def test_devices_and_long_gaps_stay_separate_but_overlaps_do_not_split(client):
    recording(client)
    recording(client, AT + timedelta(seconds=30))
    recording(client, AT + timedelta(minutes=2), device="other")
    recording(client, AT + timedelta(minutes=10))
    result = listing(client)
    assert sorted(group["recording_count"] for group in result["items"]) == [1, 1, 2]


def test_silence_only_runs_do_not_bridge_distant_speech(client):
    recording(client)
    for minute in range(1, 12):
        recording(client, AT + timedelta(minutes=minute), text="")
    recording(client, AT + timedelta(minutes=12))
    result = listing(client)
    assert result["total"] == 3
    assert sorted(group["recording_count"] for group in result["items"]) == [1, 1, 11]


def test_identical_uploads_keep_alternatives_inside_their_original_row(client):
    first = recording(client, device="web", kind="manual_upload")
    second = recording(client, AT + timedelta(minutes=2), device="web", kind="manual_upload")
    third = recording(
        client, AT + timedelta(minutes=3), device="web", kind="manual_upload", level=2,
    )
    group = listing(client)["items"][0]
    assert group["recording_count"] == 2
    sources = members(client, group["id"])["items"]
    assert [source["id"] for source in sources] == [first, third]
    assert sources[0]["alternate_sources"][0]["id"] == second


def test_search_and_day_scope_preserve_access_to_matching_originals(client):
    one = recording(client, text="A literal 25% target")
    recording(client, AT + timedelta(minutes=1), text="Different discussion")
    recording(client, AT - timedelta(days=1), text="Yesterday 25% target")
    result = listing(client, q="25%")
    assert result["total"] == 1 and result["total_recordings"] == 1
    assert result["items"][0]["sources"][0]["id"] == one
    missing = client.get(f"/v1/recording-groups/{one}/sources", params={"day": "2026-10-15"})
    assert missing.status_code == 404


def test_late_recording_joins_the_existing_group_and_deleted_members_disappear(client):
    first = recording(client)
    recording(client, AT + timedelta(minutes=4))
    assert listing(client)["items"][0]["recording_count"] == 2
    late = recording(client, AT + timedelta(minutes=2))
    assert listing(client)["items"][0]["id"] == first
    assert listing(client)["items"][0]["recording_count"] == 3
    client.delete(f"/v1/audio/{late}")
    assert listing(client)["items"][0]["recording_count"] == 2
    assert late not in {source["id"] for source in members(client, first)["items"]}


def test_forwarded_audio_is_compact_but_keeps_estimated_time_disclosure(client):
    recording(client, kind="omi_audio_webhook")
    recording(client, AT + timedelta(minutes=1), kind="omi_audio_webhook")
    group = listing(client)["items"][0]
    assert group["recording_count"] == 2 and group["estimated_times"]
    assert all(source["timestamp_basis"] == "receipt_end_estimate"
               for source in members(client, group["id"])["items"])
