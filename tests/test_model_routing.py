"""Task routing and generation latency checks; never contact real model services."""

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from omi_local import conversation_prep, decision_service
from omi_local.decisions import ExtractionRequest
from omi_local.domain import stamp
from omi_local.inbox_jobs import InboxJobs
from omi_local.inference import Inference
from omi_local.prep_models import PrepGenerate, PrepSearch
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.weekly import WeeklyReviews

TASKS = ("chat", "summary", "extraction", "preparation", "weekly")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def config(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None, **kwargs)


def test_task_routes_inherit_default_and_support_nested_environment(tmp_path, monkeypatch):
    settings = config(tmp_path, llm_model="shared", llm_revision="v1", inference_token="base")
    for task in TASKS:
        route = settings.llm_route(task)
        assert route.model == "shared"
        assert route.base_url == settings.llm_base_url
        assert route.token.get_secret_value() == "base"
        assert route.revision == "v1"
    monkeypatch.setenv("OMI_LLM_ROUTES__CHAT__MODEL", "fast-chat")
    monkeypatch.setenv("OMI_LLM_ROUTES__SUMMARY__BASE_URL", "http://summarizer.local/v1/")
    monkeypatch.setenv("OMI_LLM_ROUTES__SUMMARY__TOKEN", "summary-secret")
    settings = config(tmp_path, llm_model="shared")
    assert settings.llm_route("chat").model == "fast-chat"
    assert settings.llm_route("chat").base_url == settings.llm_base_url
    assert settings.llm_route("summary").model == "shared"
    assert settings.llm_route("summary").base_url == "http://summarizer.local/v1"
    assert settings.llm_route("summary").token.get_secret_value() == "summary-secret"
    assert "summary-secret" not in repr(settings)
    monkeypatch.setenv("OMI_LLM_ROUTES__SUMMARY__TOKEN", "")
    assert config(tmp_path).llm_route("summary").token.get_secret_value() == ""


@pytest.mark.parametrize(
    "routes",
    [
        {"typo": {"model": "x"}},
        {"chat": {"base_url": "https://example.com/v1"}},
        {"chat": {"base_url": "http://user:secret@localhost/v1"}},
        {"chat": {"model": " "}},
        {"chat": {"modle": "x"}},
    ],
)
def test_routes_reject_invalid_configuration(tmp_path, routes):
    with pytest.raises(ValidationError):
        config(tmp_path, llm_routes=routes)


