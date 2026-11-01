"""A failed later model call must not repeat completed topic-analysis parts."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_recording_topics import recording as recording

from omi_local.domain import utc_now
from omi_local.recording_topics import input_windows, process_topics


class InterruptedModel:
    def __init__(self, fail=True):
        self.calls = []
        self.fail = fail

    async def generate_json(self, task, evidence):
        self.calls.append(evidence)
        if self.fail and len(self.calls) == 2:
            raise RuntimeError("A later model request failed")
        return json.dumps({"title": "Project planning", "sections": [
            {"title": "Project planning", "start_quote": evidence[:100]},
        ]})


def long_transcript():
    return "\n".join(f"Planning line {index}: review the project schedule." for index in range(800))


def test_failed_topic_analysis_resumes_without_sending_completed_parts_again(recording):
    client, memory, key = recording
    text = long_transcript()
    memory.store.correct_transcript(key, text)
    memory.inference = InterruptedModel()
    now = utc_now()

    assert asyncio.run(process_topics(memory, now=now))
    assert len(memory.inference.calls) == 2
    assert not asyncio.run(process_topics(memory, now=now + timedelta(seconds=30)))
    assert asyncio.run(process_topics(memory, now=now + timedelta(minutes=2)))
    assert memory.inference.calls[2] == memory.inference.calls[1]
    assert memory.inference.calls.count(memory.inference.calls[0]) == 1
    assert len(memory.inference.calls) == len(list(input_windows(text))) + 1
    source = client.get(f"/v1/transcripts/{key}").json()
    assert source["title"] == "Project planning"
    assert source["sections"][0]["start_offset"] == 0
    assert source["sections"][-1]["end_offset"] == len(text)
    assert not asyncio.run(process_topics(memory, now=now + timedelta(minutes=3)))


@pytest.mark.parametrize("change", ["transcript", "language"])
def test_changed_evidence_or_model_policy_does_not_reuse_partial_topic_results(recording, change):
    _, memory, key = recording
    text = long_transcript()
    memory.store.correct_transcript(key, text)
    memory.inference = InterruptedModel()
    now = utc_now()
    assert asyncio.run(process_topics(memory, now=now))

    if change == "transcript":
        memory.store.correct_transcript(key, "Corrected project plan.\n" + text)
    else:
        memory.settings.summary_language = "da"
    assert asyncio.run(process_topics(memory, now=now + timedelta(minutes=2)))
    assert memory.inference.calls[2].startswith(
        "Corrected project plan." if change == "transcript" else "Planning line 0:"
    )


def test_retry_delay_starts_after_a_long_failed_model_call(recording, monkeypatch):
    _, memory, key = recording
    memory.store.correct_transcript(key, long_transcript())
    started = utc_now()
    clock = [started]
    monkeypatch.setattr("omi_local.recording_topics.utc_now", lambda: clock[0])

    class SlowFailure(InterruptedModel):
        async def generate_json(self, task, evidence):
            if len(self.calls) == 1:
                clock[0] = started + timedelta(minutes=5)
            return await super().generate_json(task, evidence)

    memory.inference = SlowFailure()
    assert asyncio.run(process_topics(memory, now=started))
    assert not asyncio.run(process_topics(memory, now=started + timedelta(minutes=5, seconds=30)))
    assert len(memory.inference.calls) == 2
    assert asyncio.run(process_topics(memory, now=started + timedelta(minutes=6, seconds=1)))


@pytest.mark.parametrize("interruption", ["failure", "shutdown"])
def test_failed_topic_parts_survive_a_backend_restart(tmp_path, interruption):
    from fastapi.testclient import TestClient
    from test_recaps import AT, wav

    from omi_local.api import create_app
    from omi_local.settings import Settings

    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    text, now = long_transcript(), utc_now()
    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        key = str(uuid4())
        memory.store.ingest(key, "web", AT, wav(), 1)
        memory.store.complete(key, text)
        class StoppingModel(InterruptedModel):
            async def generate_json(self, task, evidence):
                if interruption == "shutdown" and len(self.calls) == 1:
                    raise asyncio.CancelledError
                return await super().generate_json(task, evidence)

        memory.inference = StoppingModel()
        if interruption == "shutdown":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(process_topics(memory, now=now))
        else:
            assert asyncio.run(process_topics(memory, now=now))
        assert client.get(f"/v1/transcripts/{key}").json()["title"] is None

    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        model = InterruptedModel(fail=False)
        memory.inference = model
        assert asyncio.run(process_topics(memory, now=now + timedelta(minutes=2)))
        assert model.calls[0] == list(input_windows(text))[1][1]
        assert len(model.calls) == len(list(input_windows(text))) - 1
