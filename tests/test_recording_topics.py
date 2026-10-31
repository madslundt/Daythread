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
    assert all("start_seconds" not in section for section in result["sections"])
    assert client.get("/v1/transcripts").json()["items"][0]["title"] == OUTPUT["title"]
    assert not run(memory)
    assert memory.inference.calls == 1


def test_today_reports_waiting_and_active_topic_work_without_counting_it_twice(recording):
    client, memory, key = recording
    assert client.get("/v1/status").json()["generation"] == {
        "queued": 1, "running": 0, "stages": {},
    }

    def during():
        observation = client.get("/v1/status").json()["generation"]
        active = observation.pop("active")
        assert observation == {
            "queued": 0, "running": 1, "stages": {"organizing": 1},
        }
        assert active[0]["stage"] == "organizing"
        assert active[0]["completed_chunks"] == 0
        assert active[0]["total_chunks"] == 1
        assert active[0]["elapsed_seconds"] >= 0

    memory.inference = Model(during=during)
    assert run(memory)
    assert client.get("/v1/status").json()["generation"] == {
        "queued": 0, "running": 0, "stages": {},
    }


def test_today_topic_progress_advances_across_windows_and_clears_after_failure(recording):
    from omi_local.recording_topics import input_windows

    client, memory, key = recording
    text = "\n".join(f"Topic line {i}: a complete sentence." for i in range(1000))
    memory.store.correct_transcript(key, text)
    total = len(list(input_windows(text)))

    class FailingAfterFirstWindow:
        calls = 0

        async def generate_json(self, task, evidence):
            observation = client.get("/v1/status").json()["generation"]
            active = observation["active"][0]
            assert observation["running"] == 1
            assert active["completed_chunks"] == self.calls
            assert active["total_chunks"] == total
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("Model unavailable")
            return json.dumps({"title": "Topic", "sections": [
                {"title": "Topic", "start_quote": evidence[:100]},
            ]})

    memory.inference = FailingAfterFirstWindow()
    assert run(memory)
    assert memory.inference.calls == 2
    observation = client.get("/v1/status").json()["generation"]
    assert observation == {"queued": 1, "running": 0, "stages": {}}


def test_cancelled_topic_work_clears_active_progress_and_remains_pending(recording):
    client, memory, _ = recording

    class CancelledModel:
        async def generate_json(self, task, evidence):
            assert client.get("/v1/status").json()["generation"]["active"]
            raise asyncio.CancelledError

    memory.inference = CancelledModel()
    with pytest.raises(asyncio.CancelledError):
        run(memory)
    assert client.get("/v1/status").json()["generation"] == {
        "queued": 1, "running": 0, "stages": {},
    }


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


def test_language_prompt_upgrade_reanalyzes_existing_recordings(recording, monkeypatch):
    from omi_local import recording_topics

    client, memory, key = recording
    memory.inference = Model()
    with monkeypatch.context() as previous:
        previous.setattr(recording_topics, "VERSION", "recording-topics-2-broad-subjects")
        assert run(memory)
        assert not run(memory)
    assert run(memory)
    assert memory.inference.calls == 2
    assert not run(memory)
    assert client.get(f"/v1/transcripts/{key}").json()["text"] == TEXT


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


def multipart(recording, texts, *, total=None):
    client, memory, original = recording
    data = memory.store.read_audio(original)
    client.delete(f"/v1/audio/{original}")
    group, ids = str(uuid4()), []
    at = datetime(2026, 10, 16, 12, tzinfo=UTC)
    for index, text in enumerate(texts):
        key = str(uuid4())
        memory.store.ingest(
            key, "manual-web", at + timedelta(seconds=120 * index), data, 120,
            source_kind="manual_upload", source_metadata={
                "recording_id": group, "part_index": index, "part_count": total or len(texts),
            },
        )
        memory.store.complete(key, text)
        ids.append(key)
    return ids


