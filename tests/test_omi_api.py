import asyncio
import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.omi_api import ENDPOINT
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
KEY = "test-personal-key"


def conversation(key="upstream-id"):
    return {
        "id": key,
        "started_at": AT.isoformat(),
        "finished_at": (AT + timedelta(minutes=5)).isoformat(),
        "transcript_segments": [
            {"start": 10, "end": 12, "text": "The kitchen arrives Friday", "speaker_name": "Ada"},
            {"start": 13, "end": 15, "text": "I will be home", "speaker_id": 2},
        ],
        "structured": {"overview": "This summary must never be used as evidence"},
    }


@pytest.fixture
def connected(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, omi_api_key=KEY, _env_file=None)
    with TestClient(create_app(settings)) as client:
        remotes = []

        def remote(handler):
            http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            remotes.append(http)
            client.app.state.omi.client = http

        remote(lambda request: httpx.Response(200, json=[conversation()]))
        yield client, remote
        for http in remotes:
            asyncio.run(http.aclose())


def test_real_developer_wire_contract_and_searchable_transcript(connected):
    client, remote = connected

    def handler(request):
        assert str(request.url).split("?")[0] == ENDPOINT
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.url.params["include_transcript"] == "true"
        assert request.url.params["limit"] == "1"
        assert request.url.params["offset"] == "5"
        assert request.url.params["start_date"].startswith("2026-10-14")
        assert request.url.params["end_date"].startswith("2026-10-16")
        return httpx.Response(200, json=[conversation()])

    remote(handler)
    result = client.post(
        "/v1/integrations/omi/import",
        json={
            "start_date": "2026-10-14T00:00:00Z",
            "end_date": "2026-10-16T00:00:00Z",
            "limit": 1,
            "offset": 5,
        },
    ).json()
    assert (result["imported"], result["existing"], result["skipped"]) == (1, 0, 0)
    assert result["has_more"] and result["next_offset"] == 6
    source = result["items"][0]
    assert UUID(source["id"])
    assert source["source_kind"] == "omi_api" and source["source_id"] == "upstream-id"
    assert source["text"] == "Ada: The kitchen arrives Friday\nSpeaker 2: I will be home"
    assert source["state"] == "done" and source["audio_available"] is False
    assert client.get(f"/v1/audio/{source['id']}").status_code == 404
    assert client.get("/v1/transcripts?q=kitchen").json()["total"] == 1
    assert client.app.state.memory.store.search("kitchen", 10)[0]["id"] == source["id"]
    window = client.post(
        "/v1/rewind",
        json={
            "minutes": 10,
            "end": "2026-10-15T12:06:00Z",
            "summarize": False,
        },
    ).json()
    assert len(window["sources"]) == 1 and len(window["audio_gaps"]) == 1
    assert client.get("/v1/status").json()["audio"] == {"done": 1}


def test_retries_and_restart_deduplicate_and_preserve_local_corrections(connected):
    client, _ = connected
    source = client.post("/v1/integrations/omi/import", json={}).json()["items"][0]
    assert (
        client.patch(f"/v1/transcripts/{source['id']}", json={"text": "Corrected"}).status_code
        == 200
    )
    client.app.state.omi.store = Store(client.app.state.memory.store.root)
    result = client.post("/v1/integrations/omi/import", json={}).json()
    assert result["existing"] == 1 and result["imported"] == 0
    assert result["items"][0]["text"] == "Corrected"
    assert not result["has_more"]


def test_null_dates_or_empty_transcripts_wait_for_finished_source(connected):
    client, remote = connected
    remote(
        lambda request: httpx.Response(
            200,
            json=[
                conversation("ongoing") | {"finished_at": None},
                conversation("missing-start") | {"started_at": None},
                conversation("empty") | {"transcript_segments": []},
            ],
        )
    )
    result = client.post("/v1/integrations/omi/import", json={}).json()
    assert result["skipped"] == 3 and result["imported"] == 0
    assert client.get("/v1/transcripts").json()["total"] == 0


@pytest.mark.parametrize("payload", [[], [conversation()]])
def test_pagination_headers_continue_past_filtered_or_empty_pages(connected, payload):
    client, remote = connected
    remote(
        lambda request: httpx.Response(
            200,
            json=payload,
            headers={
                "X-Omi-Has-More": "true",
                "X-Omi-Next-Offset": "50",
            },
        )
    )
    result = client.post("/v1/integrations/omi/import", json={"offset": 25}).json()
    assert result["has_more"] is True and result["next_offset"] == 50
    assert result["imported"] == len(payload)


