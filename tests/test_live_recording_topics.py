"""Topic analysis waits for live conversation boundaries and reuses completed work."""

import asyncio
import json
from datetime import timedelta

import pytest
from test_recaps import AT, insert, quiet
from test_recaps import client as client

from omi_local.recording_topics import process_topics
from omi_local.voice_activity import VERSION


class TopicModel:
    def __init__(self, during=None):
        self.calls = []
        self.during = during

    async def generate_json(self, task, evidence):
        self.calls.append(evidence)
        if self.during:
            self.during()
        return json.dumps({"title": "Plans", "sections": [
            {"title": "Plans", "start_quote": evidence[:100]},
        ]})


def test_live_topics_wait_for_recorded_pause_even_when_upload_is_old(client):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    insert(client, text="The Omi conversation is still in progress.")

    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=5)))
    assert memory.inference.calls == []


@pytest.mark.parametrize("noise", [False, True])
def test_completed_chunk_without_understandable_speech_finishes_topic_window(client, noise):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    key = insert(client, text="We decided to send the plans tomorrow.")
    if noise:
        # The detector can hear voice-like noise without a recognizable utterance.
        insert(client, AT + timedelta(minutes=1), "", duration=30, activity={
            "version": VERSION, "status": "observed", "speech": [[0, 30]],
            "non_speech": [], "method": "test_observation",
        })
    else:
        quiet(client, AT + timedelta(minutes=1), duration=30)

    assert asyncio.run(process_topics(memory, now=AT + timedelta(seconds=90)))
    assert len(memory.inference.calls) == 1
    assert memory.store.get_audio(key)["topic_details"] != "{}"
    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=2)))


def test_continuous_speech_uses_bounded_cached_windows_without_reprocessing_old_text(client):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    first = [insert(client, AT + timedelta(minutes=minute),
                    f"Discussion passage {minute} about the project.", duration=120)
             for minute in range(0, 16, 2)]

    assert asyncio.run(process_topics(memory, now=AT + timedelta(minutes=16)))
    assert len(memory.inference.calls) == 1
    completed = memory.store.get_audio(first[0])["topic_details"]
    assert "Discussion passage 14" not in memory.inference.calls[0]
    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=16)))

    later = insert(client, AT + timedelta(minutes=16), "The next part continues.", duration=120)
    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=18)))
    assert memory.store.get_audio(first[0])["topic_details"] == completed
    assert len(memory.inference.calls) == 1

    quiet(client, AT + timedelta(minutes=18), duration=30)
    assert asyncio.run(process_topics(memory, now=AT + timedelta(seconds=18 * 60 + 30)))
    assert len(memory.inference.calls) == 2
    assert "Discussion passage 0" not in memory.inference.calls[1]
    assert "Discussion passage 14" in memory.inference.calls[1]
    assert "The next part continues." in memory.inference.calls[1]
    assert memory.store.get_audio(later)["topic_details"] != "{}"
    assert not asyncio.run(process_topics(memory, now=AT + timedelta(hours=1)))


def test_missing_uploads_are_not_a_speech_break_but_the_maximum_still_applies(client):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    insert(client)
    quiet(client, AT + timedelta(minutes=3), duration=30)

    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=4)))
    assert asyncio.run(process_topics(memory, now=AT + timedelta(minutes=15)))
    assert len(memory.inference.calls) == 1


def test_topic_preparation_and_queue_counts_also_wait_for_a_speech_break(client, monkeypatch):
    from omi_local.recording_topics import ensure_topics

    now = AT + timedelta(minutes=5)
    monkeypatch.setattr("omi_local.recording_topics.utc_now", lambda: now)
    monkeypatch.setattr("omi_local.topic_context.utc_now", lambda: now)
    memory = client.app.state.memory
    memory.inference = TopicModel()
    key = insert(client)

    asyncio.run(ensure_topics(memory, {key}))
    assert memory.inference.calls == []
    assert client.get("/v1/status").json()["generation"] == {
        "queued": 0, "running": 0, "stages": {},
    }


def test_incoming_pending_transcription_prevents_a_premature_speech_break(client):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    insert(client)
    pending = insert(client, AT + timedelta(minutes=1), "The discussion continues.")
    with memory.store.connect() as db:
        db.execute("UPDATE audio SET state='queued',text=NULL WHERE id=?", (pending,))
    quiet(client, AT + timedelta(minutes=2), duration=30)

    assert not asyncio.run(process_topics(memory, now=AT + timedelta(seconds=150)))
    memory.store.complete(pending, "The discussion continues.")
    assert asyncio.run(process_topics(memory, now=AT + timedelta(seconds=150)))
    assert "The discussion continues." in memory.inference.calls[0]


def test_late_overlapping_speech_during_analysis_rejects_the_apparent_pause(client):
    memory = client.app.state.memory
    key = insert(client)
    quiet(client, AT + timedelta(minutes=1), duration=30)
    memory.inference = TopicModel(during=lambda: insert(
        client, AT + timedelta(seconds=65), "A delayed spoken passage.", duration=20,
    ))

    assert asyncio.run(process_topics(memory, now=AT + timedelta(seconds=90)))
    assert memory.store.get_audio(key)["topic_details"] == "{}"


def test_speech_resuming_after_a_pause_does_not_invalidate_the_completed_window(client):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    key = insert(client)
    quiet(client, AT + timedelta(minutes=1), duration=30)
    assert asyncio.run(process_topics(memory, now=AT + timedelta(seconds=90)))
    completed = memory.store.get_audio(key)["topic_details"]

    insert(client, AT + timedelta(seconds=90), "Now we discuss a different plan.")
    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=3)))
    assert len(memory.inference.calls) == 1
    assert memory.store.get_audio(key)["topic_details"] == completed


def test_correcting_a_pause_invalidates_its_dependent_topic_window(client):
    memory = client.app.state.memory
    memory.inference = TopicModel()
    key = insert(client)
    pause = quiet(client, AT + timedelta(minutes=1), duration=30)
    assert asyncio.run(process_topics(memory, now=AT + timedelta(seconds=90)))
    memory.store.correct_transcript(pause, "The voice was understandable after correction.")

    assert memory.store.get_audio(key)["topic_details"] == "{}"
    assert not asyncio.run(process_topics(memory, now=AT + timedelta(minutes=3)))
