"""Durable chat accepts immediately, survives navigation/restarts and deletes safely."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.generation_jobs import GenerationJobs, GenerationRequest
from omi_local.inference import Inference
from omi_local.notifications import NotificationService
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store


class ChatInference:
    calls = 0

    async def chat(self, question, sources, history=None):
        self.calls += 1
        assert not history or history[-1]["content"] != question
        return "**Planning** is recorded."


@pytest.fixture
def jobs(tmp_path):
    config = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    memory = Memory(Store(tmp_path), config, ChatInference())
    memory.notifications = NotificationService(memory.store, config)
    key = str(uuid4())
    memory.store.ingest(key, "test", datetime.now(UTC), b"test", 1)
    memory.store.complete(key, "Planning the weekend")
    yield GenerationJobs(memory)
    asyncio.run(memory.notifications.close())


def request(question="What was the weekend planning?", **params):
    return GenerationRequest(id=uuid4(), kind="chat", parameters={"question": question, **params})


def test_acceptance_saves_named_dated_chat_and_question_before_generation(jobs):
    body = request("  What was\n the weekend planning?  ")
    accepted = jobs.enqueue(body)
    assert accepted["state"] == "queued"
    session_id = accepted["parameters"]["session_id"]
    history = jobs.store.chat_sessions()
    assert len(history) == 1
    assert history[0]["id"] == session_id
    assert history[0]["title"] == "What was the weekend planning?"
    assert history[0]["created_at"] and history[0]["updated_at"]
    detail = jobs.store.chat_session(session_id)
    assert [message["role"] for message in detail["messages"]] == ["user"]
    assert jobs.pending_chat(session_id)["id"] == str(body.id)
    assert jobs.enqueue(body) == accepted
    assert len(jobs.store.chat_session(session_id)["messages"]) == 1


def test_chat_finishes_once_with_saved_markdown_and_private_localized_push(jobs):
    body = request()
    accepted = jobs.enqueue(body)
    session_id = accepted["parameters"]["session_id"]
    jobs.store.register_notification_device("phone", "ab" * 32, "sandbox", False)
    assert asyncio.run(jobs.process_one())
    completed = jobs.get(str(body.id))
    assert completed["state"] == "completed", completed
    assert completed["result"]["session_id"] == session_id
    assert completed["result"]["answer"] == "**Planning** is recorded."
    messages = jobs.store.chat_session(session_id)["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["id"] == completed["result"]["user_message_id"]
    assert messages[1]["id"] == completed["result"]["message_id"]
    assert jobs.pending_chat(session_id) is None
    jobs.notify_completed()
    jobs.notify_completed()
    notices = jobs.store.notifications_after(0)["notifications"]
    assert len(notices) == 1
    assert notices[0]["kind"] == "chat"
    assert notices[0]["target"]["session_id"] == session_id
    assert notices[0]["title"] == "Chat reply ready"
    assert "weekend" not in notices[0]["body"]
    assert "Planning" not in notices[0]["body"]
    danish = jobs.store.notifications_after(0, interface_language="da")["notifications"][0]
    assert danish["title"] == "Chatsvar klar"
    assert len(jobs.store.pending_notifications()) == 1
    jobs.store.notification_presence("phone", True)
    assert jobs.store.pending_notifications() == []
    jobs.store.notification_presence("phone", False)
    assert len(jobs.store.pending_notifications()) == 1
    assert jobs.enqueue(body)["result"] == completed["result"]
    assert not asyncio.run(jobs.process_one())
    assert jobs.memory.inference.calls == 1


def test_restart_after_accept_and_after_exchange_commit_never_duplicates(jobs):
    body = request()
    accepted = jobs.enqueue(body)
    session_id = accepted["parameters"]["session_id"]
    with jobs.store.connect() as db:
        db.execute("UPDATE generation_jobs SET state='running' WHERE id=?", (str(body.id),))
    restarted = GenerationJobs(jobs.memory)
    assert restarted.get(str(body.id))["state"] == "queued"
    # Simulate process death after the exchange commit but before process_one's
    # usual completion call. The transaction must already have saved the job result.
    result = asyncio.run(restarted.execute(restarted.get(str(body.id))))
    assert restarted.get(str(body.id))["state"] == "completed"
    assert GenerationJobs(jobs.memory).get(str(body.id))["result"] == result
    assert not asyncio.run(restarted.process_one())
    assert len(jobs.store.chat_session(session_id)["messages"]) == 2
    assert jobs.memory.inference.calls == 1


def test_deleted_inflight_chat_never_reappears_or_notifies(jobs):
    body = request()
    accepted = jobs.enqueue(body)
    session_id = accepted["parameters"]["session_id"]

    async def scenario():
        began, release = asyncio.Event(), asyncio.Event()

        async def chat(question, sources, history):
            began.set()
            await release.wait()
            return "Late private answer"

        jobs.memory.inference.chat = chat
        task = asyncio.create_task(jobs.process_one())
        await began.wait()
        assert jobs.delete_chat(session_id)
        release.set()
        await task

    asyncio.run(scenario())
    assert jobs.store.chat_session(session_id) is None
    assert jobs.store.chat_sessions() == []
    with pytest.raises(HTTPException) as missing:
        jobs.get(str(body.id))
    assert missing.value.status_code == 404
    jobs.notify_completed()
    assert jobs.store.notifications_after(0)["notifications"] == []
    with pytest.raises(HTTPException) as deleted:
        jobs.enqueue(body)
    assert deleted.value.status_code == 410


def test_deletion_removes_results_evidence_notices_and_push_deliveries(jobs):
    accepted = jobs.enqueue(request())
    session_id = accepted["parameters"]["session_id"]
    jobs.store.register_notification_device("phone", "ab" * 32, "sandbox", False)
    asyncio.run(jobs.process_one())
    jobs.notify_completed()
    assert jobs.store.pending_notifications()
    assert jobs.delete_chat(session_id)
    assert not jobs.delete_chat(session_id)
    with jobs.store.connect() as db:
        for table in ("chat_sessions", "chat_messages", "generation_jobs", "output_evidence",
                      "notification_deliveries", "notifications", "generation_job_sources"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_chat_validation_and_inflight_followup_conflicts_do_not_create_orphans(jobs):
    for params in ({"question": "  "}, {"question": "test", "day": "2026-02-30"},
                   {"question": "test", "start_day": "2026-10-10"}):
        with pytest.raises(HTTPException) as invalid:
            jobs.enqueue(GenerationRequest(id=uuid4(), kind="chat", parameters=params))
        assert invalid.value.status_code == 422
    assert jobs.store.chat_sessions() == []
    accepted = jobs.enqueue(request())
    with pytest.raises(HTTPException) as conflict:
        jobs.enqueue(request(session_id=accepted["parameters"]["session_id"]))
    assert conflict.value.status_code == 409
    assert len(jobs.store.chat_sessions()) == 1
    assert len(jobs.store.chat_session(accepted["parameters"]["session_id"])["messages"]) == 1


def test_accepted_http_request_survives_client_exit_and_profiles_are_isolated(tmp_path):
    config = Settings(data_dir=tmp_path, worker_enabled=False,
                      background_processing_enabled=False, _env_file=None)
    body = {"id": str(uuid4()), "kind": "chat", "parameters": {"question": "Any evidence?"}}
    with TestClient(create_app(config)) as client:
        profile_id = client.post("/v1/profiles", json={"name": "Personal"}).json()["id"]
        headers = {"X-Omi-Profile": profile_id}
        response = client.post("/v1/jobs", json=body, headers=headers)
        assert response.status_code == 202, response.text
        session_id = response.json()["parameters"]["session_id"]
        detail = client.get(f"/v1/chat/sessions/{session_id}", headers=headers).json()
        assert detail["pending_job"]["id"] == body["id"]
    with TestClient(create_app(config)) as client:
        detail = client.get(f"/v1/chat/sessions/{session_id}", headers=headers)
        assert detail.status_code == 200
        assert len(detail.json()["messages"]) == 1
        assert client.get(f"/v1/chat/sessions/{session_id}").status_code == 404
        assert client.delete(f"/v1/chat/sessions/{session_id}").status_code == 404
        jobs = client.app.state.profiles.runtimes[profile_id].jobs
        asyncio.run(jobs.process_one())
        detail = client.get(f"/v1/chat/sessions/{session_id}", headers=headers).json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["pending_job"] is None
        assert client.delete(f"/v1/chat/sessions/{session_id}", headers=headers).status_code == 204
        assert client.get(f"/v1/chat/sessions/{session_id}", headers=headers).status_code == 404


def test_running_chat_poll_exposes_markdown_draft_before_durable_completion(jobs):
    async def scenario():
        streamed, release = asyncio.Event(), asyncio.Event()

        class Tokens(httpx.AsyncByteStream):
            async def __aiter__(self):
                first = {"choices": [{"delta": {"content": "**Planning**"},
                                      "finish_reason": None}]}
                yield ("data: " + json.dumps(first) + "\n\n").encode()
                streamed.set()
                await release.wait()
                last = {"choices": [{"delta": {"content": " is recorded."},
                                     "finish_reason": "stop"}]}
                yield ("data: " + json.dumps(last) + "\n\ndata: [DONE]\n\n").encode()

        async def response(request):
            assert json.loads(request.content)["stream"]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Tokens())

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            jobs.memory.inference = Inference(jobs.memory.settings, client)
            accepted = jobs.enqueue(request())
            task = asyncio.create_task(jobs.process_one())
            try:
                await asyncio.wait_for(streamed.wait(), 5)
                pending = jobs.get(accepted["id"])
                assert pending["state"] == "running"
                assert pending["draft"] == "**Planning**"
                assert pending["stage"] == "generating"
                detail = jobs.store.chat_session(pending["parameters"]["session_id"])
                assert len(detail["messages"]) == 1
            finally:
                release.set()
                await task
            completed = jobs.get(accepted["id"])
            assert completed["state"] == "completed", completed
            assert completed["result"]["answer"] == "**Planning** is recorded."

    asyncio.run(scenario())


def test_direct_chat_cannot_append_ahead_of_an_accepted_durable_reply(jobs):
    accepted = jobs.enqueue(request())
    session_id = accepted["parameters"]["session_id"]
    with pytest.raises(ValueError, match="already being prepared"):
        asyncio.run(jobs.memory.chat("A different question", 12, session_id))
    # The exchange transaction rechecks too: an older HTTP generation can finish
    # after a different device has accepted a durable request in this session.
    with pytest.raises(ValueError, match="already being prepared"):
        jobs.store.save_exchange(session_id, "Earlier question", "Late answer", [])
    assert len(jobs.store.chat_session(session_id)["messages"]) == 1
    assert jobs.memory.inference.calls == 0
