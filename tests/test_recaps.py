"""Daily recaps use completed conversations, independently of audio retention."""

import io
import json
import sys
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.inference import Inference
from omi_local.recaps import _daily_recap_purpose, topic_sources
from omi_local.settings import Settings
from omi_local.summary_templates import SummaryTemplate
from omi_local.voice_activity import VERSION
from omi_local.worker import scheduled_recaps

AT = datetime(2026, 10, 16, 12, tzinfo=UTC)
ORDERED_SPEECH_MARKER = (
    "Ordered speech pairs [owner, exact_text] (join the text values in order to reconstruct "
    "this exact transcript window. The JSON speaker legend maps each alias to its source-local "
    "speaker UUID reference and any reviewed name. The alias and UUID do not establish a "
    "person's identity or carry across recordings. `unknown` and `overlap` have no single owner): "
)


def ordered_speech_payloads(prompt):
    decoder, payloads, cursor = json.JSONDecoder(), [], 0
    while (start := prompt.find(ORDERED_SPEECH_MARKER, cursor)) >= 0:
        payload_start = start + len(ORDERED_SPEECH_MARKER)
        payload, consumed = decoder.raw_decode(prompt[payload_start:])
        payloads.append(payload)
        cursor = payload_start + consumed
    return payloads


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

    async def review_summary(self, sources, draft, **kwargs):
        return draft, {"model_calls": 0, "correction_count": 0,
                       "checked_source_ids": [source["id"] for source in sources]}

    async def summarize_day_overview(self, sources, purpose):
        return await self.summarize(sources, purpose)


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


def test_recording_summary_receives_current_speaker_grounded_transcript(client, monkeypatch):
    transcript = "Our current work is Atlas. We could propose Beacon."
    boundary = len("Our current work is Atlas.")
    key = insert(
        client, kind="manual_upload",
        text=transcript,
    )
    memory = client.app.state.memory
    projection = {
        "state": "ready", "version": "speaker-rev-1", "evidence_version": "speaker-rev-1",
        "speakers": [{"id": "speaker-1", "label": "A", "name": None}],
        "utterances": [
                {"source_id": key, "start": 0.0, "end": 0.8, "text": transcript[:boundary],
                 "start_offset": 0, "end_offset": boundary, "attribution": "attributed",
             "speaker_id": "speaker-1", "speaker_label": "A", "speaker_name": None},
                {"source_id": key, "start": 0.8, "end": 1.5, "text": transcript[boundary:],
                 "start_offset": boundary, "end_offset": len(transcript), "attribution": "unknown",
             "speaker_id": None, "speaker_label": None, "speaker_name": None},
        ],
    }
    monkeypatch.setattr(
        memory.recaps, "speaker_projection", lambda group, db=None: {key: projection},
    )
    client.post("/v1/recaps/2026-10-16")
    prepared_sources, purpose = memory.inference.calls[0]
    assert prepared_sources[0].get("speaker_evidence") == projection["utterances"]
    assert "aliases such as speaker_1 are anonymous clusters local to one source" in purpose.lower()
    assert "do not repeat unintelligible names" in purpose.lower()
    assert "proposed" in purpose.lower() and "current project" in purpose.lower()
    assert "preserve names and roles mentioned in speech as third parties" in purpose.lower()
    assert "map them to an anonymous voice" in purpose.lower()
    daily_purpose = memory.inference.calls[1][1]
    assert (
        "do not infer a speaker's identity or broader ownership from a label or first-person "
        "wording" in daily_purpose.lower()
    )
    assert "keep options tentative until explicitly decided" in daily_purpose.lower()
    assert "from transcribed recording notes" in daily_purpose.lower()
    assert "use a reviewed speaker name only to identify that speaker" in daily_purpose.lower()
    assert "preserve names and roles mentioned in speech as third parties" in daily_purpose.lower()
    assert (
        "source connects them to the same project, plan, goal or workflow"
        in daily_purpose.lower()
    )


