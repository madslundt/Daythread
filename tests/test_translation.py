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
from omi_local.omi_api import ImportRequest, OmiAPI
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.translation import transcript_in_language, translation_chunks

DANISH = (
    "Vi skal sende forslaget til kunden i morgen. "
    "Det er vigtigt, at alle oplysninger er korrekte, før vi sender det."
)
ENGLISH = "We should review the budget before the meeting tomorrow."
JAPANESE = (
    "これは日本語で行われた会話の記録です。"
    "私たちは明日の会議で予算について話し合い、今後の計画を決める予定です。"
)
CHINESE = (
    "这是我们今天会议的完整记录。"
    "我们计划明天再次讨论项目的预算，并决定下一阶段的工作安排。"
)
TRADITIONAL_CHINESE = (
    "我們明天要開會討論公司的營運狀況，請大家提前準備相關資料，"
    "並且確認所有的數據都是正確的。"
)


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
    assert client.get(path).json() == {
        "language": "da", "translation": None, "already_in_target_language": False,
    }
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
    assert client.get(path).json() == {
        "language": "en", "translation": None, "already_in_target_language": False,
    }
    assert client.post(path).json()["translation"]["language"] == "en"
    assert len(calls) == 2
    client.patch("/v1/settings", json={"summary_language": "da"})
    assert client.get(path).json()["translation"] == first
    client.patch("/v1/settings", json={"summary_language": "auto"})
    assert client.get(path).json() == {
        "language": "auto", "translation": None, "already_in_target_language": False,
    }
    assert client.post(path).status_code == 409
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (DANISH, "da"),
        (DANISH, "da-DK"),
        ("Vi sender forslaget i morgen.", "da"),
        ("Hej, hvordan har du det?", "da"),
        (ENGLISH, "en"),
        (ENGLISH, "en-US"),
        ("Speaker 1: " + ENGLISH, "en-GB"),
        (JAPANESE, "ja"),
        (JAPANESE.replace("。", "、") * 4, "ja"),
        (CHINESE, "zh-CN"),
        (CHINESE.replace("。", "，") * 4, "zh-CN"),
        (CHINESE, "zh-TW"),
        (TRADITIONAL_CHINESE, "zh-TW"),
        (TRADITIONAL_CHINESE, "zh-CN"),
    ],
)
def test_target_language_transcripts_hide_translate_without_inference(client, text, language):
    client.patch("/v1/settings", json={"summary_language": language})
    _, path = source(client, text)
    calls = translator(client)
    assert client.get(path).json() == {
        "language": language, "translation": None, "already_in_target_language": True,
    }
    response = client.post(path)
    assert response.status_code == 409
    assert response.json()["detail"] == "Transcript is already in the summary language"
    assert calls == []


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (DANISH * 10 + "\n" + ENGLISH, "da"),
        (ENGLISH * 10 + "\n" + DANISH, "en-US"),
        ("We agreed to meet again tomorrow og så taler vi om projektet på dansk", "en"),
        ("Vi kommer til mødet i morgen but we should review the budget first", "da"),
        (JAPANESE * 10 + "\n" + ENGLISH, "ja"),
        ("私たちは明日の会議で予算について話し合いplease check the budget決める予定です", "ja"),
        (CHINESE * 10 + "\n" + ENGLISH, "zh-CN"),
        (ENGLISH * 10 + "\n" + JAPANESE, "en"),
    ],
)
def test_mixed_language_transcripts_remain_translatable_when_target_dominates(
    client, text, language
):
    client.patch("/v1/settings", json={"summary_language": language})
    _, path = source(client, text)
    calls = translator(client)
    assert client.get(path).json()["already_in_target_language"] is False
    assert calls == []
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["already_in_target_language"] is False
    assert calls


@pytest.mark.parametrize(
    "text",
    ["", "   ", "1234?!", "１２３４。、！？", "John Smith", "John James Smith", "qzxv prstq", "OK"],
)
def test_empty_and_unknown_text_is_not_assumed_to_match_target(text):
    assert not transcript_in_language(text, "en")
    assert not transcript_in_language(text, "da")
    assert not transcript_in_language(text, "ja")
    assert not transcript_in_language(text, "zh-CN")


def test_redundant_cached_translation_is_hidden_and_post_rejected_before_cache_return(client):
    key, path = source(client, DANISH)
    store = client.app.state.memory.store
    store.save_translation(store.get_audio(key), "da", "An old redundant translation", "da")
    assert store.transcript_translation(key, "da")
    calls = translator(client)
    assert client.get(path).json()["translation"] is None
    assert client.get(path).json()["already_in_target_language"] is True
    assert client.post(path).status_code == 409
    assert calls == []


def test_imported_transcript_without_stt_language_is_classified_offline(client, monkeypatch):
    async def imported():
        payload = [{
            "id": "legacy-conversation",
            "started_at": (utc_now() - timedelta(minutes=5)).isoformat(),
            "finished_at": utc_now().isoformat(),
            "transcript_segments": [{"start": 0, "end": 30, "text": DANISH}],
        }]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            settings = Settings(omi_api_key="test-import-key", _env_file=None)
            importer = OmiAPI(settings, http, client.app.state.memory.store)
            return await importer.import_page(ImportRequest())

    key = asyncio.run(imported())["items"][0]["id"]
    path = f"/v1/transcripts/{key}/translation"
    calls = translator(client)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Language classification must not access a network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_network)
    assert client.get(path).json()["already_in_target_language"] is True
    assert client.post(path).status_code == 409
    assert calls == []


def test_correction_reclassifies_current_text_without_stale_language_metadata(client):
    key, path = source(client, ENGLISH)
    calls = translator(client)
    assert client.post(path).status_code == 200
    assert client.patch(f"/v1/transcripts/{key}", json={"text": DANISH}).status_code == 200
    assert client.get(path).json()["already_in_target_language"] is True
    assert client.post(path).status_code == 409
    assert client.patch(
        f"/v1/transcripts/{key}", json={"text": DANISH + "\n" + ENGLISH}
    ).status_code == 200
    assert client.get(path).json()["already_in_target_language"] is False
    assert client.post(path).status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize("cached", [False, True])
def test_correction_during_language_classification_rejects_stale_snapshot(
    client, monkeypatch, cached
):
    key, path = source(client, ENGLISH)
    calls = translator(client)
    if cached:
        assert client.post(path).status_code == 200
        calls.clear()
    store = client.app.state.memory.store

    def classify_then_correct(text, language):
        assert text == ENGLISH
        store.correct_transcript(key, DANISH)
        return False

    monkeypatch.setattr("omi_local.translation.transcript_in_language", classify_then_correct)
    result = client.post(path)
    assert result.status_code == 409
    assert result.json()["detail"] == "Transcript changed; try again"
    assert calls == []
    assert store.transcript_translation(key, "da") is None


@pytest.mark.parametrize("cached", [False, True])
def test_summary_language_change_during_classification_rejects_stale_target(
    client, monkeypatch, cached
):
    _, path = source(client, ENGLISH)
    calls = translator(client)
    if cached:
        assert client.post(path).status_code == 200
        calls.clear()
    memory = client.app.state.memory

    def classify_then_change_language(text, language):
        assert language == "da"
        memory.settings.summary_language = "en"
        memory.store.save_settings({"summary_language": "en"})
        return False

    monkeypatch.setattr(
        "omi_local.translation.transcript_in_language", classify_then_change_language
    )
    result = client.post(path)
    assert result.status_code == 409
    assert result.json()["detail"] == "Summary language changed; try again"
    assert calls == []


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
