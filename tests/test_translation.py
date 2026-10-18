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
from omi_local.translation import (
    analyze_transcript_languages,
    transcript_in_language,
    translation_chunks,
)

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
        data_dir=tmp_path, summary_language="auto", translation_languages=["da"],
        worker_enabled=False, _env_file=None
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
        "language": "da", "available_languages": ["da"],
        "translation": None, "already_in_target_language": False, "redundant_languages": [],
        "language_analysis": analyze_transcript_languages(original["text"]),
        "recommended_language": None,
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


def test_translation_languages_select_separate_cache_independent_of_summary_language(client):
    _, path = source(client)
    calls = translator(client)
    first = client.post(path).json()["translation"]
    assert client.patch("/v1/settings", json={
        "summary_language": "en", "translation_languages": ["en", "da"]
    }).status_code == 200
    assert client.get(path).json() == {
        "language": "en", "available_languages": ["en", "da"],
        "translation": None, "already_in_target_language": False, "redundant_languages": [],
        "language_analysis": analyze_transcript_languages("Hello. Hej, hvordan går det?"),
        "recommended_language": None,
    }
    assert client.post(path).json()["translation"]["language"] == "en"
    assert len(calls) == 2
    client.patch("/v1/settings", json={"summary_language": "auto"})
    assert client.get(path, params={"language": "DA"}).json()["translation"] == first
    assert client.post(path, params={"language": "da"}).json()["translation"] == first
    client.patch("/v1/settings", json={"translation_languages": []})
    result = client.get(path).json()
    assert result["language"] == ""
    assert result["available_languages"] == []
    assert result["translation"] is None
    assert result["recommended_language"] is None
    assert client.post(path, params={"language": "da"}).status_code == 422
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
def test_target_language_transcripts_return_same_language_result_without_inference(
    client, text, language
):
    client.patch("/v1/settings", json={"translation_languages": [language]})
    _, path = source(client, text)
    calls = translator(client)
    assert client.get(path).json() == {
        "language": language.lower(), "available_languages": [language.lower()],
        "translation": None, "already_in_target_language": True,
        "redundant_languages": [language.lower()],
        "language_analysis": analyze_transcript_languages(text),
        "recommended_language": None,
    }
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["already_in_target_language"] is True
    assert calls == []


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (DANISH * 3 + "\n" + ENGLISH, "da"),
        (ENGLISH * 10 + "\n" + DANISH, "en-US"),
        ("We agreed to meet again tomorrow og så taler vi om projektet på dansk", "en"),
        ("Vi kommer til mødet i morgen but we should review the budget first", "da"),
        (JAPANESE * 3 + "\n" + ENGLISH, "ja"),
        ("私たちは明日の会議で予算について話し合いplease check the budget決める予定です", "ja"),
        (CHINESE * 3 + "\n" + ENGLISH, "zh-CN"),
        (ENGLISH * 3 + "\n" + JAPANESE, "en"),
    ],
)
def test_mixed_language_transcripts_remain_translatable_when_target_dominates(
    client, text, language
):
    client.patch("/v1/settings", json={"translation_languages": [language]})
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


def test_redundant_cached_translation_is_hidden_and_post_returns_same_language_result(client):
    key, path = source(client, DANISH)
    store = client.app.state.memory.store
    store.save_translation(store.get_audio(key), "da", "An old redundant translation", ["da"])
    assert store.transcript_translation(key, "da")
    calls = translator(client)
    assert client.get(path).json()["translation"] is None
    assert client.get(path).json()["already_in_target_language"] is True
    assert client.post(path).status_code == 200
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
    assert client.post(path).status_code == 200
    assert calls == []


