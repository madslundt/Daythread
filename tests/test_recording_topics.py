import asyncio
import io
import json
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings

TEXT = (
    "Vi taler om kakao med chili. Det kan ikke anbefales.\n"
    "Jeg har bestilt en OMI som optager lyd og giver recap.\n"
    "Let's turn to English and talk about the Tesla Roadster. It sounds crazy."
)
OUTPUT = {
    "title": "Kakao, Omi og Tesla Roadster",
    "sections": [
        {"title": "Kakao med chili", "start_quote": "Vi taler om kakao"},
        {"title": "Omi og dagens recap", "start_quote": "Jeg har bestilt en OMI"},
        {"title": "Tesla Roadster", "start_quote": "Let's turn to English"},
    ],
}


@pytest.fixture
def recording(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        key = str(uuid4())
        out = io.BytesIO()
        with wave.open(out, "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
            stream.writeframes(b"\1\0" * 16000)
        memory.store.ingest(key, "web", datetime(2026, 10, 16, tzinfo=UTC), out.getvalue(), 1)
        memory.store.complete(key, TEXT)
        yield client, memory, key


def run(memory):
    from omi_local.recording_topics import process_topics

    return asyncio.run(process_topics(memory))


class Model:
    def __init__(self, output=OUTPUT, during=None):
        self.output, self.during, self.calls = output, during, 0

    async def generate_json(self, task, evidence):
        self.calls += 1
        assert "topic" in task.lower()
        assert TEXT in evidence
        if self.during:
            self.during()
        return json.dumps(self.output)


def test_backfill_generates_three_titled_sections_without_changing_original(recording):
    client, memory, key = recording
    memory.inference = Model()
    before = memory.store.get_audio(key)
    assert run(memory)
    result = client.get(f"/v1/transcripts/{key}").json()
    assert result["title"] == OUTPUT["title"]
    assert [s["title"] for s in result["sections"]] == [s["title"] for s in OUTPUT["sections"]]
    assert "".join(TEXT[s["start_offset"]:s["end_offset"]] for s in result["sections"]) == TEXT
    assert result["sections"][1]["start_offset"] == TEXT.index("Jeg har")
    assert result["text"] == TEXT
    assert result["started_at"] == before["started_at"]
    assert result["source_version"] == before["source_version"]
    assert client.get("/v1/transcripts").json()["items"][0]["title"] == OUTPUT["title"]
    assert not run(memory)
    assert memory.inference.calls == 1


@pytest.mark.parametrize("mutation", ["correct", "delete", "expire"])
def test_changed_evidence_cannot_publish_old_sections(recording, mutation):
    client, memory, key = recording

    def change():
        if mutation == "correct":
            memory.store.correct_transcript(key, "Changed transcript")
        elif mutation == "delete":
            client.delete(f"/v1/audio/{key}")
        else:
            memory.store.apply_retention(0, 1, now=datetime(2026, 10, 20, tzinfo=UTC))

    memory.inference = Model(during=change)
    assert run(memory)
    result = client.get(f"/v1/transcripts/{key}")
    if result.status_code == 200:
        assert result.json()["title"] is None
        assert result.json()["sections"] == []


def test_correction_clears_existing_sections(recording):
    client, memory, key = recording
    memory.inference = Model()
    run(memory)
    response = client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected words"})
    assert response.json()["title"] is None
    assert response.json()["sections"] == []


@pytest.mark.parametrize("output", [
    {}, {"title": "Wrong", "sections": []},
    {"title": "Wrong", "sections": [{"title": "Missing", "start_quote": "Invented quote"}]},
    {"title": "Wrong", "sections": list(reversed(OUTPUT["sections"]))},
])
def test_invalid_analysis_retries_without_affecting_transcription(recording, output):
    client, memory, key = recording
    memory.inference = Model(output)
    assert run(memory)
    result = client.get(f"/v1/transcripts/{key}").json()
    assert result["state"] == "done" and result["text"] == TEXT
    assert result["sections"] == [] and result["title"] is None
    assert not run(memory)  # Backoff prevents a broken model from spinning.
    from omi_local.domain import utc_now
    from omi_local.recording_topics import process_topics

    assert asyncio.run(process_topics(memory, now=utc_now() + timedelta(minutes=2)))
    assert memory.inference.calls == 2


def test_audio_expiry_preserves_topics_but_transcript_expiry_purges_them(recording):
    client, memory, key = recording
    memory.inference = Model()
    run(memory)
    memory.store.apply_retention(1, 0, now=datetime(2026, 10, 20, tzinfo=UTC))
    assert client.get(f"/v1/transcripts/{key}").json()["title"] == OUTPUT["title"]
    memory.store.apply_retention(0, 1, now=datetime(2026, 10, 20, tzinfo=UTC))
    assert client.get(f"/v1/transcripts/{key}").json()["sections"] == []


def test_summary_language_change_reanalyzes_existing_recordings(recording):
    client, memory, key = recording
    memory.inference = Model()
    run(memory)
    assert not run(memory)
    memory.settings = memory.settings.model_copy(update={"summary_language": "da"})
    assert run(memory)
    assert memory.inference.calls == 2


@pytest.mark.parametrize("text", [
    "Hej", "\n".join(f"Topic line {i}: a complete sentence." for i in range(1000)) + " TAIL",
], ids=["short", "long"])
def test_short_and_long_transcripts_are_covered_completely(recording, text):
    client, memory, key = recording
    memory.store.correct_transcript(key, text)

    class WholeTopic:
        def __init__(self):
            self.excerpts = []

        async def generate_json(self, task, evidence):
            self.excerpts.append(evidence)
            return json.dumps({"title": "Topic", "sections": [
                {"title": "Topic", "start_quote": evidence[:100]},
            ]})

    memory.inference = WholeTopic()
    run(memory)
    result = client.get(f"/v1/transcripts/{key}").json()
    assert len(result["sections"]) == 1
    assert result["title"] == "Topic"
    assert "".join(memory.inference.excerpts) == text
    assert "".join(text[s["start_offset"]:s["end_offset"]] for s in result["sections"]) == text
    assert max(map(len, memory.inference.excerpts)) <= 12000


def test_adjacent_matching_topics_merge_but_later_returns_stay_chronological(recording):
    client, memory, key = recording
    text = TEXT + "\nTilbage til kakao: vi prøver mørk chokolade."
    memory.store.correct_transcript(key, text)
    memory.inference = Model({
        "title": "Kakao og Omi",
        "sections": [
            {"title": "Kakao", "start_quote": "Vi taler om kakao"},
            {"title": "KAKAO!", "start_quote": "Det kan ikke anbefales"},
            {"title": "Omi", "start_quote": "Jeg har bestilt en OMI"},
            {"title": "Kakao", "start_quote": "Tilbage til kakao"},
        ],
    })
    assert run(memory)
    result = client.get(f"/v1/transcripts/{key}").json()
    assert [section["title"] for section in result["sections"]] == ["Kakao", "Omi", "Kakao"]
    assert result["sections"][0]["end_offset"] == TEXT.index("Jeg har")
    assert "".join(text[s["start_offset"]:s["end_offset"]] for s in result["sections"]) == text
