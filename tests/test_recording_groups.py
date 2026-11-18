"""Timeline grouping preserves every original while paging sessions before their members."""

import io
import json
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


def recording(client, at=AT, *, device="omi", kind="", text="Recorded discussion", level=1,
              duration=None, source_id="", source_metadata=None):
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        frames = round(duration * 16000) if duration is not None else 160
        stream.writeframes(level.to_bytes(2, "little", signed=True) * frames)
    key = str(uuid4())
    store = client.app.state.memory.store
    store.ingest(key, device, at, audio.getvalue(), duration or 60, source_kind=kind,
                 source_id=source_id, source_metadata=source_metadata)
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
    assert result["total"] == 2
    assert result["total_recordings"] == 2
    assert [group["recording_count"] for group in result["items"]] == [1, 1]


def test_timeline_only_counts_and_pages_completed_nonempty_transcripts(client):
    visible = recording(client)
    hidden = [recording(client, AT + timedelta(minutes=index + 10), text=text)
              for index, text in enumerate([
                  "", " \n\t\r \u00a0\u2003", "Not completed", "Failed", "Expired",
              ])]
    store = client.app.state.memory.store
    with store.connect() as db:
        for key, state in zip(hidden[2:], ["queued", "failed", "expired"], strict=True):
            db.execute("UPDATE audio SET state=? WHERE id=?", (state, key))
    result = listing(client, limit=1)
    assert result["total"] == result["total_recordings"] == 1
    assert result["items"][0]["sources"][0]["id"] == visible
    assert listing(client, limit=1, offset=1)["items"] == []
    for key in hidden:
        assert client.get(f"/v1/recording-groups/{key}/sources", params={
            "day": "2026-10-16",
        }).status_code == 404
    # Filtering the timeline must not delete original archive entries.
    assert all(store.get_audio(key) for key in hidden)


def test_empty_duplicate_upload_is_excluded_before_canonical_selection(client):
    recording(client, device="web", kind="manual_upload", text="")
    visible = recording(client, AT + timedelta(minutes=1), device="web", kind="manual_upload")
    result = listing(client)
    assert result["total"] == result["total_recordings"] == 1
    source = result["items"][0]["sources"][0]
    assert source["id"] == visible and source["alternate_sources"] == []


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


def test_short_omi_chunks_have_one_transcript_and_seekable_audio_without_topics(client):
    first = recording(client, text="First few seconds.", duration=4)
    recording(client, AT + timedelta(seconds=4), text="The conversation continues.", duration=4)
    response = client.get(f"/v1/recording-groups/{first}/recording", params={"day": "2026-10-16"})
    assert response.status_code == 200
    result = response.json()
    assert result["text"] == "First few seconds.\nThe conversation continues."
    assert result["state"] == "done"
    assert result["audio_available"] and result["sections"] == []
    assert result["interruptions"] == []
    audio = client.get(f"/v1/recording-groups/{first}/audio", params={"day": "2026-10-16"})
    assert audio.status_code == 200
    with wave.open(io.BytesIO(audio.content)) as stream:
        assert stream.getnframes() == 128000
    scoped = client.get(f"/v1/recording-groups/{first}/recording", params={
        "day": "2026-10-16", "q": "First few seconds",
    }).json()
    assert scoped["text"] == "First few seconds."
    assert client.get(f"/v1/recording-groups/{first}/audio", params={
        "day": "2026-10-15",
    }).status_code == 404


def test_session_playback_marks_gaps_and_overlaps_and_supports_seeking(client):
    first = recording(client, duration=4, level=1)
    recording(client, AT + timedelta(seconds=6), duration=4, level=2)
    recording(client, AT + timedelta(seconds=9), duration=4, level=3)
    base = f"/v1/recording-groups/{first}"
    params = {"day": "2026-10-16"}
    result = client.get(f"{base}/recording", params=params).json()
    assert result["duration_seconds"] == 12
    assert [(item["position_seconds"], item["gap_seconds"])
            for item in result["interruptions"]] == [(4, 2), (8, -1)]
    audio = client.get(f"{base}/audio", params=params).content
    seek = client.get(f"{base}/audio", params=params, headers={"Range": "bytes=128040-128047"})
    assert seek.status_code == 206 and seek.content == audio[128040:128048]
    assert client.head(f"{base}/audio", params=params).content == b""
    waveform = client.get(f"{base}/waveform", params=params).json()
    assert waveform["duration"] == 12 and len(waveform["peaks"]) == 160
    assert waveform["peaks"][0] == pytest.approx(1 / 3)
    assert waveform["peaks"][-1] == 1