def test_subject_chapters_span_upload_parts_and_recap_uses_the_same_map(recording):
    from omi_local.recaps import topic_sources

    client, memory, _ = recording
    texts = ["Project launch needs an October review.",
             "We agreed on Friday. Now let's discuss the garden. The roses need water."]
    ids = multipart(recording, texts)
    memory.store.complete(ids[0], texts[0], [{"text": texts[0], "start": 0, "end": 120}])
    memory.store.complete(ids[1], texts[1], [
        {"text": "We agreed on Friday.", "start": 0, "end": 30},
        {"text": "Now let's discuss the garden. The roses need water.", "start": 30, "end": 120},
    ])

    class Subjects:
        def __init__(self):
            self.calls, self.summaries = 0, []

        async def generate_json(self, task, evidence):
            self.calls += 1
            assert evidence == "\n".join(texts)
            return json.dumps({"title": "Launch review and garden", "sections": [
                {"title": "Launch review", "start_quote": "Project launch needs"},
                {"title": "Garden", "start_quote": "Now let's discuss the garden"},
            ]})

        async def summarize(self, sources, purpose):
            self.summaries.append(sources)
            return "\n".join(f"{source['text']} [{source['id']}]" for source in sources)

    memory.inference = Subjects()
    assert run(memory)
    sources = [client.get(f"/v1/transcripts/{key}").json() for key in ids]
    assert memory.inference.calls == 1
    assert sources[0]["sections"][0]["chapter_id"] == sources[1]["sections"][0]["chapter_id"]
    assert sources[1]["sections"][1]["start_offset"] == texts[1].index("Now let's")
    assert sources[1]["sections"][1]["start_seconds"] == 30
    for source in sources:
        assert "".join(source["text"][s["start_offset"]:s["end_offset"]]
                       for s in source["sections"]) == source["text"]
    navigation = client.get(f"/v1/recording-groups/{ids[0]}/sources", params={
        "day": "2026-10-16", "limit": 1,
    }).json()
    assert [chapter["title"] for chapter in navigation["chapters"]] == ["Launch review", "Garden"]
    assert [part["source_id"] for part in navigation["chapters"][0]["parts"]] == ids
    assert len(navigation["items"]) == 1  # Navigation covers chapters beyond the current page.
    inputs = topic_sources(sources)
    assert [source["subject_hint"] for source in inputs] == [
        "Launch review", "Launch review", "Garden",
    ]
    assert "".join(source["text"] for source in inputs) == "".join(texts)
    assert {source["id"] for source in inputs} == set(ids)
    recap = client.post("/v1/recaps/2026-10-16").json()
    assert recap["summary"]
    assert [source["subject_hint"] for source in memory.inference.summaries[0]] == [
        "Launch review", "Launch review", "Garden",
    ]
    assert memory.inference.calls == 1  # Recap reuses the existing chapter map.
    assert not run(memory)
    memory.store.correct_transcript(ids[1], texts[1].replace("Friday", "Tuesday"))
    assert all(client.get(f"/v1/transcripts/{key}").json()["sections"] == [] for key in ids)


def test_unfinished_multipart_recording_waits_for_all_parts(recording):
    _, memory, _ = recording
    multipart(recording, [TEXT], total=2)
    memory.inference = Model()
    assert not run(memory)
    assert memory.inference.calls == 0


def test_analysis_windows_keep_a_subject_even_when_its_title_changes(recording):
    client, memory, key = recording
    text = "\n".join(f"Launch review point {index}: keep discussing the project schedule."
                     for index in range(500))
    memory.store.correct_transcript(key, text)

    class ContinuingSubject:
        calls = 0

        async def generate_json(self, task, evidence):
            self.calls += 1
            if self.calls > 1:
                assert "previous_subject" in task and "previous_transcript_tail" in task
            return json.dumps({
                "title": f"Project review {self.calls}", "continues_previous": self.calls > 1,
                "sections": [{"title": f"Project review {self.calls}",
                              "start_quote": evidence[:100]}],
            })

    memory.inference = ContinuingSubject()
    assert run(memory)
    result = client.get(f"/v1/transcripts/{key}").json()
    assert len(result["sections"]) == 1
    assert result["sections"][0]["end_offset"] == len(text)
    assert memory.inference.calls > 1


@pytest.mark.parametrize("mutation", ["correct", "delete", "retime"])
def test_sibling_change_during_analysis_rejects_every_part(recording, mutation):
    client, memory, _ = recording
    ids = multipart(recording, [TEXT, "The same discussion continues in this audio part."])

    def change():
        if mutation == "correct":
            memory.store.correct_transcript(ids[1], "Corrected discussion")
        elif mutation == "delete":
            client.delete(f"/v1/audio/{ids[1]}")
        else:
            with memory.store.connect() as db:
                db.execute("UPDATE audio SET segments=? WHERE id=?", (json.dumps([
                    {"text": "Changed timing", "start": 0, "end": 10},
                ]), ids[1]))

    memory.inference = Model(during=change)
    assert run(memory)
    assert client.get(f"/v1/transcripts/{ids[0]}").json()["sections"] == []


def test_live_chunks_share_context_and_late_transcripts_refresh_the_chapters(recording):
    client, memory, _ = recording
    ids = multipart(recording, [TEXT, "The Roadster discussion continues here."])
    with memory.store.connect() as db:
        db.execute("UPDATE audio SET device_id='omi',source_kind='',source_metadata='{}'")
    memory.inference = Model()
    assert run(memory)
    first = client.get(f"/v1/transcripts/{ids[0]}").json()["sections"][-1]
    continued = client.get(f"/v1/transcripts/{ids[1]}").json()["sections"][0]
    assert first["chapter_id"] == continued["chapter_id"]
    data = memory.store.read_audio(ids[0])
    late = str(uuid4())
    memory.store.ingest(late, "omi", datetime(2026, 10, 16, 12, 4, tzinfo=UTC), data, 60)
    assert not run(memory)  # Do not analyze an incomplete incoming transcript.
    memory.store.complete(late, "Another thought about the Roadster.")
    assert run(memory)
    assert memory.inference.calls == 2
    assert client.get(f"/v1/transcripts/{late}").json()["sections"][0]["title"] == "Tesla Roadster"
    client.delete(f"/v1/audio/{ids[1]}")
    assert client.get(f"/v1/transcripts/{ids[0]}").json()["sections"] == []
    assert client.get(f"/v1/transcripts/{late}").json()["sections"] == []
