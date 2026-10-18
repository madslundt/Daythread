"""Recovery exports must not resume or rewrite durable background work."""

import asyncio
import json
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_backend import AT, wav

from omi_local.api import create_app
from omi_local.configuration import Configuration
from omi_local.embeddings import Embeddings, index_one
from omi_local.generation_jobs import SCHEMA as JOB_SCHEMA
from omi_local.generation_jobs import GenerationJobs, GenerationRequest
from omi_local.inbox_jobs import InboxJobs
from omi_local.inference import Inference
from omi_local.inference_errors import InferenceDisabled
from omi_local.recording_topics import process_topics
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.weekly import WeeklyReviews
from omi_local.worker import process_one, run_worker, scheduled_decisions, scheduled_recaps


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("field", ["stt_enabled", "llm_enabled"])
def test_model_task_flags_are_environment_policy_not_restored_preferences(tmp_path, field):
    (tmp_path / ".private-settings.json").write_text(json.dumps({"overrides": {field: True}}))
    initial = Settings(data_dir=tmp_path, _env_file=None, **{field: False})
    assert getattr(Configuration(initial).startup(initial), field) is False


@pytest.mark.anyio
@pytest.mark.parametrize("stt", [True, False])
@pytest.mark.parametrize("llm", [True, False])
@pytest.mark.parametrize("embedding", [True, False])
async def test_processing_tasks_are_independent(tmp_path, monkeypatch, stt, llm, embedding):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, timezone="UTC", stt_enabled=stt, llm_enabled=llm,
        embedding_enabled=embedding, embedding_model="test", embedding_revision="v1",
        embedding_dimension=2,
    )
    inference = SimpleNamespace(settings=settings, transcribe=AsyncMock(return_value="New invoice"))
    memory = Memory(Store(tmp_path), settings, inference)
    queued, imported = uuid4(), uuid4()
    memory.store.ingest(queued, "omi", AT, wav([7] * 16000), 1)
    memory.store.ingest(imported, "omi", AT + timedelta(seconds=2), b"import", 1)
    memory.store.complete(str(imported), "Existing invoice")
    monkeypatch.setattr("omi_local.worker.contains_speech_energy", lambda *_: True)
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda _: {"status": "unknown"})
    assert await process_one(memory) is stt
    assert inference.transcribe.await_count == int(stt)
    row = memory.store.get_audio(str(queued))
    assert row["state"] == ("done" if stt else "queued")
    assert row["attempts"] == int(stt)

    def vectors(request):
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [
            {"index": index, "embedding": [1, 0]} for index in range(len(texts))
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(vectors)) as client:
        adapter = Embeddings(settings, client)
        memory.retriever.embeddings = adapter
        if embedding:
            while await index_one(memory.store, adapter, memory.retriever.set_id):
                pass
        else:
            assert not await index_one(memory.store, adapter, "inactive")
        found = await memory.search("invoice", 10)
        assert str(imported) in {source["id"] for source in found["sources"]}
        assert found["retrieval"] == ("hybrid" if embedding else "keyword")
        if not llm:
            with pytest.raises(InferenceDisabled, match="off"):
                await memory.chat("invoice", 10)


@pytest.mark.anyio
async def test_paused_llm_jobs_are_not_claimed_and_resume_after_enable(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, _env_file=None, llm_enabled=False)
    memory = Memory(Store(tmp_path), settings, SimpleNamespace(settings=settings))
    jobs = GenerationJobs(memory)
    job_id = str(uuid4())
    with memory.store.connect() as db:
        db.execute(
            "INSERT INTO generation_jobs(id,kind,parameters,created_at) VALUES(?,?,?,?)",
            (job_id, "recap", json.dumps({"day": "2026-10-15"}), AT.isoformat()),
        )
    memory.recap = AsyncMock(return_value={"sources": [], "summary": "Ready"})
    assert not await jobs.process_one()
    assert jobs.get(job_id)["state"] == "queued"
    assert jobs.observation() == {"queued": 0, "running": 0, "stages": {}, "paused": 1}
    memory.recap.assert_not_awaited()
    weekly = WeeklyReviews(memory)
    monkeypatch.setattr(weekly.store, "claim", lambda: pytest.fail("Paused weekly claim"))
    assert not await weekly.process_one()
    inbox = InboxJobs(memory.store)
    monkeypatch.setattr(inbox, "claim", lambda: pytest.fail("Paused inbox claim"))
    assert not await inbox.process_one(memory.inference)
    assert not await process_topics(memory)
    await scheduled_recaps(memory)
    await scheduled_decisions(memory)
    with pytest.raises(InferenceDisabled):
        jobs.enqueue(GenerationRequest(id=uuid4(), kind="recap", parameters={"day": "2026-10-15"}))
    settings.llm_enabled = True
    assert await jobs.process_one()
    assert jobs.get(job_id)["state"] == "completed"
    memory.recap.assert_awaited_once()