def test_session_spans_member_pages_and_only_includes_transcribed_recordings(client):
    first = recording(client, text="Passage 0", duration=4)
    for index in range(1, 32):
        recording(client, AT + timedelta(seconds=4 * index), text=f"Passage {index}", duration=4)
    last = recording(client, AT + timedelta(seconds=128), duration=4)
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute("UPDATE audio SET state='queued',text=NULL WHERE id=?", (last,))
    result = members(client, first, limit=1)
    assert len(result["items"]) == 1 and result["total"] == 32
    combined = result["recording"]
    assert combined["text"] == "\n".join(f"Passage {index}" for index in range(32))
    assert combined["state"] == "done" and combined["audio_available"]
    assert combined["duration_seconds"] == 128
    assert last not in {source["id"] for source in members(client, first, limit=200)["items"]}


def test_multipart_timeline_plays_transcribed_parts_when_other_parts_have_no_text(client):
    keys = [recording(client, AT + timedelta(seconds=4 * index), device="web",
                      kind="manual_upload", text=text, duration=4, level=index + 1)
            for index, text in enumerate(["First passage", "", "Last passage"])]
    recording_id = str(uuid4())
    with client.app.state.memory.store.connect() as db:
        for index, key in enumerate(keys):
            metadata = {"recording_id": recording_id, "part_index": index, "part_count": 3}
            db.execute("UPDATE audio SET source_metadata=? WHERE id=?", (json.dumps(metadata), key))
    group = listing(client)["items"][0]
    assert group["recording_count"] == 1 and group["recording_id"] == recording_id
    result = members(client, group["id"])
    assert [source["id"] for source in result["items"]] == [keys[0], keys[2]]
    combined = result["recording"]
    assert combined["text"] == "First passage\nLast passage"
    assert combined["state"] == "done" and combined["audio_available"]
    assert combined["duration_seconds"] == 8
    assert combined["interruptions"][0]["gap_seconds"] == 4
    playback = client.get(f"/v1/recording-groups/{group['id']}/audio", params={"day": "2026-10-16"})
    assert playback.status_code == 200
    with wave.open(io.BytesIO(playback.content)) as audio:
        assert audio.getnframes() == 8 * 16000
    assert combined["sections"] == []


def test_session_audio_version_rejects_changed_membership_and_respects_profiles(client):
    first = recording(client, duration=4)
    base = f"/v1/recording-groups/{first}"
    params = {"day": "2026-10-16"}
    before = client.get(f"{base}/recording", params=params).json()
    scoped = params | {"version": before["audio_version"]}
    assert client.get(f"{base}/audio", params=scoped).status_code == 200
    recording(client, AT + timedelta(seconds=4), duration=4)
    for suffix in ("audio", "waveform"):
        assert client.get(f"{base}/{suffix}", params=scoped).status_code == 409
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    for suffix in ("recording", "audio", "waveform"):
        response = client.get(f"{base}/{suffix}", params=params, headers={"X-Omi-Profile": profile})
        assert response.status_code == 404


def test_session_corrections_and_retention_update_the_combined_view(client):
    first = recording(client, text="Before correction.", duration=4)
    second = recording(client, AT + timedelta(seconds=4), text="Second passage.", duration=4)
    store = client.app.state.memory.store
    before = members(client, first)["recording"]
    store.correct_transcript(second, "Corrected passage.")
    after = members(client, first)["recording"]
    assert after["text"] == "Before correction.\nCorrected passage."
    assert after["transcript_version"] != before["transcript_version"]
    assert after["audio_version"] == before["audio_version"]
    store.apply_retention(1, 0, now=AT + timedelta(days=4))
    retained = members(client, first)["recording"]
    assert retained["text"] == after["text"] and not retained["audio_available"]
    assert client.get(f"/v1/recording-groups/{first}/audio", params={
        "day": "2026-10-16",
    }).status_code == 404


