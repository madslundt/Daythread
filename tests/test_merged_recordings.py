import io
import json
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings


@pytest.fixture
def recording(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, worker_enabled=False,
                                       timezone="UTC", _env_file=None))) as client:
        store = client.app.state.memory.store
        recording_id = str(uuid4())
        at = datetime(2026, 10, 16, 9, tzinfo=UTC)
        ids, samples = [], []
        texts = ["Launch planning ☕.", "The launch continues. Garden plans begin.",
                 "Water the roses."]
        for index, text in enumerate(texts):
            key = str(uuid4())
            pcm = (index + 1).to_bytes(2, "little", signed=True) * 32000
            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
                wav.writeframes(pcm)
            store.ingest(key, "manual-web", at + timedelta(seconds=2 * index),
                         audio.getvalue(), 2, source_kind="manual_upload", source_id="meeting.wav",
                         source_metadata={"recording_id": recording_id, "part_index": index,
                                          "part_count": 3})
            store.complete(key, text)
            ids.append(key)
            samples.append(pcm)
        split = texts[1].index("Garden")
        sections = [
            [{"title": "Launch", "chapter_id": "launch", "start_offset": 0,
              "end_offset": len(texts[0]), "start_seconds": 0, "end_seconds": 2}],
            [{"title": "Launch", "chapter_id": "launch", "start_offset": 0,
              "end_offset": split, "start_seconds": 0, "end_seconds": 1},
             {"title": "Garden", "chapter_id": "garden", "start_offset": split,
              "end_offset": len(texts[1]), "start_seconds": 1, "end_seconds": 2}],
            [{"title": "Garden", "chapter_id": "garden", "start_offset": 0,
              "end_offset": len(texts[2]), "start_seconds": 0, "end_seconds": 2}],
        ]
        with store.connect() as db:
            for key, spans in zip(ids, sections):
                db.execute("UPDATE audio SET topic_details=? WHERE id=?", (
                    json.dumps({"title": "Launch and garden", "sections": spans}), key))
        yield client, store, recording_id, ids, texts, b"".join(samples)


def test_upload_is_one_recording_in_the_timeline(recording):
    client, _, recording_id, _, _, _ = recording
    result = client.get("/v1/recording-groups?day=2026-10-16").json()
    assert result["total_recordings"] == 1
    assert result["items"][0]["recording_id"] == recording_id
    assert result["items"][0]["recording_count"] == 1
    assert result["items"][0]["sources"] == []


def test_combines_complete_transcript_and_topics_with_global_offsets(recording):
    client, _, recording_id, _, texts, _ = recording
    response = client.get(f"/v1/recordings/{recording_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["text"] == "\n".join(texts)
    assert result["state"] == "done" and result["audio_available"]
    assert [section["title"] for section in result["sections"]] == ["Launch", "Garden"]
    assert result["sections"][1]["start_offset"] == result["text"].index("Garden")
    assert result["sections"][1]["start_seconds"] == 3
    assert result["sections"][-1]["end_seconds"] == 6
    assert "".join(result["text"][s["start_offset"]:s["end_offset"]]
                   for s in result["sections"]) == result["text"]


def test_combines_sample_exact_audio_and_seeks_across_a_part_boundary(recording):
    client, _, recording_id, _, _, pcm = recording
    url = f"/v1/recordings/{recording_id}/audio"
    response = client.get(url)
    assert response.status_code == 200
    with wave.open(io.BytesIO(response.content)) as wav:
        assert wav.getnframes() == 96000
        assert wav.readframes(96000) == pcm
    start, end = 44 + 64000 - 8, 44 + 64000 + 7
    seek = client.get(url, headers={"Range": f"bytes={start}-{end}"})
    assert seek.status_code == 206
    assert seek.content == response.content[start:end + 1]
    assert seek.headers["content-range"] == f"bytes {start}-{end}/{len(response.content)}"
    assert client.get(url, headers={"Range": "bytes=-10"}).content == pcm[-10:]
    assert client.get(url, headers={"Range": "bytes=999999-"}).status_code == 416


def test_waveform_covers_all_parts_without_joining_them_in_memory(recording, monkeypatch):
    client, store, recording_id, ids, _, _ = recording
    reads, read = [], store.read_audio

    def tracked(key):
        reads.append(key)
        return read(key)

    monkeypatch.setattr(store, "read_audio", tracked)
    response = client.get(f"/v1/recordings/{recording_id}/waveform")
    assert response.status_code == 200
    assert reads == ids
    result = response.json()
    assert result["duration"] == 6
    assert len(result["peaks"]) == 160
    assert result["peaks"][0] == pytest.approx(1 / 3)
    assert result["peaks"][-1] == 1


def test_missing_part_does_not_publish_a_partial_recording_as_complete(recording):
    client, store, recording_id, ids, _, _ = recording
    store.delete_audio(ids[1])
    result = client.get(f"/v1/recordings/{recording_id}").json()
    assert result["state"] != "done"
    assert result["text"] is None and result["sections"] == []
    assert not result["audio_available"]
    assert client.get(f"/v1/recordings/{recording_id}/audio").status_code == 409


def test_corrections_and_audio_retention_are_reflected_without_a_stale_merged_copy(recording):
    client, store, recording_id, ids, texts, _ = recording
    before = client.get(f"/v1/recordings/{recording_id}").json()
    store.correct_transcript(ids[1], "Corrected words.")
    after = client.get(f"/v1/recordings/{recording_id}").json()
    assert after["text"] == "\n".join([texts[0], "Corrected words.", texts[2]])
    assert after["transcript_version"] != before["transcript_version"]
    assert after["sections"] == []  # Never publish a mixture of old and refreshed chapters.
    store.apply_retention(1, 0, now=datetime(2026, 10, 20, tzinfo=UTC))
    retained = client.get(f"/v1/recordings/{recording_id}").json()
    assert retained["text"] == after["text"] and not retained["audio_available"]
    assert client.get(f"/v1/recordings/{recording_id}/audio").status_code == 404


def test_seeking_reads_only_the_required_part_and_head_reads_none(recording, monkeypatch):
    client, store, recording_id, ids, _, pcm = recording
    reads, read = [], store.read_audio

    def tracked(key):
        reads.append(key)
        return read(key)

    monkeypatch.setattr(store, "read_audio", tracked)
    url = f"/v1/recordings/{recording_id}/audio"
    head = client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == len(pcm) + 44
    assert reads == []
    response = client.get(url, headers={"Range": "bytes=-20"})
    assert response.content == pcm[-20:]
    assert reads == [ids[-1]]


def test_recording_cannot_be_read_from_another_profile(recording):
    client, _, recording_id, _, _, _ = recording
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    for suffix in ("", "/audio", "/waveform"):
        response = client.get(f"/v1/recordings/{recording_id}{suffix}",
                              headers={"X-Omi-Profile": profile})
        assert response.status_code == 404
