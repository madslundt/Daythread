"""Completeness provenance composes with committed retrieval and archive workflows."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_conversation_prep import Model as PrepModel
from test_conversation_prep import body as prep_body
from test_decisions import create as create_decision
from test_extended import FakeInference, audio_data
from test_inbox import listing, proposal, run
from test_semantic import drain, indexed_memory
from test_semantic import insert as semantic_insert
from test_weekly import FakeInference as WeeklyModel

from omi_local.api import create_app
from omi_local.inbox_store import InboxStore
from omi_local.settings import Settings

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
WEEK = date(2026, 10, 12)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_actual_hybrid_and_diary_inputs_match_saved_evidence_and_stable_chat_ids(tmp_path):
    config = Settings(
        data_dir=tmp_path,
        timezone="UTC",
        worker_enabled=False,
        embedding_enabled=True,
        embedding_base_url="http://embeddings.local/v1",
        embedding_model="fictional-model",
        embedding_revision="fixture",
        embedding_dimension=2,
        _env_file=None,
    )
    memory, http = await indexed_memory(config)

    class CapturingModel(FakeInference):
        supplied = None

        async def chat(self, question, sources, history=None):
            self.supplied = sources
            return await super().chat(question, sources, history)

    model = CapturingModel()
    memory.inference = model
    try:
        key = semantic_insert(memory.store, "Søren betaler fakturaen.")
        await drain(memory)
        await memory.recap(date(2026, 10, 15))
        result = await memory.chat("Who handles billing?", 2, day=date(2026, 10, 15))
        assert result["retrieval"] == result["evidence"]["retrieval_mode"] == "hybrid"
        assert result["evidence"]["retrieval_details"] == result["retrieval_details"]
        assert (
            {s["id"] for s in model.supplied}
            == {s["id"] for s in result["evidence"]["selected"]}
            == {key}
        )
        assert any(s.get("diary_summaries") for s in model.supplied)
        for supplied, selected in zip(model.supplied, result["evidence"]["selected"], strict=True):
            assert selected["transcript_version"] == supplied["transcript_version"]
            assert selected["supplied_ranges"] == [
                {k: m[k] for k in ("start_offset", "end_offset")} for m in supplied["matches"]
            ]
        persisted = memory.store.evidence(result["output_id"])
        assert persisted["retrieval_mode"] == "hybrid"
        first = memory.store.chat_session(result["session_id"])["messages"]
        assert first[0]["id"] == result["user_message_id"]
        assert first[1]["id"] == result["message_id"] == result["output_id"]
        await memory.chat("Who handles billing?", 2, session_id=result["session_id"])
        messages = memory.store.chat_session(result["session_id"])["messages"]
        assert [m["id"] for m in messages[:2]] == [m["id"] for m in first]
        assert all(
            m["retrieval_details"]["mode"] == "hybrid" for m in messages if m["role"] == "assistant"
        )
        memory.store.correct_transcript(key, "Søren betaler fakturaen.")
        assert memory.store.evidence(result["output_id"])["changed_source_ids"] == [key]
        assert memory.store.chat_session(result["session_id"])["messages"][1]["stale"]
    finally:
        await http.aclose()


@pytest.mark.parametrize("mutation", ["correction", "delete", "retention"])
def test_shared_source_lifecycle_updates_feedback_inbox_decisions_weekly_and_prep(
    tmp_path, mutation
):
    config = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    with TestClient(create_app(config)) as client:
        memory = client.app.state.memory
        store = memory.store
        key = str(uuid4())
        text = "May: I will send the proposal."
        store.ingest(key, "fixture", AT, audio_data(), 1)
        store.complete(key, text)
        source = client.get(f"/v1/sources/{key}").json()
        run(store, [proposal(key, text, title="Send the proposal")])
        inbox = listing(store)["items"][0]
        decision = create_decision(client, source, "Send the proposal")
        memory.inference = WeeklyModel()
        weekly = client.app.state.weekly
        weekly.enqueue(WEEK, str(uuid4()), False)
        client.portal.call(weekly.process_one)
        version = weekly.store.versions(WEEK.isoformat())[0]
        memory.inference = FakeInference()
        answer = client.post(
            "/v1/chat", json={"question": "May proposal", "day": "2026-10-15"}
        ).json()
        assert (
            client.put(
                f"/v1/feedback/{uuid4()}",
                json={
                    "issue": "incorrect",
                    "output_id": answer["output_id"],
                    "source_id": key,
                    "note": "fixture report",
                },
            ).status_code
            == 200
        )
        found = client.post("/v1/conversation-prep/search", json={"subject": "May"}).json()
        assert found["sources"][0]["audio_availability"] == "available"
        memory.inference = PrepModel()
        prepared = client.post("/v1/conversation-prep/generate", json=prep_body(found))
        assert prepared.status_code == 200, prepared.text
        if mutation == "correction":
            store.correct_transcript(key, "May: I will send the revised proposal tomorrow.")
            assert client.get(f"/v1/outputs/{answer['output_id']}/evidence").json()[
                "changed_source_ids"
            ] == [key]
            assert (
                client.get(f"/v1/decisions/{decision['id']}").json()["evidence_condition"]
                == "stale"
            )
            assert InboxStore(store).item(inbox["id"])["health"] == "needs_review"
            assert weekly.read(WEEK, version["id"])["stale"]
            assert client.get("/v1/feedback").json()["total"] == 1
        else:
            if mutation == "delete":
                assert client.delete(f"/v1/audio/{key}").status_code == 200
            else:
                store.apply_retention(0, 1, now=AT + timedelta(days=3))
            assert client.get(f"/v1/outputs/{answer['output_id']}/evidence").status_code == 404
            assert client.get("/v1/feedback").json()["total"] == 0
            removed = client.get(f"/v1/decisions/{decision['id']}").json()
            assert removed["evidence_condition"] == "removed" and removed["content"] is None
            assert not listing(store)["items"]
            assert not weekly.store.versions(WEEK.isoformat())
            assert client.get("/v1/chat/sessions").json()["items"] == []
        assert client.post(
            "/v1/conversation-prep/validate",
            json={"search": found["search"], "snapshot": found["snapshot"]},
        ).json() == {"valid": False}