def parrot(client, role, *, at=AT, timestamp="2026-10-16T09-00-00Z", **kwargs):
    return recording(client, at, device="manual-web", kind="manual_upload",
                     source_id=f"{role}_{timestamp}.caf", duration=4, **kwargs)


@pytest.mark.parametrize("archive", (False, True))
def test_parrot_pair_is_one_recording_with_one_seekable_mix(client, archive):
    system = parrot(client, "system", text="Remote speaker.", level=1200)
    mic = parrot(client, "mic", at=AT + timedelta(seconds=1), text="Local speaker.", level=600)
    result = listing(client, archive=archive)
    assert result["total"] == result["total_recordings"] == 1
    group = result["items"][0]
    assert group["id"] == system and group["parallel_tracks"] and group["recording_count"] == 1
    assert group["sources"] == [] and "recording_id" not in group
    detail = members(client, system, archive=archive)
    assert [row["id"] for row in detail["items"]] == [system, mic]
    assert detail["total"] == 2 and detail["chapters"] == []
    mixed = detail["recording"]
    assert mixed["text"] == "Remote speaker.\nLocal speaker."
    assert mixed["audio_available"] and mixed["sections"] == mixed["interruptions"] == []
    assert mixed["duration_seconds"] == 5 and mixed["ended_at"] == group["ended_at"]
    params = {"day": "2026-10-16", "archive": archive, "version": mixed["audio_version"]}
    url = f"/v1/recording-groups/{system}/audio"
    response = client.get(url, params=params)
    assert response.status_code == 200
    with wave.open(io.BytesIO(response.content)) as wav:
        assert wav.getnframes() == 80000
        for frames, level in ((16000, 1200), (48000, 900), (16000, 600)):
            assert wav.readframes(frames) == level.to_bytes(2, "little") * frames
    for start, end in ((39, 49), (32039, 32051), (65577, 65591), (128039, 128051)):
        position = {"Range": f"bytes={start}-{end}"}
        seek = client.get(url, params=params, headers=position)
        assert seek.status_code == 206 and seek.content == response.content[start:end + 1]
        assert seek.headers["content-range"] == f"bytes {start}-{end}/{len(response.content)}"
    waveform = client.get(f"/v1/recording-groups/{system}/waveform", params=params).json()
    assert waveform["duration"] == 5
    assert [waveform["peaks"][i] for i in (0, 80, 159)] == [1, 0.75, 0.5]
    for key, level in ((system, 1200), (mic, 600)):
        original = client.get(f"/v1/audio/{key}")
        with wave.open(io.BytesIO(original.content)) as wav:
            assert wav.getnframes() == 64000
            assert wav.readframes(1) == level.to_bytes(2, "little")


@pytest.mark.parametrize("case", ("other timestamp", "no overlap", "ambiguous", "invalid date"))
def test_parrot_requires_unambiguous_filename_and_capture_overlap(client, case):
    timestamp = "2026-13-16T09-00-00Z" if case == "invalid date" else "2026-10-16T09-00-00Z"
    parrot(client, "system", timestamp=timestamp)
    parrot(client, "mic", level=2,
           at=AT + timedelta(seconds=4 if case == "no overlap" else 1),
           timestamp="2026-10-16T09-00-01Z" if case == "other timestamp" else timestamp)
    if case == "ambiguous":
        parrot(client, "system", level=3, text="Unrelated capture.")
    result = listing(client, archive=True)
    assert result["total"] == result["total_recordings"] == (3 if case == "ambiguous" else 2)
    assert not any(item.get("parallel_tracks") for item in result["items"])
    if case == "ambiguous":
        scoped = listing(client, q="discussion")["items"]
        assert not any(item.get("parallel_tracks") for item in scoped)