def test_correction_reclassifies_current_text_without_stale_language_metadata(client):
    key, path = source(client, ENGLISH)
    calls = translator(client)
    assert client.post(path).status_code == 200
    assert client.patch(f"/v1/transcripts/{key}", json={"text": DANISH}).status_code == 200
    assert client.get(path).json()["already_in_target_language"] is True
    assert client.post(path).status_code == 200
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

    def classify_then_correct(text):
        assert text == ENGLISH
        store.correct_transcript(key, DANISH)
        return analyze_transcript_languages(text)

    monkeypatch.setattr("omi_local.translation.analyze_transcript_languages", classify_then_correct)
    result = client.post(path)
    assert result.status_code == 409
    assert result.json()["detail"] == "Transcript changed; try again"
    assert calls == []
    assert store.transcript_translation(key, "da") is None


@pytest.mark.parametrize("cached", [False, True])
def test_disabled_language_during_classification_rejects_chosen_target(
    client, monkeypatch, cached
):
    _, path = source(client, ENGLISH)
    calls = translator(client)
    if cached:
        assert client.post(path).status_code == 200
        calls.clear()
    memory = client.app.state.memory

    def classify_then_change_language(text):
        assert text == ENGLISH
        memory.settings.translation_languages = ["en"]
        memory.store.save_settings({"translation_languages": ["en"]})
        return analyze_transcript_languages(text)

    monkeypatch.setattr(
        "omi_local.translation.analyze_transcript_languages", classify_then_change_language
    )
    result = client.post(path)
    assert result.status_code == 409
    assert result.json()["detail"] == (
        "Translation languages changed; choose a language and try again"
    )
    assert not calls


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
            store.save_settings({"translation_languages": ["en"]})

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


@pytest.mark.parametrize("language", ["en", "da", "auto"])
def test_default_translation_language_does_not_follow_summary_language(tmp_path, language):
    settings = Settings(
        data_dir=tmp_path, summary_language=language, worker_enabled=False, _env_file=None
    )
    with TestClient(create_app(settings)) as api:
        _, path = source(api)
        calls = translator(api)
        assert api.get(path).json()["language"] == "en"
        assert api.post(path).status_code == 200
        assert calls[0][1] == "en"


@pytest.mark.parametrize("value", [
    ["auto"], ["AUTO"], ["English"], ["en_US"], ["en-"], [" en"], ["e"], [""],
    ["en/da"], [123], "en", None,
])
def test_invalid_whitelist_is_rejected_without_mutation(client, value):
    result = client.patch("/v1/settings", json={"translation_languages": value})
    assert result.status_code == 422
    assert client.get("/v1/settings").json()["translation_languages"] == ["da"]