def test_full_final_page_honors_explicit_end_of_history(connected):
    client, remote = connected
    remote(
        lambda request: httpx.Response(
            200, json=[conversation()], headers={"X-Omi-Has-More": "false"}
        )
    )
    result = client.post("/v1/integrations/omi/import", json={"limit": 1}).json()
    assert result["has_more"] is False


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Omi-Has-More": "maybe"},
        {"X-Omi-Has-More": "true"},
        {"X-Omi-Has-More": "true", "X-Omi-Next-Offset": "0"},
        {"X-Omi-Has-More": "true", "X-Omi-Next-Offset": "garbage"},
    ],
)
def test_invalid_pagination_does_not_write_a_page(connected, headers):
    client, remote = connected
    remote(lambda request: httpx.Response(200, json=[conversation()], headers=headers))
    assert client.post("/v1/integrations/omi/import", json={}).status_code == 502
    assert client.get("/v1/transcripts").json()["total"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"conversations": []},
        [conversation() | {"id": ""}],
        [conversation() | {"started_at": "2026-10-15T12:00:00"}],
        [conversation() | {"finished_at": "2026-10-15T11:00:00Z"}],
        [conversation() | {"transcript_segments": [{"start": 0, "end": 999, "text": "bad"}]}],
        [conversation() | {"transcript_segments": [{"start": -1, "end": 1, "text": "bad"}]}],
        [conversation() | {"transcript_segments": [{"start": 0, "end": 1, "text": 123}]}],
    ],
)
def test_invalid_remote_data_never_writes_partial_page_or_leaks_body(connected, payload):
    client, remote = connected
    if isinstance(payload, list):
        payload = [conversation("valid-before-invalid"), *payload]
    remote(lambda request: httpx.Response(200, json=payload))
    response = client.post("/v1/integrations/omi/import", json={})
    assert response.status_code == 502
    assert "nothing was imported" in response.json()["detail"]
    assert client.get("/v1/transcripts").json()["total"] == 0


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_upstream_failures_do_not_forward_diagnostics_or_redirect_secrets(connected, status):
    client, remote = connected
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example/"}, text=KEY)

    remote(handler)
    response = client.post("/v1/integrations/omi/import", json={})
    assert response.status_code in (502, 503)
    assert KEY not in response.text and len(requests) == 1


def test_network_error_is_reported_as_omi_error(connected):
    client, remote = connected

    def handler(request):
        raise httpx.ConnectError("private network diagnostics", request=request)

    remote(handler)
    response = client.post("/v1/integrations/omi/import", json={})
    assert response.status_code == 502
    assert "Omi API is unavailable" in response.text and "private" not in response.text


def test_missing_key_is_optional_and_never_exposed(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/integrations/omi").json()["configured"] is False
        assert client.post("/v1/integrations/omi/import", json={}).status_code == 503
        assert "omi_api_key" not in client.get("/v1/settings").json()
        assert client.patch("/v1/settings", json={"omi_api_key": KEY}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"start_date": "2026-10-15T12:00:00"},
        {"start_date": "2026-10-16T00:00:00Z", "end_date": "2026-10-15T00:00:00Z"},
    ],
)
def test_import_boundary_validation(connected, body):
    client, _ = connected
    assert client.post("/v1/integrations/omi/import", json=body).status_code == 422


def test_transcript_only_sources_survive_backup_and_retention(connected, tmp_path):
    restore = runpy.run_path(str(Path(__file__).parents[1] / "scripts/archive.py"))["restore"]

    client, _ = connected
    result = client.post("/v1/integrations/omi/import", json={}).json()
    source = result["items"][0]
    store = client.app.state.memory.store
    archive = store.export_archive()
    destination = tmp_path / "restored"
    restore(archive, destination, 10_000_000)
    restored = Store(destination)
    assert restored.search("kitchen", 10)[0]["source_id"] == "upstream-id"
    assert not list(restored.audio_dir.iterdir())
    store.apply_retention(0, 1, AT + timedelta(days=3))
    response = client.post("/v1/integrations/omi/import", json={}).json()
    assert response["existing"] == 1 and response["items"][0]["text"] is None
    assert client.delete(f"/v1/audio/{source['id']}").status_code == 200


def test_configured_status_and_backups_do_not_contain_key(connected):
    import zipfile

    client, _ = connected
    status = client.get("/v1/integrations/omi")
    assert status.json()["configured"] is True and KEY not in status.text
    assert KEY not in client.get("/v1/settings").text
    archive = client.app.state.memory.store.export_archive()
    with zipfile.ZipFile(archive) as content:
        assert all(KEY.encode() not in content.read(name) for name in content.namelist())
    assert KEY not in json.dumps(client.get("/v1/status").json())