@pytest.mark.anyio
async def test_all_tasks_off_keep_maintenance_without_starting_llm_loops(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enabled=False, llm_enabled=False)
    maintained = asyncio.Event()

    async def maintenance(memory):
        maintained.set()
        await asyncio.Event().wait()

    async def forbidden(memory):
        pytest.fail("A paused language model loop must not start")

    monkeypatch.setattr("omi_local.worker.maintenance", maintenance)
    for name in ("run_inbox", "run_topics", "run_insights", "run_recaps"):
        monkeypatch.setattr("omi_local.worker." + name, forbidden)
    memory = SimpleNamespace(
        settings=settings, store=SimpleNamespace(claim=lambda: pytest.fail("Paused STT claim")),
    )
    task = asyncio.create_task(run_worker(memory))
    try:
        await asyncio.wait_for(maintained.wait(), 1)
        assert not task.done()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.anyio
async def test_disabled_inference_blocks_direct_http_and_stream_before_usage(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enabled=False, llm_enabled=False)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as http:
        inference = Inference(settings, http)
        for provider in ("stt", "llm"):
            with pytest.raises(InferenceDisabled, match="off"):
                await inference.post(provider, "http://unused/v1")
        with pytest.raises(InferenceDisabled, match="off"):
            await inference.transcribe_result(b"not even parsed")
        with pytest.raises(InferenceDisabled, match="off"):
            await inference.generate_stream("question", "evidence", AsyncMock())
    assert calls == []


def test_managed_policy_quiesces_all_profile_adapters_without_saving_preferences(tmp_path):
    settings = Settings(
        data_dir=tmp_path, managed_hosting=True, worker_enabled=False, _env_file=None,
        embedding_enabled=True, embedding_model="test", embedding_revision="v1",
        embedding_dimension=2,
    )
    with TestClient(create_app(settings)) as client:
        client.post("/v1/profiles", json={"name": "Earlier archive"})
        current = client.app.state.memory
        recording = uuid4()
        current.store.ingest(recording, "omi", AT, b"audio", 1)
        current.store.complete(str(recording), "An existing invoice")
        set_id = current.retriever.set_id
        job = current.store.claim_embedding_batch(set_id, 1000)
        assert current.store.finish_embedding_batch(job, [[1, 0]])
        saved = current.store.runtime_settings()
        bound = current.inference.for_task("chat")
        response = client.post("/v1/managed-model-policy", json={
            "stt_enabled": False, "llm_enabled": False, "embedding_enabled": False,
        })
        assert response.status_code == 200
        assert set(response.json().values()) == {False}
        assert bound.settings.llm_enabled is False
        for runtime in client.app.state.profiles.runtimes.values():
            memory = runtime.memory
            for target in (
                memory.settings, memory.inference.settings, memory.retriever.settings,
                memory.retriever.embeddings.settings,
            ):
                assert not target.stt_enabled and not target.llm_enabled
                assert not target.embedding_enabled
            assert memory.retriever.set_id is None
        with current.store.connect() as db:
            assert db.execute("SELECT count(*) FROM retrieval_passages").fetchone()[0] == 1
        assert current.store.runtime_settings() == saved
        assert not (tmp_path / ".private-settings.json").exists()
        for value, code in ((True, 409), (False, 200)):
            assert client.post(
                "/v1/managed-model-policy", json={"stt_enabled": value}
            ).status_code == code
        for invalid in ({}, {"llm_enabled": 0}, {"llm_enabled": "false"}, {"llm_model": "other"}):
            assert client.post("/v1/managed-model-policy", json=invalid).status_code == 422
        assert client.get("/v1/archive/export").status_code == 200
        assert client.get("/v1/status").json()["processing_enabled"] == {
            "stt": False, "llm": False, "embedding": False,
        }


