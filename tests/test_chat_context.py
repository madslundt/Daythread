"""Model-aware conversation compaction preserves history and bounds complete requests."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_long_conversation_is_summarized_instead_of_silently_dropping_early_turns(tmp_path):
    store = Store(tmp_path)
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    session = store.ensure_session(None, "Launch planning")
    for index in range(10):
        question = (
            "Remember Project Orchid and its accessibility review. "
            if index == 0
            else f"Planning question {index}. "
        ) + "Details discussed. " * 120
        store.save_exchange(session["id"], question, f"Recorded reply {index}.", [])
    key = str(uuid4())
    store.ingest(key, "phone", datetime.now(UTC), b"audio", 1)
    store.complete(key, "The launch planning includes an accessibility review.")
    calls = []

    def response(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "home", "context_window": 8192}]})
        body = json.loads(request.content)
        calls.append(body)
        compacting = body["messages"][0]["content"].startswith("You compact")
        text = (
            (
                "The user discussed Project Orchid, an accessibility review, and launch planning."
                if "Project Orchid" in body["messages"][1]["content"]
                else "The user discussed launch planning."
            )
            if compacting
            else "The accessibility review is recorded."
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        answer = await Memory(store, settings, Inference(settings, client)).chat(
            "What did we discuss in planning?",
            12,
            session["id"],
        )
    assert answer["answer"]
    assert any("Project Orchid" in call["messages"][1]["content"] for call in calls[:-1])
    assert "Project Orchid" in calls[-1]["messages"][1]["content"]
    assert len(store.chat_session(session["id"])["messages"]) == 22
    assert any("conversation" in call["messages"][0]["content"].lower() for call in calls[:-1])


def assert_budget(body, limit):
    from omi_local.chat_context import safety

    used = sum(len(row["content"].encode("utf-8")) for row in body["messages"]) + 128
    assert used + body["max_tokens"] + safety(limit) <= limit, (used, body["max_tokens"], limit)


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [4096, 8192, 32768])
@pytest.mark.parametrize("text", ["planning " * 2500, "日程🙂" * 1800, '\\x00"\\\\\n' * 1000])
async def test_complete_prompts_are_bounded_for_giant_evidence_and_turns(tmp_path, limit, text):
    from omi_local.inference_errors import InferenceError

    key = str(uuid4())
    settings = Settings(data_dir=tmp_path, chat_context_window_tokens=limit, _env_file=None)
    calls = []

    def response(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content)
        assert_budget(body, limit)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Earlier user goal."
                            if body["messages"][0]["content"].startswith("You compact")
                            else f"Planning [{key}]",
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        inference = Inference(settings, client)
        try:
            await inference.chat(
                "What planning?",
                [{"id": key, "started_at": "2026-11-02T12:00:00Z", "text": text}],
                [{"id": "old", "role": "user", "content": text}],
            )
        except InferenceError as error:
            assert "context window" in str(error)
    assert calls


@pytest.mark.anyio
async def test_small_summary_route_never_receives_chat_sized_evidence_maps(tmp_path):
    key = str(uuid4())
    settings = Settings(
        data_dir=tmp_path, llm_routes={"summary": {"model": "tiny"}}, _env_file=None
    )
    seen = []

    def response(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "home", "context_window": 32768},
                        {"id": "tiny", "context_window": 4096},
                    ]
                },
            )
        body = json.loads(request.content)
        assert_budget(body, 4096 if body["model"] == "tiny" else 32768)
        seen.append(body["model"])
        return httpx.Response(
            200, json={"choices": [{"message": {"content": f"Planning [{key}]"}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await Inference(settings, client).chat(
            "What planning?",
            [
                {"id": key, "started_at": "2026-11-02T12:00:00Z", "text": "planning " * 4000},
            ],
        )
    assert seen and set(seen) == {"home"}


@pytest.mark.anyio
async def test_short_chat_keeps_complete_recent_turns_without_compaction(tmp_path):
    settings = Settings(data_dir=tmp_path, chat_context_window_tokens=8192, _env_file=None)
    bodies = []

    def response(request):
        body = json.loads(request.content)
        assert_budget(body, 8192)
        bodies.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Recorded reply"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await Inference(settings, client).chat(
            "What next?",
            [],
            [
                {"id": "a", "role": "user", "content": "Please focus on accessibility."},
                {"id": "b", "role": "assistant", "content": "We discussed the review."},
            ],
        )
    assert len(bodies) == 1
    assert "Please focus on accessibility." in bodies[0]["messages"][1]["content"]


@pytest.mark.anyio
async def test_output_retry_budget_remains_inside_window(tmp_path):
    settings = Settings(data_dir=tmp_path, chat_context_window_tokens=4096, _env_file=None)
    seen = []

    def response(request):
        body = json.loads(request.content)
        assert_budget(body, 4096)
        seen.append(body["max_tokens"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if len(seen) == 1 else "stop",
                        "message": {"content": "Recorded reply"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await Inference(settings, client).chat("What next?", [])
    assert len(seen) == 2
    assert seen[1] >= seen[0]


@pytest.mark.anyio
async def test_summary_survives_restart_and_only_its_recorded_sources_invalidate_it(tmp_path):
    from omi_local.chat_context_store import context_metadata
    from omi_local.generation_jobs import GenerationJobs

    settings = Settings(
        data_dir=tmp_path, worker_enabled=False, chat_context_window_tokens=8192, _env_file=None
    )
    store = Store(tmp_path)
    key = str(uuid4())
    store.ingest(key, "phone", datetime.now(UTC), b"used", 1)
    store.complete(key, "Accessibility planning")
    source = store.get_audio(key)
    session_id = store.ensure_session(None, "Planning")["id"]
    originals = []
    for index in range(10):
        question = f"Planning detail {index}. " + "Relevant context. " * 120
        originals.append(question)
        store.save_exchange(session_id, question, "Recorded discussion", [source])
    compact_calls = []

    def response(request):
        body = json.loads(request.content)
        assert_budget(body, 8192)
        compacting = body["messages"][0]["content"].startswith("You compact")
        if compacting:
            compact_calls.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "User goals and planning questions."
                            if compacting
                            else f"Planning [{key}]",
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        memory = Memory(store, settings, Inference(settings, client))
        stages = []

        async def event(value):
            if value["type"] == "status":
                stages.append(value["stage"])

        await memory.chat("Planning?", 12, session_id, on_event=event)
        initial = context_metadata(store, session_id)
        assert initial["compaction_count"] == 1
        assert initial["compacted_at"]
        assert initial["context_limit"] == 8192
        assert initial["context_tokens"] < 8192
        assert "compacting" in stages
        assert stages[-1] == "generating"
        first_count = len(compact_calls)
        reopened = Store(tmp_path)
        assert context_metadata(reopened, session_id) == initial
        await Memory(reopened, settings, Inference(settings, client)).chat(
            "Planning next?", 12, session_id
        )
        assert len(compact_calls) == first_count, (
            "A short follow-up must reuse the persisted summary"
        )
        history = reopened.chat_session(session_id)
        assert history["context"]["compaction_count"] == 1
        assert [
            row["content"] for row in history["messages"] if row["content"] in originals
        ] == originals
        other = str(uuid4())
        reopened.ingest(other, "phone", datetime.now(UTC), b"unrelated", 1)
        reopened.complete(other, "Unrelated conversation")
        assert context_metadata(reopened, session_id)["compaction_count"] == 1
        reopened.correct_transcript(key, "The accessibility plan changed")
        assert context_metadata(reopened, session_id)["compaction_count"] == 0
        assert len(reopened.chat_session(session_id)["messages"]) == 24
        jobs = GenerationJobs(Memory(reopened, settings, Inference(settings, client)))
        assert jobs.delete_chat(session_id)
        with reopened.connect() as db:
            assert not db.execute("SELECT * FROM chat_context").fetchall()
            assert not db.execute("SELECT * FROM chat_context_sources").fetchall()


@pytest.mark.anyio
async def test_model_capacity_changes_compaction_threshold_and_summary_is_untrusted(tmp_path):
    history = [
        {"id": str(i), "role": "user", "content": "Earlier goals and preferences. " * 25}
        for i in range(12)
    ]
    counts = []
    for limit in (8192, 65536):
        settings = Settings(data_dir=tmp_path, llm_model=f"model-{limit}", _env_file=None)
        calls = []

        def response(request):
            if request.method == "GET":
                return httpx.Response(
                    200, json={"data": [{"id": f"model-{limit}", "context_length": limit}]}
                )
            body = json.loads(request.content)
            assert_budget(body, limit)
            calls.append(body)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Earlier user goals."}}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            state = {}
            await Inference(settings, client).chat("What next?", [], history, context_state=state)
        counts.append(state["metadata"]["compaction_count"])
        assert state["metadata"]["limit_source"] == "provider"
        final = json.loads(calls[-1]["messages"][1]["content"])
        assert "untrusted" in final["task"]
        assert "Earlier user goals." not in final["evidence"]
        assert not calls[-1]["messages"][0]["content"].startswith("You compact")
    assert counts == [1, 0]


@pytest.mark.anyio
async def test_disabled_compaction_refuses_overflow_without_deleting_messages(tmp_path):
    from omi_local.inference_errors import InferenceError

    settings = Settings(
        data_dir=tmp_path, chat_auto_compact=False, chat_context_window_tokens=4096, _env_file=None
    )
    calls = []

    def response(request):
        calls.append(request)
        raise AssertionError("No overflowing request may reach the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(InferenceError, match="disabled"):
            await Inference(settings, client).chat(
                "What next?",
                [],
                [
                    {"id": "one", "role": "user", "content": "Earlier goals. " * 1000},
                ],
            )
    assert calls == []


@pytest.mark.anyio
async def test_advertised_tiny_context_is_honored_instead_of_unsafe_fallback(tmp_path):
    from omi_local.inference_errors import InferenceError

    settings = Settings(data_dir=tmp_path, _env_file=None)
    posts = []

    def response(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "home", "context_window": 1024}]})
        posts.append(request)
        raise AssertionError("The system prompt alone exceeds this provider's context window")

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(InferenceError, match="context window"):
            await Inference(settings, client).chat("What next?", [])
    assert posts == []


@pytest.mark.anyio
async def test_durable_job_preserves_only_actionable_safe_context_errors(tmp_path):
    from omi_local.generation_jobs import GenerationJobs, GenerationRequest
    settings = Settings(data_dir=tmp_path, chat_context_window_tokens=1024, _env_file=None)
    store = Store(tmp_path)
    key = str(uuid4())
    store.ingest(key, "phone", datetime.now(UTC), b"audio", 1)
    store.complete(key, "Accessibility planning")

    def response(request):
        raise AssertionError("An impossible context must fail before a provider request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        jobs = GenerationJobs(Memory(store, settings, Inference(settings, client)))
        accepted = jobs.enqueue(GenerationRequest(id=uuid4(), kind="chat",
                                                  parameters={"question": "Accessibility?"}))
        await jobs.process_one()
        failed = jobs.get(accepted["id"])
        assert failed["state"] == "failed"
        assert failed["error"] == "This model’s context window is too small for the request."
        assert len(store.chat_session(accepted["parameters"]["session_id"])["messages"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("discovery", ["missing_capacity", "unsupported"])
async def test_default_65536_fallback_compacts_and_bounds_full_requests(tmp_path, discovery):
    from omi_local.chat_context import FALLBACK_WINDOW

    settings = Settings(data_dir=tmp_path, _env_file=None)
    assert settings.chat_context_window_tokens == 0
    assert FALLBACK_WINDOW == 65536
    history = [
        {"id": str(index), "role": "user", "content": "Weekly planning. " * 500}
        for index in range(12)
    ]
    history[0]["content"] = "Project65536 must be remembered. " + history[0]["content"]
    originals = [dict(message) for message in history]
    bodies = []

    def response(request):
        if request.method == "GET":
            if discovery == "unsupported":
                return httpx.Response(404)
            return httpx.Response(200, json={"data": [{"id": "home"}]})
        body = json.loads(request.content)
        assert_budget(body, 65536)
        bodies.append(body)
        compacting = body["messages"][0]["content"].startswith("You compact")
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Project65536 must be remembered." if compacting else "Saved response.",
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        state = {}
        await Inference(settings, client).chat("What next?", [], history, context_state=state)
    assert state["metadata"]["context_limit"] == 65536
    assert state["metadata"]["limit_source"] == "fallback"
    assert state["metadata"]["compaction_count"] == 1
    assert len(bodies) > 1
    assert "Project65536" in bodies[-1]["messages"][1]["content"]
    assert history == originals