def test_whitelist_is_normalized_persisted_and_shared_globally(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as api:
        other = api.post("/v1/profiles", json={"name": "Other"}).json()["id"]
        # Start the other runtime before editing Settings through it.
        headers = {"X-Omi-Profile": other}
        assert api.get("/v1/transcripts", headers=headers).status_code == 200
        result = api.patch("/v1/settings", headers=headers, json={
            "translation_languages": ["EN-US", "da", "en-us", "DA", "zh-Hant"],
        })
        assert result.status_code == 200
        expected = ["en-us", "da", "zh-hant"]
        assert result.json()["translation_languages"] == expected
        for runtime in api.app.state.profiles.runtimes.values():
            assert runtime.memory.settings.translation_languages == expected
            assert runtime.memory.store.runtime_settings()["translation_languages"] == expected
        # The full server configuration also exposes and validates the same preference.
        config = api.get("/v1/configuration").json()
        assert config["values"]["translation_languages"] == expected
    with TestClient(create_app(settings)) as reopened:
        assert reopened.get("/v1/settings").json()["translation_languages"] == expected


@pytest.mark.parametrize("language", ["xx", "auto", "en_US", ""])
def test_explicit_invalid_target_never_generates(client, language):
    _, path = source(client)
    calls = translator(client)
    for method in (client.get, client.post):
        assert method(path, params={"language": language}).status_code == 422
    assert calls == []


@pytest.mark.parametrize("language", ["fr", "es", "zh-hant"])
def test_target_must_be_enabled_in_settings_before_translation(client, language):
    _, path = source(client)
    calls = translator(client)
    client.patch("/v1/settings", json={"translation_languages": []})
    response = client.post(path, params={"language": language})
    assert response.status_code == 422
    assert calls == []
    assert client.patch(
        "/v1/settings", json={"translation_languages": [language]},
    ).status_code == 200
    assert client.post(path, params={"language": language}).status_code == 200
    assert calls[0][1] == language
    assert client.get(path, params={"language": language}).json()["translation"]


@pytest.mark.parametrize("cached", [False, True])
def test_summary_language_change_during_classification_keeps_target(client, monkeypatch, cached):
    _, path = source(client, ENGLISH)
    calls = translator(client)
    if cached:
        assert client.post(path).status_code == 200
        calls.clear()
    memory = client.app.state.memory

    def classify_then_change_summary(text):
        memory.settings.summary_language = "en"
        memory.store.save_settings({"summary_language": "en"})
        return analyze_transcript_languages(text)

    monkeypatch.setattr(
        "omi_local.translation.analyze_transcript_languages", classify_then_change_summary
    )
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["language"] == "da"
    assert len(calls) == (0 if cached else 1)


def test_summary_and_whitelist_order_changes_during_inference_keep_enabled_target(client):
    key, path = source(client)
    memory = client.app.state.memory

    def change():
        memory.settings.summary_language = "en"
        memory.settings.translation_languages = ["en", "da"]
        memory.store.save_settings({
            "summary_language": "en", "translation_languages": ["en", "da"]
        })

    calls = translator(client, change)
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["language"] == "da"
    assert response.json()["available_languages"] == ["en", "da"]
    assert calls[0][1] == "da"
    assert memory.store.transcript_translation(key, "da")


@pytest.mark.parametrize("repetitions, redundant", [(8, False), (9, True), (10, True)])
def test_language_coverage_threshold_counts_letters_not_detector_confidence(
    monkeypatch, repetitions, redundant
):
    from types import SimpleNamespace

    class Detector:
        def append(self, text):
            self.language = "en" if text.startswith("English") else "da"

        def get_probabilities(self):
            return [SimpleNamespace(lang=self.language, prob=1.0)]

    monkeypatch.setattr("omi_local.translation.language_profiles", lambda: SimpleNamespace(
        get_lang_list=lambda: ["en", "da"], create=Detector,
    ))
    # Equal letter counts: 8/9 < 90%, 9/10 = 90%, 10/11 > 90%.
    english = "English words spoken here."
    danish = "Danske sproget tales heri."
    assert sum(map(str.isalpha, english)) == sum(map(str.isalpha, danish))
    assert transcript_in_language((english + "\n") * repetitions + danish, "en") is redundant


@pytest.mark.parametrize("text, language", [(DANISH, "da"), (ENGLISH, "en"),
                                           (JAPANESE, "ja"), (CHINESE, "zh-CN")])
def test_small_minority_does_not_offer_redundant_translation(client, text, language):
    client.patch("/v1/settings", json={"translation_languages": [language, "de"]})
    _, path = source(client, (text + "\n") * 40 + "Hello.")
    calls = translator(client)
    result = client.get(path).json()
    assert result["redundant_languages"] == [language.lower()]
    assert client.post(path).json()["already_in_target_language"] is True
    assert not calls


def test_all_targets_are_checked_even_when_first_target_does_not_match(client):
    client.patch("/v1/settings", json={"translation_languages": ["de", "en", "en-US", "da"]})
    _, path = source(client, ENGLISH)
    calls = translator(client)
    for target in [None, "de", "en", "en-US", "da"]:
        params = {"language": target} if target else {}
        assert client.get(path, params=params).json()["redundant_languages"] == ["en", "en-us"]
    assert not calls


@pytest.mark.parametrize("text, language", [
    (CHINESE.replace("。", "") * 20 + "please check the budget", "zh-CN"),
    (ENGLISH.replace(".", " ") * 20 + "明天的會議", "en"),
])
def test_small_minority_in_another_script_without_sentence_boundaries(text, language):
    assert transcript_in_language(text, language)