def test_managed_policy_is_unavailable_in_self_hosted_app(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        assert client.post(
            "/v1/managed-model-policy", json={"llm_enabled": False}
        ).status_code == 403


def test_llm_off_returns_clear_api_errors_while_upload_search_and_export_work(tmp_path):
    settings = Settings(
        data_dir=tmp_path, worker_enabled=False, llm_enabled=False, stt_enabled=False,
        translation_languages=["en"], _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        recording = uuid4()
        assert client.put(
            f"/v1/audio/{recording}", params={"device_id": "omi", "started_at": AT.isoformat()},
            content=wav([7] * 16000), headers={"Content-Type": "audio/wav"},
        ).status_code == 200
        memory = client.app.state.memory
        memory.store.complete(str(recording), "Søren betaler fakturaen i morgen.")
        for path, body in (
            ("/v1/chat", {"question": "fakturaen"}),
            ("/v1/recaps/2026-10-15", {}),
            (f"/v1/transcripts/{recording}/translation?language=en", {}),
        ):
            result = client.post(path, json=body)
            assert result.status_code == 409, result.text
            assert "off" in result.json()["detail"]
        events = client.post("/v1/chat/stream", json={"question": "fakturaen"})
        assert '"status": 409' in events.text and "off" in events.text
        result = client.post("/v1/search", json={"question": "fakturaen"})
        assert result.status_code == 200
        assert result.json()["sources"][0]["id"] == str(recording)
        assert client.get("/v1/archive/export").status_code == 200


def test_embedding_off_preserves_index_and_same_contract_resumes_without_reindex(tmp_path):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, embedding_enabled=True, embedding_model="test",
        embedding_revision="v1", embedding_dimension=2,
    )
    store = Store(tmp_path)
    first = Memory(store, settings, SimpleNamespace())
    recording = uuid4()
    store.ingest(recording, "omi", AT, b"audio", 1)
    store.complete(str(recording), "Existing invoice")
    set_id = first.retriever.set_id
    assert store.finish_embedding_batch(store.claim_embedding_batch(set_id, 1000), [[1, 0]])
    paused = settings.model_copy(update={"embedding_enabled": False})
    assert Memory(store, paused, SimpleNamespace()).retriever.set_id is None
    assert store.embedding_coverage(set_id) == {"total": 1, "indexed": 1}
    resumed = Memory(store, settings, SimpleNamespace())
    assert resumed.retriever.set_id == set_id
    assert store.claim_embedding_batch(set_id, 1000) is None


@pytest.mark.anyio
async def test_quiescing_during_embedding_claim_does_not_spend_or_retry(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path, _env_file=None, embedding_enabled=True, embedding_model="test",
        embedding_revision="v1", embedding_dimension=2,
    )
    store = Store(tmp_path)
    memory = Memory(store, settings, SimpleNamespace())
    recording = uuid4()
    store.ingest(recording, "omi", AT, b"audio", 1)
    store.complete(str(recording), "An invoice")
    claim = store.claim_embedding_batch

    def pause_during_claim(*args):
        job = claim(*args)
        settings.embedding_enabled = False
        return job

    monkeypatch.setattr(store, "claim_embedding_batch", pause_during_claim)
    async with httpx.AsyncClient() as http:
        assert not await index_one(store, Embeddings(settings, http), memory.retriever.set_id)
    with store.connect() as db:
        row = db.execute("SELECT state,attempts,retry_at FROM embedding_jobs").fetchone()
        assert tuple(row) == ("pending", 0, "")


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["local", "gateway"])
async def test_quiescing_during_transcription_returns_job_to_queue_without_retry(
    tmp_path, monkeypatch, source
):
    settings = Settings(data_dir=tmp_path, _env_file=None, managed_hosting=source == "gateway")
    store = Store(tmp_path)
    recording = uuid4()
    audio = wav([7] * 16000)
    store.ingest(recording, "omi", AT, audio, 1)

    def pause_during_read(_):
        if source == "local":
            settings.stt_enabled = False
        return audio

    monkeypatch.setattr(store, "read_audio", pause_during_read)
    monkeypatch.setattr("omi_local.worker.contains_speech_energy", lambda *_: True)
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda _: {"status": "unknown"})
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(409, json={"detail": "This task is turned off."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        memory = Memory(store, settings, Inference(settings, http))
        assert await process_one(memory)
        assert not await process_one(memory)
    row = store.get_audio(str(recording))
    assert row["state"] == "queued" and row["attempts"] == 0 and not row["retry_at"]
    assert not row["error"]
    assert len(calls) == int(source == "gateway")


@pytest.mark.anyio
async def test_quiescing_during_generation_keeps_job_queued_without_failure(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    memory = Memory(Store(tmp_path), settings, SimpleNamespace())
    jobs = GenerationJobs(memory)
    body = GenerationRequest(id=uuid4(), kind="recap", parameters={"day": "2026-10-15"})
    jobs.enqueue(body)

    async def pause(day):
        settings.llm_enabled = False
        settings.require_inference("llm")

    memory.recap = pause
    assert await jobs.process_one()
    assert not await jobs.process_one()
    assert jobs.get(str(body.id))["state"] == "queued"
    assert jobs.get(str(body.id))["error"] is None


def test_background_policy_is_environment_only_and_default_enabled(tmp_path, monkeypatch):
    assert Settings(data_dir=tmp_path, _env_file=None).background_processing_enabled
    monkeypatch.setenv("OMI_BACKGROUND_PROCESSING_ENABLED", "false")
    settings = Settings(data_dir=tmp_path, _env_file=None)
    assert not settings.background_processing_enabled
    (tmp_path / ".private-settings.json").write_text(
        json.dumps(
            {
                "overrides": {
                    "background_processing_enabled": True,
                }
            }
        )
    )
    with TestClient(create_app(settings)) as client:
        assert not client.app.state.memory.settings.background_processing_enabled
        assert (
            "background_processing_enabled" not in client.get("/v1/configuration").json()["values"]
        )
        response = client.patch("/v1/settings", json={"background_processing_enabled": True})
        assert response.status_code in (400, 422)


def test_recovery_loads_all_profiles_without_workers_or_job_state_changes(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    with TestClient(
        create_app(
            Settings(
                data_dir=root,
                worker_enabled=False,
                _env_file=None,
            )
        )
    ) as setup:
        profile = setup.post("/v1/profiles", json={"name": "Earlier profile"}).json()["id"]
    stores = [Store(root), Store(root / "profiles" / profile)]
    identities = []
    for index, store in enumerate(stores):
        identity = uuid4()
        identities.append(identity)
        store.ingest(identity, "omi", AT, wav([7] * 16000), 1)
        store.save_settings({"summary_language": "da" if index == 0 else "de"})
        with store.connect() as db:
            db.executescript(JOB_SCHEMA)
            db.execute("UPDATE audio SET state='transcribing' WHERE id=?", (str(identity),))
            db.execute(
                "INSERT INTO generation_jobs(id,kind,parameters,state,created_at) "
                "VALUES(?,'diary','{}','running',?)",
                (str(uuid4()), AT.isoformat()),
            )

    def forbidden(*args, **kwargs):
        pytest.fail("Opening a recovery archive must not mutate startup state")

    monkeypatch.setattr(Store, "recover", forbidden)
    monkeypatch.setattr(Store, "save_settings", forbidden)
    monkeypatch.setattr(Store, "save_decision_timezone", forbidden)
    monkeypatch.setattr(Store, "configure_embeddings", forbidden)
    settings = Settings(
        data_dir=root,
        worker_enabled=True,
        managed_hosting=True,
        background_processing_enabled=False,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        status = client.get("/v1/status")
        assert status.status_code == 200
        assert status.json()["weekly_worker_running"] is False
        assert status.json()["worker_running"] is False
        runtimes = client.app.state.profiles.runtimes
        assert set(runtimes) == {"default", profile}
        for runtime in runtimes.values():
            assert all(
                task is None
                for task in (
                    runtime.worker,
                    runtime.embedding_worker,
                    runtime.weekly_worker,
                    runtime.generation_worker,
                    runtime.notification_worker,
                )
            )
        assert client.get("/v1/archive/export").status_code == 200
        for index, store in enumerate(stores):
            assert store.get_audio(str(identities[index]))["state"] == "transcribing"
            assert store.runtime_settings()["summary_language"] == ("da" if index == 0 else "de")
            with store.connect() as db:
                assert db.execute("SELECT state FROM generation_jobs").fetchone()[0] == "running"
