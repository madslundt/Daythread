"""Shared app/web configuration with temporary archives and no real inference calls."""

import json
import shutil
import stat
import zipfile

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.configuration import RETIRED_FIELDS
from omi_local.settings import DEPLOYMENT_FIELDS, Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "archive",
        worker_enabled=False,
        _env_file=None,
        inference_token="shared-secret",
        stt_api_key="speech-secret",
        llm_api_key="language-secret",
        omi_api_key="omi-secret",
        stt_routes={"da": {"model": "danish", "token": "route-secret"}},
    )


def save(client, changes, revision=None):
    revision = revision or client.get("/v1/configuration").json()["revision"]
    return client.patch("/v1/configuration", json={"revision": revision, "changes": changes})


def test_every_setting_is_editable_and_read_responses_never_reveal_keys(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/configuration")
        value = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert {field["key"] for field in value["fields"]} == (
            set(Settings.model_fields) - DEPLOYMENT_FIELDS - RETIRED_FIELDS
        )
        assert value["secrets"]["inference_token"]
        assert value["secrets"]["stt_routes.da.token"]
        assert value["values"]["inference_token"] is None
        assert "token" not in value["values"]["stt_routes"]["da"]
        for key in ("stt_api_key", "llm_api_key"):
            assert value["secrets"][key]
            assert value["values"][key] is None
            field = next(field for field in value["fields"] if field["key"] == key)
            assert field["kind"] == "secret" and field["nullable"]
        for secret in (
            "shared-secret",
            "omi-secret",
            "route-secret",
            "speech-secret",
            "language-secret",
        ):
            assert secret not in response.text
            assert secret not in client.get("/v1/settings").text


def test_model_routes_keys_and_preferences_apply_live_and_survive_restart(settings):
    with TestClient(create_app(settings)) as client:
        result = save(
            client,
            {
                "stt_model": "multilingual",
                "stt_base_url": "http://stt.local/v1/",
                "stt_routes": {"da": {"model": "new-danish"}, "en": {"model": "english"}},
                "llm_routes": {"chat": {"model": "fast", "token": "chat-secret"}},
                "inference_token": "replacement-secret",
                "omi_api_key": "new-omi-secret",
                "summary_language": "da",
                "recap_hour": 20,
            },
        )
        assert result.status_code == 200
        assert "replacement-secret" not in result.text
        assert result.json()["pending_restart"] == []
        active = client.app.state.memory.settings
        assert active.stt_route("da").model == "new-danish"
        assert active.stt_route("da").token.get_secret_value() == "route-secret"
        assert active.stt_route("en").token.get_secret_value() == "speech-secret"
        assert active.llm_route("chat").token.get_secret_value() == "chat-secret"
        assert client.app.state.omi.key == "new-omi-secret"
        assert client.get("/v1/settings").json()["summary_language"] == "da"
        assert client.app.state.memory.store.runtime_settings() == {
            "summary_language": "da",
            "recap_hour": 20,
        }
        private_file = settings.data_dir / ".private-settings.json"
        assert stat.S_IMODE(private_file.stat().st_mode) == 0o600
    with TestClient(create_app(settings)) as client:
        active = client.app.state.memory.settings
        assert active.stt_model == "multilingual"
        assert active.stt_base_url == "http://stt.local/v1"
        assert active.summary_language == "da"
        assert active.inference_token.get_secret_value() == "replacement-secret"
        assert active.stt_route("da").token.get_secret_value() == "route-secret"


def test_transcription_request_duration_applies_live_and_survives_restart(settings):
    with TestClient(create_app(settings)) as client:
        view = client.get("/v1/configuration").json()
        assert view["values"]["stt_chunk_seconds"] == 30
        field = next(item for item in view["fields"] if item["key"] == "stt_chunk_seconds")
        assert field["group"] == "Transcription"
        assert field["minimum"] == 1 and field["maximum"] == 120
        assert not field["restart_required"]
        result = save(client, {"stt_chunk_seconds": 12})
        assert result.status_code == 200
        assert result.json()["pending_restart"] == []
        assert client.app.state.memory.inference.settings.stt_chunk_seconds == 12
        for invalid in (0, 121, 1.5):
            assert save(client, {"stt_chunk_seconds": invalid}).status_code == 422
        assert client.app.state.memory.inference.settings.stt_chunk_seconds == 12
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/configuration").json()["values"]["stt_chunk_seconds"] == 12
        assert client.app.state.memory.inference.settings.stt_chunk_seconds == 12


def test_route_keys_can_be_replaced_disabled_inherited_and_removed(settings):
    with TestClient(create_app(settings)) as client:
        for token, expected in [
            ("new-route-secret", "new-route-secret"),
            ("", ""),
            (None, "speech-secret"),
        ]:
            assert (
                save(
                    client, {"stt_routes": {"da": {"model": "danish", "token": token}}}
                ).status_code
                == 200
            )
            assert (
                client.app.state.memory.settings.stt_route("da").token.get_secret_value()
                == expected
            )
        assert save(client, {"stt_routes": {}}).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.app.state.memory.settings.stt_routes == {}
        assert save(client, {"omi_api_key": ""}).status_code == 200
        assert not client.get("/v1/integrations/omi").json()["configured"]


def test_restart_required_changes_are_staged_together(settings):
    changes = {
        "embedding_enabled": True,
        "embedding_model": "embed",
        "embedding_revision": "v1",
        "embedding_dimension": 2,
        "embedding_token": "embed-secret",
    }
    with TestClient(create_app(settings)) as client:
        result = save(client, changes)
        assert result.status_code == 200
        assert set(result.json()["pending_restart"]) == set(changes)
        assert result.json()["values"]["embedding_enabled"]
        assert not client.app.state.memory.settings.embedding_enabled
        assert save(client, {"stt_model": "other"}).status_code == 200
        assert set(client.get("/v1/configuration").json()["pending_restart"]) == set(changes)
    with TestClient(create_app(settings)) as client:
        assert client.app.state.memory.settings.embedding_enabled
        assert client.app.state.memory.retriever.embeddings.settings.embedding_model == "embed"
        assert client.get("/v1/configuration").json()["pending_restart"] == []


def test_archive_path_change_selects_new_archive_on_restart_without_moving_old(settings, tmp_path):
    target = tmp_path / "another-archive"
    with TestClient(create_app(settings)) as client:
        result = save(client, {"data_dir": str(target)})
        assert result.status_code == 200
        assert result.json()["pending_restart"] == ["data_dir"]
        assert client.app.state.memory.store.root == settings.data_dir
    with TestClient(create_app(settings)) as client:
        assert client.app.state.memory.store.root == target
        assert settings.data_dir.exists()


def test_conflicting_app_web_and_legacy_settings_edits_are_rejected(settings):
    with TestClient(create_app(settings)) as client:
        revision = client.get("/v1/configuration").json()["revision"]
        assert save(client, {"stt_model": "first"}, revision).status_code == 200
        assert save(client, {"stt_model": "stale"}, revision).status_code == 409
        revision = client.get("/v1/configuration").json()["revision"]
        assert client.patch("/v1/settings", json={"summary_language": "en"}).status_code == 200
        assert save(client, {"stt_model": "stale"}, revision).status_code == 409
        assert client.app.state.memory.settings.stt_model == "first"


@pytest.mark.parametrize(
    "changes",
    [
        {"stt_base_url": "http://example.com/v1"},
        {"llm_routes": {"chat": {"base_url": "http://user:secret@localhost/v1"}}},
        {"embedding_enabled": True},
        {"audio_forwarding_token": "invalid-private-key"},
        {"stt_routes": {"da": {"token": 123}}},
        {"unknown": "private-value"},
    ],
)
def test_invalid_changes_are_not_saved_or_echoed(settings, changes):
    with TestClient(create_app(settings)) as client:
        before = client.get("/v1/configuration").json()
        result = save(client, changes)
        assert result.status_code == 422
        assert "invalid-private-key" not in result.text
        assert "private-value" not in result.text
        assert client.get("/v1/configuration").json() == before
        assert not (settings.data_dir / ".private-settings.json").exists()


def test_private_keys_are_excluded_from_archive_export(settings):
    with TestClient(create_app(settings)) as client:
        assert (
            save(
                client,
                {
                    "inference_token": "new-private-key",
                    "stt_api_key": "private-speech-key",
                    "llm_api_key": "private-language-key",
                },
            ).status_code
            == 200
        )
        store = client.app.state.memory.store
        assert "new-private-key" not in json.dumps(store.runtime_settings())
        archive_path = store.export_archive()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                assert not any("private-settings" in name for name in archive.namelist())
                assert b"new-private-key" not in archive.read("omi.sqlite3")
                assert b"private-speech-key" not in archive.read("omi.sqlite3")
                assert b"private-language-key" not in archive.read("omi.sqlite3")
        finally:
            shutil.rmtree(archive_path.parent)


@pytest.mark.parametrize("provider,route", [("stt", "en"), ("llm", "summary")])
def test_provider_api_keys_apply_live_preserve_and_survive_restart(settings, provider, route):
    key = f"{provider}_api_key"
    with TestClient(create_app(settings)) as client:
        result = save(
            client,
            {
                key: "replacement-provider-secret",
                f"{provider}_base_url": "https://models.example.com/v1/",
            },
        )
        assert result.status_code == 200
        assert result.json()["pending_restart"] == []
        assert "replacement-provider-secret" not in result.text
        assert save(client, {f"{provider}_model": "new-model"}).status_code == 200
    with TestClient(create_app(settings)) as client:

        def resolve(name):
            return getattr(client.app.state.memory.inference.settings, f"{provider}_route")(name)

        assert resolve(route).base_url == "https://models.example.com/v1"
        assert resolve(route).token.get_secret_value() == "replacement-provider-secret"
        assert save(client, {key: ""}).status_code == 200
        assert resolve(route).token.get_secret_value() == ""
        assert save(client, {key: None}).status_code == 200
        assert resolve(route).token.get_secret_value() == "shared-secret"