def test_daily_purpose_uses_original_transcript_for_typed_full_recording():
    day = AT.date()
    purpose = _daily_recap_purpose(day, [{
        "id": str(uuid4()), "reference_type": "recording_transcription",
    }]).lower()
    assert "from the complete original transcript" in purpose
    assert "from transcribed recording notes" not in purpose
    assert "2026-10-16" not in purpose
    assert "do not infer or repair a spoken date or year" in purpose
    assert "current role, team context" in purpose
    assert "a project mention alone does not make an independent recap subject" in purpose
    assert "do not turn garbled names or labels into confident subject headings" in purpose


def test_meeting_purpose_allows_consequential_detail_without_changing_diary_style():
    day = AT.date()
    sources = [{"id": str(uuid4()), "text": "Meeting transcript"}]
    diary = _daily_recap_purpose(day, sources).lower()
    meeting = _daily_recap_purpose(
        day, sources, SummaryTemplate(template="meeting"),
    ).lower()

    assert "a few focused sentences" in diary
    assert "do not impose a short sentence quota" not in diary
    assert "do not impose a short sentence quota or fixed section count" in meeting
    assert "## subject headings" in diary and "## subject headings" in meeting
    assert "scope and constraints, concrete examples and quantities" in meeting
    assert "approaches or options, resourcing and timing" in meeting
    assert "conditional follow-ups when present" in meeting
    assert "unanswered question or speculation is unresolved" in meeting
    assert "do not fill in missing units, dates or years" in meeting


def test_completed_conversation_notes_get_one_full_original_coherence_edit(client, monkeypatch):
    original = (
        "The proposed workflow is still being evaluated and depends on approval. "
        "If approved, the next step is to collect owner input by Friday."
    )
    key = insert(client, kind="manual_upload", text=original)
    memory = client.app.state.memory
    reviews = []

    async def cohere(sources, draft, purpose):
        reviews.append((sources, draft, purpose))
        return (
            f"## Workflow\nThe proposal remains under evaluation, subject to approval [{key}].\n\n"
            f"If approved, collect owner input by Friday [{key}]",
            {"status": "applied", "model_calls": 1, "input_tokens_estimate": 800},
        )

    monkeypatch.setattr(memory.inference, "cohere_completed_summary", cohere, raising=False)
    result = client.post("/v1/recaps/2026-10-16").json()
    assert len(reviews) == 1
    assert reviews[0][0][0]["text"] == original
    assert key in reviews[0][1]
    assert "preserve original source UUID citations" in reviews[0][2]
    assert "If approved, collect owner input by Friday" in result["summary"]
    review_trace = result["evidence"]["pipeline"]["conversation_coherence"]
    assert review_trace["model_calls"] == 1
    assert review_trace["reviews"] == [{
        "status": "applied", "model_calls": 1, "input_tokens_estimate": 800,
    }]


def test_speaker_evidence_revision_invalidates_notes_and_rejects_stale_generation(
    client, monkeypatch,
):
    key = insert(client, kind="manual_upload", text="I work on Atlas; we could propose Beacon.")
    memory = client.app.state.memory
    version = ["speaker-rev-1"]
    projection = {
        "state": "ready", "version": version[0], "evidence_version": version[0],
        "speakers": [{"id": "speaker-1", "label": "A", "name": None}],
        "utterances": [{
            "source_id": key, "start": 0.0, "end": 1.0, "text": "I work on Atlas;",
            "start_offset": 0, "end_offset": 16, "attribution": "attributed",
            "speaker_id": "speaker-1", "speaker_label": "A", "speaker_name": None,
        }],
    }
    monkeypatch.setattr(
        memory.recaps, "speaker_projection",
        lambda group, db=None: {key: projection | {
            "version": version[0], "evidence_version": version[0],
        }},
    )
    group = memory.recaps.groups(AT + timedelta(hours=1))[0]
    before = memory.recaps.group_key(group)
    version[0] = "speaker-rev-2"
    assert memory.recaps.group_key(group) != before

    class SpeakerEditsDuringGeneration(FakeInference):
        async def summarize(self, sources, purpose):
            result = await super().summarize(sources, purpose)
            version[0] = "speaker-rev-3"
            return result

    memory.inference = SpeakerEditsDuringGeneration()
    response = client.post("/v1/recaps/2026-10-16")
    assert response.status_code == 409


