"""Vendor routes keep archive policy, secrets and original capture provenance shared."""

import asyncio
import json
import runpy
import stat
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_manual_upload import AT, audio_part, manifest, prepare
from test_pocket_api import listing, recording

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.settings import Settings
from omi_local.store import Store

KEY = "pk_private_vendor_integration_key"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "archive", worker_enabled=False, timezone="UTC",
        pocket_api_key=KEY, _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session


@pytest.fixture
def remote():
    clients = []

    def install(client, payload=None, *, profile="default", handler=None):
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(
            handler or (lambda _: httpx.Response(200, json=payload or listing([recording()])))
        ))
        clients.append(upstream)
        client.app.state.profiles.runtimes[profile].vendors["pocket"].client = upstream

    yield install
    for upstream in clients:
        asyncio.run(upstream.aclose())


def pocket_import(client, **kwargs):
    response = client.post("/v1/vendors/pocket/import", json={}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def export_body(**changes):
    return {
        "source_id": "plaud-source", "format": "srt",
        "content": "1\n00:00:10,000 --> 00:00:12,000\nAda: Kitchen delivery Friday\n",
        "started_at": stamp(AT), "ended_at": stamp(AT + timedelta(minutes=5)),
        **changes,
    }


def plaud_import(client, **changes):
    response = client.post("/v1/vendors/plaud/import-export", json=export_body(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def test_catalog_declares_real_capabilities_without_secrets(client):
    response = client.get("/v1/vendors")
    assert response.status_code == 200 and KEY not in response.text
    vendors = {item["id"]: item for item in response.json()["items"]}
    assert {"manual", "omi", "plaud", "pocket", "apple", "sony", "zoom"} <= vendors.keys()
    assert vendors["pocket"]["configured"]
    assert "transcript_import" in vendors["pocket"]["capabilities"]
    assert "transcript_export" in vendors["plaud"]["capabilities"]
    assert "transcript_import" not in vendors["plaud"]["capabilities"]
    assert "file_upload" in vendors["sony"]["capabilities"]
    assert client.get("/v1/vendors/pocket").json() == vendors["pocket"]
    assert client.get("/v1/vendors/missing").status_code == 404
    assert client.post("/v1/vendors/plaud/import", json={}).status_code == 422


def test_pocket_key_is_private_live_across_profiles_and_survives_restart(settings):
    replacement = "pk_replacement_vendor_key"
    with TestClient(create_app(settings)) as client:
        profile = client.post("/v1/profiles", json={"name": "Work"}).json()["id"]
        before = client.get("/v1/configuration").json()
        field = next(row for row in before["fields"] if row["key"] == "pocket_api_key")
        assert field["kind"] == "secret" and not field["restart_required"]
        assert before["values"]["pocket_api_key"] is None
        assert before["secrets"]["pocket_api_key"]
        saved = client.patch("/v1/configuration", json={
            "revision": before["revision"], "changes": {"pocket_api_key": replacement},
        })
        assert saved.status_code == 200 and replacement not in saved.text
        for runtime in client.app.state.profiles.runtimes.values():
            assert runtime.vendors["pocket"].key == replacement
        assert client.get("/v1/vendors/pocket", headers={"X-Omi-Profile": profile}).json()[
            "configured"
        ]
        assert "pocket_api_key" not in client.get("/v1/settings").json()
        assert client.patch("/v1/settings", json={"pocket_api_key": replacement}).status_code == 422
        store = client.app.state.memory.store
        assert replacement not in json.dumps(store.runtime_settings())
        private = settings.data_dir / ".private-settings.json"
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        with zipfile.ZipFile(store.export_archive()) as archive:
            assert all(
                replacement.encode() not in archive.read(name) for name in archive.namelist()
            )
    with TestClient(create_app(settings)) as client:
        assert client.app.state.profiles.runtimes["default"].vendors["pocket"].key == replacement
        view = client.get("/v1/configuration").json()
        assert client.patch("/v1/configuration", json={
            "revision": view["revision"], "changes": {"pocket_api_key": ""},
        }).status_code == 200
        assert not client.get("/v1/vendors/pocket").json()["configured"]
        assert client.post("/v1/vendors/pocket/import", json={}).status_code == 503


def test_pocket_import_is_searchable_profile_scoped_and_preserves_corrections(client, remote):
    remote(client)
    first = pocket_import(client)["items"][0]
    assert first["vendor_id"] == "pocket" and first["vendor_label"] == "Pocket"
    assert first["source_kind"] == "pocket_api" and not first["audio_available"]
    prep = client.post("/v1/conversation-prep/search", json={"subject": "kitchen"})
    assert prep.status_code == 200
    assert prep.json()["sources"][0]["vendor_id"] == "pocket"
    assert client.get("/v1/transcripts", params={"q": "kitchen"}).json()["total"] == 1
    assert client.get(f"/v1/audio/{first['id']}").status_code == 404
    corrected = client.patch(f"/v1/transcripts/{first['id']}", json={"text": "Corrected"})
    assert corrected.status_code == 200
    retried = pocket_import(client)
    assert retried["existing"] == 1 and retried["items"][0]["text"] == "Corrected"
    profile = client.post("/v1/profiles", json={"name": "Private work"}).json()["id"]
    headers = {"X-Omi-Profile": profile}
    remote(client, profile=profile)
    assert client.get("/v1/transcripts", headers=headers).json()["total"] == 0
    separate = pocket_import(client, headers=headers)
    assert separate["imported"] == 1 and separate["items"][0]["id"] == first["id"]
    assert separate["items"][0]["text"] != "Corrected"
    assert client.get("/v1/transcripts").json()["total"] == 1


@pytest.mark.parametrize("format", ["srt", "txt"])
def test_plaud_export_joins_search_completeness_and_closed_conversations(client, format):
    changes = {} if format == "srt" else {
        "format": "txt", "content": "Ada: Kitchen delivery Friday",
    }
    source = plaud_import(client, **changes)["items"][0]
    assert source["vendor_id"] == "plaud" and source["vendor_label"] == "Plaud"
    assert source["source_kind"] == "plaud_export" and not source["audio_available"]
    prep = client.post("/v1/conversation-prep/search", json={"subject": "kitchen"})
    assert prep.status_code == 200
    assert prep.json()["sources"][0]["vendor_label"] == "Plaud"
    assert source["started_at"] == stamp(AT)
    assert len(source["segments"]) == (1 if format == "srt" else 0)
    assert client.get("/v1/transcripts", params={"q": "kitchen"}).json()["total"] == 1
    assert client.get(f"/v1/audio/{source['id']}").status_code == 404
    assert plaud_import(client, **changes)["existing"] == 1
    coverage = client.get("/v1/completeness", params={
        "start": stamp(AT), "end": stamp(AT + timedelta(hours=1)),
    }).json()
    assert coverage["seconds"]["available_audio"] == 0
    assert coverage["seconds"]["transcript"] == 300
    assert coverage["counts"]["transcript_only_imports"] == 1
    assert coverage["counts"]["expired_audio"] == 0
    groups = client.app.state.memory.recaps.groups(AT + timedelta(hours=1))
    assert len(groups) == 1 and groups[0].ready
    assert groups[0].basis == "finished_import"
    assert groups[0].closed_at == source["ended_at"]
    assert not list(client.app.state.memory.store.audio_dir.iterdir())


def test_deleted_import_stays_suppressed_after_restart_but_other_vendor_is_independent(
    settings, remote,
):
    with TestClient(create_app(settings)) as client:
        remote(client, listing([recording("shared-upstream-id")]))
        first = pocket_import(client)["items"][0]
        assert client.delete(f"/v1/audio/{first['id']}").status_code == 200
        other = plaud_import(client, source_id="shared-upstream-id")
        assert other["imported"] == 1 and other["items"][0]["id"] != first["id"]
        assert client.delete(f"/v1/audio/{other['items'][0]['id']}").status_code == 200
    with TestClient(create_app(settings)) as client:
        remote(client, listing([recording("shared-upstream-id")]))
        assert pocket_import(client)["skipped"] == 1
        assert plaud_import(client, source_id="shared-upstream-id")["skipped"] == 1
        assert client.get("/v1/transcripts").json()["total"] == 0


def test_vendor_transcripts_restore_with_provenance_and_retention_does_not_reimport(
    client, remote, tmp_path,
):
    remote(client)
    pocket = pocket_import(client)["items"][0]
    plaud = plaud_import(client)["items"][0]
    store = client.app.state.memory.store
    restore = runpy.run_path(str(Path(__file__).parents[1] / "scripts/archive.py"))["restore"]
    destination = tmp_path / "restored"
    restore(store.export_archive(), destination, 10_000_000)
    restored = Store(destination)
    matches = restored.search("kitchen", 10)
    assert {row["source_kind"] for row in matches} == {"pocket_api", "plaud_export"}
    assert {row["id"] for row in matches} == {pocket["id"], plaud["id"]}
    assert not list(restored.audio_dir.iterdir())
    store.apply_retention(0, 1, AT + timedelta(days=3))
    assert pocket_import(client)["items"][0]["text"] is None
    assert plaud_import(client)["items"][0]["text"] is None
    assert client.get("/v1/transcripts", params={"q": "kitchen"}).json()["total"] == 0


def test_invalid_full_pocket_page_writes_nothing(client, remote):
    remote(client, listing([
        recording("valid"), recording("invalid") | {"duration": -1},
    ]))
    response = client.post("/v1/vendors/pocket/import", json={})
    assert response.status_code == 502 and "nothing was imported" in response.text
    assert client.get("/v1/transcripts").json()["total"] == 0
    assert client.get("/v1/status").json()["audio"] == {}


def send_vendor_part(client, plan, data, *, index=0, vendor_id=None):
    params = {
        "device_id": "manual-web", "source_kind": "manual_upload",
        "source_id": plan["source_id"],
        "started_at": stamp(AT + timedelta(seconds=index * 120)),
        "recording_id": plan["recording_id"], "part_index": index,
        "part_count": len(plan["parts"]), "recording_started_at": plan["started_at"],
        "recording_ended_at": plan["ended_at"],
    }
    if vendor_id is not None:
        params["vendor_id"] = vendor_id
    return client.put(f"/v1/audio/{plan['parts'][index]['id']}", params=params, content=data)


def test_branded_upload_resume_preserves_original_plan_vendor_and_rejects_changed_parts(client):
    parts = [audio_part(120), audio_part()]
    body = manifest(parts, vendor_id="plaud")
    plan = prepare(client, body)
    assert plan["vendor_id"] == "plaud"
    first = send_vendor_part(client, plan, parts[0], vendor_id="plaud")
    assert first.status_code == 200, first.text
    assert first.json()["vendor_id"] == "plaud"
    assert send_vendor_part(client, plan, parts[0], vendor_id="pocket").status_code == 409
    assert send_vendor_part(client, plan, parts[0]).status_code == 409
    Store(client.app.state.memory.store.root)
    resumed = prepare(client, body | {"vendor_id": "pocket", "source_id": "renamed.wav"})
    assert resumed["vendor_id"] == "plaud" and resumed["source_id"] == "meeting.m4a"
    assert resumed["recording_id"] == plan["recording_id"]
    assert [part["uploaded"] for part in resumed["parts"]] == [True, False]
    final = send_vendor_part(client, resumed, parts[1], index=1, vendor_id="plaud")
    assert final.status_code == 200, final.text
    assert final.json()["vendor_id"] == "plaud"
    assert prepare(client, body)["status"] == "duplicate"


def test_default_manual_upload_plan_remains_compatible_with_older_clients(client):
    data = audio_part()
    plan = prepare(client, manifest([data]))
    assert plan["vendor_id"] == "manual"
    response = send_vendor_part(client, plan, data)
    assert response.status_code == 200, response.text
    assert response.json()["source_kind"] == "manual_upload"
    assert "vendor_id" not in response.json()["source_metadata"]
    assert send_vendor_part(client, plan, data).status_code == 200


@pytest.mark.parametrize("vendor_id", ["unknown", "", "../plaud", "Plaud"])
def test_invalid_brand_ids_are_rejected_before_upload(client, vendor_id):
    response = client.post("/v1/recording-uploads/prepare", json=manifest(
        [audio_part()], vendor_id=vendor_id,
    ))
    assert response.status_code == 422
    uploaded = client.put(f"/v1/audio/{uuid4()}", params={
        "device_id": "manual-web", "source_kind": "manual_upload", "source_id": "recording.wav",
        "started_at": stamp(AT), "vendor_id": vendor_id,
    }, content=audio_part())
    assert uploaded.status_code == 422
    assert client.get("/v1/status").json()["audio"] == {}