@pytest.mark.anyio
async def test_all_generation_tasks_use_their_resolved_route(tmp_path):
    settings = config(
        tmp_path,
        inference_token="default-secret",
        llm_routes={
            task: {"model": task, "base_url": f"http://{task}.local/v1", "token": task}
            for task in TASKS
        },
    )
    seen = []
    key = str(uuid4())
    sources = [{"id": key, "started_at": stamp(datetime.now(UTC)), "text": "Evidence"}]

    def handler(request):
        body = json.loads(request.content)
        seen.append((str(request.url), body["model"], request.headers["authorization"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": f"OK [{key}]"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http)
        await inference.chat("Question", sources)
        await inference.summarize(sources, "a recap")
        for task in ("extraction", "preparation", "weekly"):
            await inference.for_task(task).generate_json("Task", "Evidence")
    assert seen == [
        (f"http://{task}.local/v1/chat/completions", task, f"Bearer {task}") for task in TASKS
    ]


@pytest.mark.anyio
async def test_large_chat_reduces_with_summary_model_and_answers_without_extra_call(tmp_path):
    settings = config(tmp_path, llm_routes={"chat": {"model": "fast"}, "summary": {"model": "sum"}})
    key = str(uuid4())
    source = {"id": key, "started_at": stamp(datetime.now(UTC)), "text": "Evidence " * 2300}
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": f"Evidence notes [{key}]"}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        answer, trace = await Inference(settings, http).chat_traced("What happened?", [source], [])
    assert key in answer
    # Three map batches, then answer directly from the bounded notes.
    assert [call["model"] for call in calls] == ["sum", "sum", "sum", "fast"]
    assert trace["model_calls"] == 4
    assert trace["reduction_used"]
    assert json.loads(calls[-1]["messages"][1]["content"])["task"] == "What happened?"


@pytest.mark.anyio
async def test_chat_and_recap_provenance_and_cache_use_the_right_models(tmp_path):
    settings = config(tmp_path, llm_routes={"chat": {"model": "fast"}, "summary": {"model": "sum"}})
    store = Store(settings.data_dir)
    key = uuid4()
    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    store.ingest(key, "omi", at, b"test audio", 30)
    store.complete(str(key), "Kitchen discussion")

    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": f"Recorded [{key}]"}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        memory = Memory(store, settings, Inference(settings, http))
        result = await memory.chat("Kitchen", 12)
        assert result["evidence"]["model"] == "fast"
        recap = await memory.recap(date(2026, 10, 15))
        assert recap["model"] == recap["evidence"]["model"] == "sum"
        before = memory.fingerprint(*memory.day_window(date(2026, 10, 15)))
        settings.llm_routes["chat"].model = "other-chat"
        assert memory.fingerprint(*memory.day_window(date(2026, 10, 15))) == before
        settings.llm_routes["summary"].revision = "new-weights"
        assert memory.cached_recap(date(2026, 10, 15))["stale"]


@pytest.mark.anyio
async def test_extraction_preparation_and_weekly_workflows_route_real_http_requests(tmp_path):
    settings = config(
        tmp_path,
        timezone="UTC",
        llm_routes={task: {"model": task} for task in TASKS},
    )
    store = Store(settings.data_dir)
    key = uuid4()
    store.ingest(key, "omi", datetime(2026, 10, 6, 12, tzinfo=UTC), b"audio", 30)
    store.complete(str(key), "May discussed the kitchen")
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        content = {
            "extraction": {"candidates": []},
            "weekly": {"items": []},
            "preparation": {
                "latest_discussion": [
                    {
                        "text": "May discussed the kitchen",
                        "citations": [
                            {"source_id": str(key), "excerpt": "May discussed the kitchen"}
                        ],
                    }
                ],
                "promises_to_check": [],
                "recorded_questions": [],
                "suggested_questions": [],
            },
        }[model]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(settings, http)
        memory = Memory(store, settings, inference)
        jobs = InboxJobs(store)
        ids = jobs.discover("UTC", model=settings.llm_route("extraction").cache_key)
        assert await jobs.process_one(inference)
        assert jobs.status(ids[0])["state"] == "done"
        await decision_service.extract(
            memory, ExtractionRequest(request_id=uuid4(), source_ids=[key])
        )
        query = PrepSearch(subject="May")
        found = conversation_prep.search(memory, query)
        prepared = await conversation_prep.generate(
            memory,
            PrepGenerate(
                search=query,
                snapshot=found["snapshot"],
                selected=[{"id": key, "version": found["sources"][0]["version"]}],
                latest_source_id=key,
            ),
        )
        assert prepared["model"] == "preparation"
        weekly = WeeklyReviews(memory)
        monday = date(2026, 10, 5)
        queued = weekly.enqueue(monday, str(uuid4()), False)
        assert await weekly.process_one()
        assert weekly.store.job(queued["job"]["id"])["state"] == "succeeded"
        assert weekly.read(monday)["version"]["model"] == "weekly"
        before = weekly.snapshot(monday)["fingerprint"]
        settings.llm_routes["chat"].model = "other-chat"
        assert weekly.snapshot(monday)["fingerprint"] == before
        settings.llm_routes["weekly"].revision = "new-weights"
        assert weekly.read(monday)["stale"]
    assert seen == ["extraction", "extraction", "preparation", "weekly", "weekly"]


@pytest.mark.anyio
async def test_queued_extraction_rejects_changed_task_route_before_calling_model(tmp_path):
    settings = config(tmp_path, llm_routes={"extraction": {"model": "extractor"}})
    store = Store(settings.data_dir)
    key = uuid4()
    store.ingest(key, "omi", datetime(2026, 10, 6, 12, tzinfo=UTC), b"audio", 30)
    store.complete(str(key), "I will send the report")
    jobs = InboxJobs(store)
    ids = jobs.discover("UTC", model=settings.llm_route("extraction").cache_key)
    settings.llm_routes["extraction"].revision = "changed"

    def unexpected(request):
        pytest.fail("A queued job must reject changed configuration before model inference")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        assert await jobs.process_one(Inference(settings, http))
    assert jobs.status(ids[0])["state"] == "failed"
    assert "configuration changed" in jobs.status(ids[0])["error"]


@pytest.mark.anyio
async def test_legacy_queued_extraction_keeps_shared_model_without_overrides(tmp_path):
    settings = config(tmp_path, llm_model="shared")
    store = Store(settings.data_dir)
    key = uuid4()
    store.ingest(key, "omi", datetime(2026, 10, 6, 12, tzinfo=UTC), b"audio", 30)
    store.complete(str(key), "I will send the report")
    jobs = InboxJobs(store)
    ids = jobs.discover("UTC", model="shared")

    def handler(request):
        assert json.loads(request.content)["model"] == "shared"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"candidates":[]}'}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await jobs.process_one(Inference(settings, http))
    assert jobs.status(ids[0])["state"] == "done"