def test_recap_fingerprint_uses_caller_snapshot_and_timing_only_changes(client, monkeypatch):
    key = insert(client, kind="manual_upload", text="Atlas continues")
    memory = client.app.state.memory
    group = memory.recaps.groups(AT + timedelta(hours=1))[0]
    timing = [0.0]

    def projection(current_group, db=None):
        assert db is not None
        return {key: {
            "state": "ready", "evidence_version": "same-speaker-version",
            "utterances": [{"source_id": key, "start": timing[0], "end": timing[0] + 1,
                            "text": "Atlas continues", "start_offset": 0, "end_offset": 16,
                            "attribution": "attributed", "speaker_label": "A"}],
        }}

    seen = []
    monkeypatch.setattr(memory.recaps, "speaker_projection", projection)
    original = memory.recaps.group_key

    def capture(current_group, db=None):
        seen.append(db)
        return original(current_group, db)

    monkeypatch.setattr(memory.recaps, "group_key", capture)
    with memory.store.connect() as db:
        db.execute("BEGIN")
        memory.recaps.day_key([group], day=AT.date(), db=db)
        assert seen and all(item is db for item in seen)
        first = original(group, db)
        timing[0] = 0.25
        assert original(group, db) != first


def test_recap_preparation_fingerprint_tracks_summary_thinking_budget(client):
    insert(client, kind="manual_upload", text="Atlas continues")
    memory = client.app.state.memory
    group = memory.recaps.groups(AT + timedelta(hours=1))[0]
    before = memory.recaps.group_key(group)
    memory.settings = memory.settings.model_copy(update={
        "llm_summary_thinking_token_budget": 8192,
    })
    assert memory.recaps.group_key(group) != before


def test_recap_fingerprint_uses_effective_summary_thinking_setting(client):
    insert(client, kind="manual_upload", text="Atlas continues")
    memory = client.app.state.memory
    group = memory.recaps.groups(AT + timedelta(hours=1))[0]
    memory.settings = memory.settings.model_copy(update={
        "llm_enable_thinking": False, "llm_summary_enable_thinking": None,
    })
    inherited_off = memory.recaps.group_key(group)
    memory.settings = memory.settings.model_copy(update={"llm_summary_enable_thinking": True})
    summary_on = memory.recaps.group_key(group)
    assert summary_on != inherited_off
    memory.settings = memory.settings.model_copy(update={
        "llm_enable_thinking": True, "llm_summary_enable_thinking": True,
    })
    assert memory.recaps.group_key(group) == summary_on


def test_daily_recap_cache_identity_uses_effective_summary_thinking(client, monkeypatch):
    insert(client, kind="manual_upload", text="Atlas continues")
    memory = client.app.state.memory
    keys = []
    original = memory.store.recap_summary

    def capture(key, *args, **kwargs):
        keys.append(key)
        return original(key, *args, **kwargs)

    monkeypatch.setattr(memory.store, "recap_summary", capture)
    memory.settings = memory.settings.model_copy(update={
        "llm_enable_thinking": False, "llm_summary_enable_thinking": None,
    })
    client.post("/v1/recaps/2026-10-16")
    first_key = keys[-1]
    memory.settings = memory.settings.model_copy(update={"llm_summary_enable_thinking": True})
    client.post("/v1/recaps/2026-10-16")
    assert keys[-1] != first_key


def test_speaker_projection_resolves_one_scope_for_all_source_parts(client, monkeypatch):
    first = insert(client, AT, "Part one", kind="manual_upload")
    second = insert(client, AT + timedelta(seconds=60), "Part two", kind="manual_upload")
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute(
            "UPDATE audio SET source_id='same-file.wav',source_kind='manual_upload' "
            "WHERE id IN (?,?)", (first, second),
        )
    rows = store.get_audio_many([first, second])
    group = type("Group", (), {"sources": [rows[first], rows[second]]})()
    speakers = client.app.state.memory.speakers
    resolve = speakers.resolve
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[1])
        return resolve(*args, **kwargs)

    monkeypatch.setattr(speakers, "resolve", counted)
    projected = client.app.state.memory.recaps.speaker_projection(group)
    assert set(projected) == {first, second}
    assert calls == [first]


