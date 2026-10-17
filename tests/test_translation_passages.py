"""Translations use exact versioned source passages, separately from full transcripts."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_translation import DANISH, ENGLISH, source, translator

from omi_local.api import create_app
from omi_local.domain import utc_now
from omi_local.settings import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path, translation_languages=["da"], worker_enabled=False, _env_file=None
    )
    with TestClient(create_app(settings)) as session:
        yield session


def passage(client, text=DANISH + " 🚗 " + ENGLISH):
    key, path = source(client, text)
    version = client.app.state.memory.store.get_audio(key)["transcript_version"]
    start = text.index(ENGLISH)
    params = {"start_offset": start, "end_offset": len(text), "transcript_version": version}
    return key, path, params


def test_passage_translation_is_exact_cached_and_separate_from_full_transcript(client):
    key, path, params = passage(client)
    calls = translator(client)
    assert client.get(path, params=params).json()["translation"] is None
    result = client.post(path, params=params)
    assert result.status_code == 200, result.text
    translation = result.json()["translation"]
    assert calls == [(ENGLISH, "da")]
    assert client.get(path, params=params).json()["translation"] == translation
    assert client.post(path, params=params).json()["translation"] == translation
    assert len(calls) == 1
    assert client.get(path).json()["translation"] is None
    client.post(path)
    assert len(calls) == 2
    # A different passage has independent language detection and never gets Tesla's translation.
    params.update(start_offset=0, end_offset=len(DANISH))
    result = client.get(path, params=params).json()
    assert result["already_in_target_language"] is True
    assert result["translation"] is None
    assert client.post(path, params=params).status_code == 200


@pytest.mark.parametrize("changes", [
    {"start_offset": -1}, {"end_offset": 10000}, {"end_offset": 0},
    {"start_offset": 10000}, {"end_offset": None}, {"start_offset": None},
    {"transcript_version": None}, {"transcript_version": "stale"},
])
def test_invalid_or_stale_passage_never_generates(client, changes):
    _, path, params = passage(client)
    params.update(changes)
    params = {key: value for key, value in params.items() if value is not None}
    calls = translator(client)
    for method in (client.get, client.post):
        assert method(path, params=params).status_code in {409, 422}
    assert calls == []


@pytest.mark.parametrize("action", ["correct", "delete", "expire", "audio_only"])
def test_passage_cache_follows_transcript_lifetime(client, action):
    key, path, params = passage(client)
    translator(client)
    assert client.post(path, params=params).status_code == 200
    store = client.app.state.memory.store
    if action == "correct":
        store.correct_transcript(key, "Changed text")
        assert client.get(path, params=params).status_code == 409
    elif action == "delete":
        store.delete_audio(key)
        assert client.get(path, params=params).status_code == 404
    else:
        store.apply_retention(1 if action == "audio_only" else 0,
                              1 if action == "expire" else 0, utc_now() + timedelta(days=2))
    with store.connect() as db:
        count = db.execute("SELECT count(*) FROM transcript_passage_translations").fetchone()[0]
        assert count == (action == "audio_only")


def test_inflight_passage_correction_is_rejected(client):
    key, path, params = passage(client)
    translator(client, lambda: client.app.state.memory.store.correct_transcript(key, "New text"))
    assert client.post(path, params=params).status_code == 409
    with client.app.state.memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM transcript_passage_translations").fetchone()[0] == 0


def test_passage_language_query_selects_independent_cache(client):
    _, path, params = passage(client)
    client.patch("/v1/settings", json={"translation_languages": ["da", "fr"]})
    calls = translator(client)
    danish = client.post(path, params=params).json()["translation"]
    french_params = params | {"language": "FR"}
    response = client.post(path, params=french_params)
    assert response.status_code == 200
    french = response.json()["translation"]
    assert french["language"] == "fr"
    assert response.json()["available_languages"] == ["da", "fr"]
    assert calls == [(ENGLISH, "da"), (ENGLISH, "fr")]
    assert client.get(path, params=french_params).json()["translation"] == french
    assert client.get(path, params=params).json()["translation"] == danish