def test_parrot_scoped_search_and_tags_keep_their_original_boundaries(client):
    system = parrot(client, "system", text="Remote speaker only.")
    mic = parrot(client, "mic", at=AT + timedelta(seconds=1), text="Local speaker only.")
    parrot(client, "system", text="Retry of the same PCM.")
    # Equal sampled audio on the other track isn't a duplicate; a true retry is.
    result = listing(client)
    pair = result["items"][0]
    assert result["total_recordings"] == 1
    assert members(client, pair["id"])["total"] == 2
    for q in ("Local speaker", "Remote speaker"):
        result = listing(client, q=q, archive=True)
        assert result["total_recordings"] == 1
        source = result["items"][0]["sources"][0]
        assert q in source["text"] and source["alternate_sources"] == []
        assert not result["items"][0].get("parallel_tracks")
    path = f"/v1/recording-groups/{pair['id']}/tags?day=2026-10-16"
    before = client.get(path).json()
    assert before["source_count"] == 3
    response = client.put(path, json={"action": "add", "name": "Parrot call",
                                     "expected_revision": before["revision"],
                                     "expected_membership": before["membership"]})
    assert response.status_code == 200, response.text
    tag_id = response.json()["tags"][0]["id"]
    tagged = listing(client, tag_id=tag_id)
    assert tagged["total_recordings"] == 1 and tagged["items"][0]["parallel_tracks"]
    assert client.get(f"/v1/sources/{mic}/tags").json()["tags"][0]["id"] == tag_id
    assert client.get(f"/v1/sources/{system}/tags").json()["tags"][0]["id"] == tag_id


def test_parrot_separately_uploaded_multipart_tracks_keep_silent_parts_and_surviving_audio(client):
    ids = {}
    for role, delay, levels in (("system", 0, [500, 1000]), ("mic", 1, [500, 400])):
        record_id = str(uuid4())
        ids[role] = [parrot(client, role, at=AT + timedelta(seconds=delay + 4 * index),
                           text=f"{role} speech." if index else "", level=level,
                           source_metadata={"recording_id": record_id, "part_index": index,
                                            "part_count": 2}) for index, level in enumerate(levels)]
    row = listing(client)["items"][0]
    group_id = ids["system"][0]
    assert row["id"] == group_id and row["parallel_tracks"] and row["recording_count"] == 1
    detail = members(client, group_id)
    assert detail["total"] == 4
    before = detail["recording"]
    assert before["text"] == "system speech.\nmic speech." and before["duration_seconds"] == 9
    assert before["mixed_audio"] is True
    store = client.app.state.memory.store
    store.audio_storage.delete(ids["mic"][0])
    with store.connect() as db:
        db.execute("UPDATE audio SET audio_available=0 WHERE id=?", (ids["mic"][1],))
    after = members(client, group_id)["recording"]
    assert after["text"] == before["text"] and after["audio_availability"] == "partial"
    assert after["audio_version"] != before["audio_version"]
    url = f"/v1/recording-groups/{group_id}/audio"
    params = {"day": "2026-10-16"}
    assert client.get(url, params=params | {"version": before["audio_version"]}).status_code == 409
    audio = client.get(url, params=params | {"version": after["audio_version"]},
                       headers={"If-Range": f'"{before["audio_version"]}"', "Range": "bytes=44-45"})
    assert audio.status_code == 200 and audio.headers["etag"] == f'"{after["audio_version"]}"'
    with wave.open(io.BytesIO(audio.content)) as wav:
        assert wav.readframes(144000) == b"\xf4\x01" * 64000 + b"\xe8\x03" * 64000 + b"\0\0" * 16000
    store.apply_retention(1, 0, now=AT + timedelta(days=4))
    retained = members(client, group_id, archive=True)["recording"]
    assert retained["text"] == before["text"] and not retained["audio_available"]
    assert client.get(url, params=params | {"archive": True}).status_code == 404