def test_speaker_projection_only_returns_source_keys_for_multiple_scopes(client, monkeypatch):
    first = insert(client, text="Part one")
    second = insert(client, AT + timedelta(minutes=7), "Part two")
    memory = client.app.state.memory
    rows = memory.store.get_audio_many([first, second])
    group = SimpleNamespace(sources=[rows[first], rows[second]])
    scopes = {
        key: {"key": f"source:{key}", "rows": [rows[key]]}
        for key in (first, second)
    }
    monkeypatch.setattr(
        memory.speakers, "resolve", lambda _kind, key, *_args, **_kwargs: scopes[key],
    )
    monkeypatch.setattr(memory.speakers, "load", lambda *_args: None)
    monkeypatch.setattr(
        "omi_local.recaps.project_utterances",
        lambda _turns, scope_rows, _ready, _speakers: [{
            "source_id": scope_rows[0]["id"], "text": scope_rows[0]["text"],
            "attribution": "unknown",
        }],
    )
    projected = memory.recaps.speaker_projection(group)
    assert set(projected) == {first, second}


@pytest.mark.anyio
async def test_summary_transport_clips_speaker_spans_to_topic_and_8k_windows(tmp_path):
    key = str(uuid4())
    left, right = "ALPHA repeated ", "BETA repeated "
    text = left + right
    split = len(left)
    speaker_a, speaker_b = str(uuid4()), str(uuid4())
    source = {
        "id": key, "started_at": stamp(AT), "source_kind": "manual_upload", "text": text,
        "sections": [
            {"title": "Alpha", "start_offset": 0, "end_offset": split},
            {"title": "Beta", "start_offset": split, "end_offset": len(text)},
        ],
        "speaker_evidence": [
            {"source_id": key, "text": left, "start_offset": 0, "end_offset": split,
             "start": 0.0, "end": 1.0, "speaker_id": speaker_a,
             "speaker_label": "A", "attribution": "attributed"},
            {"source_id": key, "text": right, "start_offset": split, "end_offset": len(text),
             "start": 1.0, "end": 2.0, "speaker_id": speaker_b,
             "speaker_label": "B", "attribution": "attributed"},
        ],
    }
    topic_inputs = topic_sources([source])
    assert [item["speaker_evidence"][0]["speaker_label"] for item in topic_inputs] == ["A", "B"]
    assert topic_inputs[0]["canonical_text"] == text

    long_text = "x" * 7998 + "repeated" + " y" * 5000 + " repeated"
    first_end = 7998 + len("repeated")
    later_start = long_text.rfind("repeated")
    long_speaker_a, long_speaker_b = str(uuid4()), str(uuid4())
    long_source = {
        "id": key, "started_at": stamp(AT), "source_kind": "manual_upload", "text": long_text,
        "speaker_evidence": [
            {"source_id": key, "text": "repeated", "start_offset": 7998,
             "end_offset": first_end, "start": 2.0, "end": 2.4, "speaker_label": "A",
             "speaker_id": long_speaker_a, "attribution": "attributed"},
            {"source_id": key, "text": "repeated", "start_offset": later_start,
             "end_offset": later_start + len("repeated"), "start": 5.0, "end": 5.4,
             "speaker_id": long_speaker_b, "speaker_label": "B", "attribution": "attributed"},
        ],
    }
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload["evidence"])
        return httpx.Response(200, json={"choices": [{"message": {"content": f"Notes [{key}]"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)
        inference = Inference(settings, http)
        await inference.summarize(topic_inputs, "topic notes")
        topic_payloads = [
            payload for evidence in calls for payload in ordered_speech_payloads(evidence)
        ]
        alpha = next(payload for payload in topic_payloads
                     if "".join(item[1] for item in payload["speech"]) == left)
        beta = next(payload for payload in topic_payloads
                    if "".join(item[1] for item in payload["speech"]) == right)
        assert alpha["speakers"]["speaker_1"]["id"] == speaker_a
        assert beta["speakers"]["speaker_2"]["id"] == speaker_b
        calls.clear()
        await inference.summarize([long_source], "faithful notes")
    assert calls
    long_payloads = [
        payload for evidence in calls for payload in ordered_speech_payloads(evidence)
    ]
    assert "".join(
        block[1] for payload in long_payloads for block in payload["speech"]
    ) == long_text
    legends = [speaker for payload in long_payloads for speaker in payload["speakers"].values()]
    assert {speaker["id"] for speaker in legends} == {long_speaker_b}
    assert all("label" not in speaker for speaker in legends)
    assert any(block[0] == "unknown"
               for payload in long_payloads for block in payload["speech"])
    assert all("start_offset" not in evidence and '"start"' not in evidence
               for evidence in calls)
    assert any("Ordered speech pairs" in evidence for evidence in calls)


@pytest.mark.anyio
async def test_unknown_speaker_spans_keep_summary_filtering_and_avoid_duplicate_transcript(
    tmp_path,
):
    key = str(uuid4())
    text = "um, we booked the ticket for Friday."
    captured = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        captured.append(payload["evidence"])
        return httpx.Response(200, json={"choices": [{"message": {"content": f"Booked [{key}]"}}]})

    source = {
        "id": key, "started_at": stamp(AT), "text": text, "source_kind": "manual_upload",
        "speaker_evidence": [{
            "source_id": key, "text": text, "start_offset": 0, "end_offset": len(text),
            "start": None, "end": None, "speaker_id": None, "speaker_label": None,
            "speaker_name": None, "attribution": "unknown",
        }],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await Inference(Settings(data_dir=tmp_path, summary_language="en", _env_file=None), http
                        ).summarize([source], "faithful notes")
    assert captured
    assert "um," not in captured[0]
    assert "we booked the ticket for Friday." in captured[0]
    assert "Ordered speech pairs" not in captured[0]


def test_later_context_resynthesizes_all_earlier_topics_without_reusing_the_old_recap(client):
    travel = insert(client, AT - timedelta(hours=2), "Compare train routes", "manual_upload")
    hotel = insert(client, AT - timedelta(hours=1), "Reserve the hotel", "manual_upload")
    link = None

    class EvolvingDay(FakeInference):
        async def summarize(self, sources, purpose):
            if not purpose.startswith("a readable diary recap"):
                return await super().summarize(sources, purpose)
            self.calls.append((sources, purpose))
            texts = "\n".join(source["text"] for source in sources)
            assert "Compare train routes" in texts and "Reserve the hotel" in texts
            # Saved recap wording is navigation, not evidence for the next generation.
            assert "Earlier travel summary" not in texts
            assert "Earlier hotel summary" not in texts
            if link is None:
                return (f"## Travel options\nEarlier travel summary [{travel}].\n"
                        f"## Hotel booking\nEarlier hotel summary [{hotel}].")
            assert "Both bookings are for our Lisbon trip" in texts
            assert {source["id"] for source in sources} == {travel, hotel, link}
            return (f"## Weekend trip\nCompare train routes [{travel}] and reserve the hotel "
                    f"[{hotel}] for the same Lisbon trip [{link}].")

    memory = client.app.state.memory
    memory.inference = EvolvingDay()
    initial = client.post("/v1/recaps/2026-10-16").json()
    assert initial["summary"].count("## ") == 2
    link = insert(client, text="Both bookings are for our Lisbon trip", kind="manual_upload")
    assert client.get("/v1/recaps/2026-10-16").json()["stale"]
    updated = client.post("/v1/recaps/2026-10-16").json()
    assert updated["output_id"] != initial["output_id"]
    assert updated["summary"].count("## ") == 1
    assert {source["id"] for source in updated["sources"]} == {travel, hotel, link}
    assert all(f"[{key}]" in updated["summary"] for key in (travel, hotel, link))
    # Two original preparations, first synthesis, one new preparation, full-day synthesis.
    assert len(memory.inference.calls) == 5
    versions = client.get("/v1/diary/2026-10-16/versions").json()["items"]
    assert any(item["content"]["summary"] == initial["summary"] for item in versions)


def test_daily_publication_preserves_all_distinct_model_topics_without_truncating(client):
    key = insert(client, kind="manual_upload")

    class VariedDay(FakeInference):
        async def summarize(self, sources, purpose):
            if not purpose.startswith("a readable diary recap"):
                return await super().summarize(sources, purpose)
            return "\n\n".join(f"## Subject {i}\nUseful detail {i} [{key}]."
                               for i in range(1, 13))

    client.app.state.memory.inference = VariedDay()
    recap = client.post("/v1/recaps/2026-10-16").json()
    assert recap["summary"].count("## ") == 12
    assert f"Useful detail 12 [{key}]" in recap["summary"]
    assert not recap["stale"]


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
    assert "Preserve useful details once" in memory.inference.calls[-1][1]


@pytest.mark.parametrize("version,expected_calls", [
    ("DAY_PROMPT_VERSION", 3), ("PROMPT_VERSION", 4),
])
def test_prompt_upgrade_refreshes_saved_recap(client, monkeypatch, version, expected_calls):
    import asyncio

    import omi_local.recaps as recaps

    monkeypatch.setattr("omi_local.worker.utc_now", lambda: AT + timedelta(hours=1))
    insert(client, text="Milk frother. Omi recording. Roadster discussion.", kind="manual_upload")
    memory = client.app.state.memory
    asyncio.run(scheduled_recaps(memory))
    previous = client.get("/v1/recaps/2026-10-16").json()
    assert len(memory.inference.calls) == 2

    monkeypatch.setattr(recaps, version, "next-topic-format")
    assert client.get("/v1/recaps/2026-10-16").json()["stale"]
    asyncio.run(scheduled_recaps(memory))
    updated = client.get("/v1/recaps/2026-10-16").json()
    assert updated["output_id"] != previous["output_id"]
    assert not updated["stale"]
    # Conversation prompt changes must rewrite notes as well as the daily recap.
    assert len(memory.inference.calls) == expected_calls
    purpose = memory.inference.calls[-1][1]
    assert "no target, minimum or maximum number of topics" in purpose
    assert "one coherent summary per topic" in purpose
    assert "group details only when the source connects them" in purpose.lower()
    assert "a project mention alone does not make an independent recap subject" in purpose.lower()
    assert "use a reviewed speaker name only to identify that speaker" in purpose.lower()
    asyncio.run(scheduled_recaps(memory))
    assert len(memory.inference.calls) == expected_calls


@pytest.mark.parametrize("elapsed,ready", [(329, False), (330, True)])
def test_live_recap_waits_for_five_minutes_of_observed_quiet_and_settling(
    client, monkeypatch, elapsed, ready,
):
    first = insert(client)
    close_after(client, AT + timedelta(minutes=1))
    now = AT + timedelta(minutes=1, seconds=elapsed)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: now)
    result = client.post("/v1/recaps/2026-10-16").json()
    if ready:
        assert result["summary"] and first in result["summary"]
    else:
        assert result["summary"] is None
        assert client.app.state.memory.inference.calls == []
        assert result["waiting_for_pause"] == 1


def test_open_meeting_transcripts_wait_for_a_pause(client):
    meeting = str(uuid4())
    assert client.post("/v1/events", json={
        "event_id": meeting, "device_id": "omi", "occurred_at": stamp(AT),
        "action": "meeting_toggle",
    }).status_code == 200
    key = insert(client, AT + timedelta(minutes=1), "Send the proposal on Friday.")
    assert client.post("/v1/recaps/2026-10-16").json()["summary"] is None
    close_after(client, AT + timedelta(minutes=2))
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["summary"] and key in result["summary"]
    assert result["deferred_sources"] == 0
    assert client.get("/v1/timeline").json()["active_meeting"]["id"] == meeting
    assert result["sources"][0]["meeting_hint_ids"] == [meeting]


def test_new_upload_arrivals_during_generation_publish_a_snapshot_then_refresh(client):
    first = insert(client, kind="manual_upload")
    memory = client.app.state.memory
    added = []

    class LateSpeech(FakeInference):
        async def summarize(self, sources, purpose):
            if not added:
                added.append(insert(
                    client, AT + timedelta(minutes=3), "Actually, let us reconsider",
                    duration=120, kind="manual_upload",
                ))
            return await super().summarize(sources, purpose)

    memory.inference = LateSpeech()
    initial = client.post("/v1/recaps/2026-10-16").json()
    assert initial["summary"] and first in initial["summary"]
    assert added[0] not in initial["summary"] and initial["stale"]
    refreshed = client.post("/v1/recaps/2026-10-16").json()
    assert first in refreshed["summary"] and added[0] in refreshed["summary"]
    assert not refreshed["stale"]


def test_late_live_transcript_keeps_the_recap_visible_until_it_updates(client):
    first = insert(client)
    close_after(client, AT + timedelta(minutes=1))
    original = client.post("/v1/recaps/2026-10-16").json()
    second = insert(client, AT + timedelta(minutes=15), "Reconsider the plan", duration=120)
    current = client.get("/v1/recaps/2026-10-16").json()
    assert current["summary"] == original["summary"] and not current["stale"]
    assert client.get("/v1/diary").json()["items"][0]["summary"]
    assert first in client.get("/v1/diary/2026-10-16.md").text
    close_after(client, AT + timedelta(minutes=17))
    assert client.get("/v1/recaps/2026-10-16").json()["stale"]
    updated = client.post("/v1/recaps/2026-10-16").json()
    assert first in updated["summary"] and second in updated["summary"]
    assert not client.get("/v1/recaps/2026-10-16").json()["stale"]



def test_live_pause_is_fixed_at_five_minutes_and_uploaded_files_do_not_wait(client, monkeypatch):
    now = AT + timedelta(minutes=2, seconds=30)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: now)
    uploaded = insert(client, AT - timedelta(hours=1), kind="manual_upload")
    live = insert(client)
    quiet(client, AT + timedelta(minutes=1), duration=60)
    initial = client.post("/v1/recaps/2026-10-16").json()
    assert {source["id"] for source in initial["sources"]} == {uploaded}
    assert initial["waiting_for_pause"] == 1
    assert client.patch("/v1/settings", json={"recap_quiet_minutes": 1}).status_code == 200
    refreshed = client.post("/v1/recaps/2026-10-16").json()
    assert {source["id"] for source in refreshed["sources"]} == {uploaded}
    assert refreshed["waiting_for_pause"] == 1
    quiet(client, AT + timedelta(minutes=2), duration=120)
    quiet(client, AT + timedelta(minutes=4), duration=120)
    now = AT + timedelta(minutes=6, seconds=30)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: now)
    ready = client.post("/v1/recaps/2026-10-16").json()
    assert {source["id"] for source in ready["sources"]} == {uploaded, live}
    assert ready["waiting_for_pause"] == 0


def test_resumed_speech_restarts_the_live_pause_requirement(client, monkeypatch):
    first = insert(client)
    close_after(client, AT + timedelta(minutes=1))
    second = insert(client, AT + timedelta(minutes=3), "There is another point to discuss")
    now = AT + timedelta(minutes=8, seconds=30)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: now)
    pending = client.post("/v1/recaps/2026-10-16").json()
    assert pending["summary"] is None
    assert client.app.state.memory.inference.calls == []
    quiet(client, AT + timedelta(minutes=7), duration=120)
    now = AT + timedelta(minutes=9, seconds=30)
    ready = client.post("/v1/recaps/2026-10-16").json()
    assert {source["id"] for source in ready["sources"]} == {first, second}


def test_repeated_long_excerpt_keeps_all_sources_without_regrouping_identical_information(client):
    text = ('The deployment needs a review on Friday before invitations can be sent. '
            'Payment checks remain a dependency and the owner must report any failed checks. ') * 8
    first = insert(client, text=text, kind='manual_upload')
    before = client.post('/v1/recaps/2026-10-16').json()
    memory = client.app.state.memory
    calls = len(memory.inference.calls)
    duplicate = insert(client, AT + timedelta(minutes=20), text=text, kind='manual_upload')
    after = client.post('/v1/recaps/2026-10-16').json()
    assert after['summary'] == before['summary']
    assert {source['id'] for source in after['sources']} == {first, duplicate}
    assert len(memory.inference.calls) == calls
    assert after['evidence']['pipeline']['exact_repeated_excerpt_ids'] == [duplicate]
    assert after['evidence']['pipeline']['daily_synthesis']['cache_hit'] == 'identical_unique_notes'
    assert memory.store.get_audio(duplicate)['text'] == text
