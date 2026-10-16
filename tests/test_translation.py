"""Translation is explicit, cached separately, and subject to source lifetime/version checks."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import utc_now
from omi_local.inference import Inference, InferenceTruncated
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.translation import translation_chunks


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path, summary_language="da", worker_enabled=False, _env_file=None
    )
    with TestClient(create_app(settings)) as client:
        yield client


def source(client, text="Hello. Hej, hvordan går det?"):
    memory = client.app.state.memory
    key = str(uuid4())
    memory.store.ingest(key, "test", utc_now(), b"test", 30)
    memory.store.complete(key, text, segments=[{"start": 0, "end": 30, "text": text}])
    return key, f"/v1/transcripts/{key}/translation"


def translator(client, callback=None):
    calls = []

    async def translate(text, language):
        calls.append((text, language))
        if callback:
            callback()
        return "Hej. Hej, hvordan går det?"

    client.app.state.memory.inference.translate = translate
    return calls


def test_reads_never_generate_and_explicit_translation_preserves_evidence(client):
    key, path = source(client)
    calls = translator(client)
    original = client.get(f"/v1/transcripts/{key}").json()
    assert client.get(path).json() == {"language": "da", "translation": None}
    assert calls == []
    translated = client.post(path).json()["translation"]
    assert translated["text"] == "Hej. Hej, hvordan går det?"
    assert translated["transcript_version"] == original["transcript_version"]
    assert calls == [(original["text"], "da")]
    assert client.get(f"/v1/transcripts/{key}").json() == original
    assert client.get(f"/v1/transcripts/{key}/revisions").json()["items"] == []
    assert client.get("/v1/transcripts?q=Hello").json()["total"] == 1
    assert client.get(path).json()["translation"] == translated
    assert client.post(path).json()["translation"] == translated
    assert len(calls) == 1
    # Persisted in the archive rather than in the client or process memory.
    reopened = Store(client.app.state.memory.store.root)
    assert reopened.transcript_translation(key, "DA") == translated


def test_language_changes_select_separate_cache_and_auto_requires_target(client):
    _, path = source(client)
    calls = translator(client)
    first = client.post(path).json()["translation"]
    assert client.patch("/v1/settings", json={"summary_language": "en"}).status_code == 200
    assert client.get(path).json() == {"language": "en", "translation": None}
    assert client.post(path).json()["translation"]["language"] == "en"
    assert len(calls) == 2
    client.patch("/v1/settings", json={"summary_language": "da"})
    assert client.get(path).json()["translation"] == first
    client.patch("/v1/settings", json={"summary_language": "auto"})
    assert client.get(path).json() == {"language": "auto", "translation": None}
    assert client.post(path).status_code == 409
    assert len(calls) == 2


@pytest.mark.parametrize("action", ["correct", "delete", "expire", "language"])
def test_inflight_changes_reject_translation_without_saving_stale_text(client, action):
    key, path = source(client)
    store = client.app.state.memory.store

    def change():
        if action == "correct":
            store.correct_transcript(key, "Corrected discussion")
        elif action == "delete":
            store.delete_audio(key)
        elif action == "expire":
            store.apply_retention(0, 1, utc_now() + timedelta(days=2))
        else:
            store.save_settings({"summary_language": "en"})

    translator(client, change)
    assert client.post(path).status_code == 409
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM transcript_translations").fetchone()[0] == 0


def test_correction_removes_cache_and_new_translation_uses_original_correction(client):
    key, path = source(client)
    calls = translator(client)
    client.post(path)
    client.patch(f"/v1/transcripts/{key}", json={"text": "Changed. Ændret."})
    assert client.get(path).json()["translation"] is None
    client.post(path)
    assert calls[-1] == ("Changed. Ændret.", "da")


@pytest.mark.parametrize("action", ["delete", "expire", "audio_only"])
def test_translation_lifetime_follows_transcript(client, action):
    key, path = source(client)
    translator(client)
    client.post(path)
    store = client.app.state.memory.store
    if action == "delete":
        store.delete_audio(key)
        assert client.get(path).status_code == 404
    else:
        store.apply_retention(
            1 if action == "audio_only" else 0,
            1 if action == "expire" else 0,
            utc_now() + timedelta(days=2),
        )
        assert bool(client.get(path).json()["translation"]) == (action == "audio_only")
    with store.connect() as db:
        count = db.execute("SELECT count(*) FROM transcript_translations").fetchone()[0]
        assert count == (1 if action == "audio_only" else 0)


def test_failed_or_truncated_translation_is_safe_to_retry(client):
    key, path = source(client)

    async def fail(*args):
        raise InferenceTruncated("secret upstream body")

    client.app.state.memory.inference.translate = fail
    result = client.post(path)
    assert result.status_code == 502
    assert "secret" not in result.text
    assert client.get(path).json()["translation"] is None
    assert client.get(f"/v1/transcripts/{key}").json()["text"] == "Hello. Hej, hvordan går det?"
    translator(client)
    assert client.post(path).status_code == 200


def test_empty_pending_and_unknown_transcripts_do_not_generate(client):
    calls = translator(client)
    key, path = source(client, "  ")
    assert client.post(path).status_code == 409
    store = client.app.state.memory.store
    key = uuid4()
    store.ingest(key, "test", utc_now(), b"test", 30)
    assert client.post(f"/v1/transcripts/{key}/translation").status_code == 409
    assert client.post(f"/v1/transcripts/{uuid4()}/translation").status_code == 404
    assert not calls


def test_translation_is_profile_scoped(client):
    key, path = source(client)
    calls = translator(client)
    assert client.post(path).status_code == 200
    other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    for method in (client.get, client.post):
        assert method(path, headers={"X-Omi-Profile": other}).status_code == 404
    assert len(calls) == 1


def test_chunking_keeps_all_text_and_translates_every_chunk(client):
    text = "Hej. Hello, this is mixed speech.\n" * 500
    chunks = translation_chunks(text)
    assert "".join(chunks) == text
    assert max(map(len, chunks)) <= 4000
    key, path = source(client, text)
    calls = translator(client)
    assert client.post(path).status_code == 200
    assert "".join(text for text, _ in calls) == text
    assert client.get(f"/v1/transcripts/{key}").json()["text"] == text


def test_translation_inference_uses_summary_route_and_translation_prompt(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hej"}}]})

    async def run():
        settings = Settings(
            data_dir=tmp_path, _env_file=None,
            llm_routes={"summary": {"model": "translation-model"}},
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await Inference(settings, http).translate("Hello", "da")

    assert asyncio.run(run()) == "Hej"
    assert calls[0]["model"] == "translation-model"
    messages = calls[0]["messages"]
    assert "translat" in messages[0]["content"].lower()
    task = json.loads(messages[1]["content"])
    assert "da" in task["task"]
    assert task["evidence"] == "Hello"


def test_concurrent_clicks_coalesce_and_partial_failure_is_not_saved(client):
    key, path = source(client, "Hello. " * 1000)
    memory = client.app.state.memory
    calls = []

    async def translate(text, language):
        calls.append(text)
        await asyncio.sleep(0.01)
        return "Hej"

    memory.inference.translate = translate
    # Use the ASGI transport so both POSTs share the app's event loop and lock.
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                     base_url="http://test") as http:
            return await asyncio.gather(http.post(path), http.post(path))

    responses = asyncio.run(run())
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert len(calls) == len(translation_chunks("Hello. " * 1000))

    memory.store.correct_transcript(key, "Changed. " * 1000)
    calls.clear()

    async def fail_later(text, language):
        calls.append(text)
        if len(calls) > 1:
            raise InferenceTruncated("truncated second chunk")
        return "Ændret"

    memory.inference.translate = fail_later
    assert client.post(path).status_code == 502
    assert client.get(path).json()["translation"] is None
