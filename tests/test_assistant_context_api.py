"""Synthetic tests for the private, read-only HomeCompute export."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings


def setup_client(tmp_path, monkeypatch, *, enabled=True, profile="default"):
    token = secrets.token_urlsafe(40)
    token_path = tmp_path / "export-token"
    token_path.write_text(token + "\n", encoding="ascii")
    token_path.chmod(0o600)
    monkeypatch.setenv("OMI_ASSISTANT_CONTEXT_ENABLED", str(enabled).lower())
    monkeypatch.setenv("OMI_ASSISTANT_CONTEXT_PROFILE_ID", profile)
    monkeypatch.setenv("OMI_ASSISTANT_CONTEXT_TOKEN_FILE", str(token_path))
    settings = Settings(data_dir=tmp_path / "data", worker_enabled=False, _env_file=None)
    return TestClient(create_app(settings)), token


def add_diaries(client, count=3):
    store = client.app.state.profiles.runtimes["default"].memory.store
    now = datetime.now(UTC)
    source_id = str(uuid4())
    store.ingest(UUID(source_id), "test", now, b"synthetic-audio", 1)
    store.complete(source_id, "Synthetic current source")
    with store.connect() as db:
        source = dict(
            db.execute(
                "SELECT id,text,source_version,transcript_version FROM audio WHERE id=?",
                (source_id,),
            ).fetchone()
        )
    for index in range(count):
        day = (now.date() - timedelta(days=index)).isoformat()
        timestamp = (
            (now - timedelta(seconds=count - index))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        content = {
            "summary": "Dansk résumé: æøå " + str(index),
            "sources": [source],
            "pending_chunks": 0,
        }
        with store.connect() as db:
            db.execute(
                "INSERT INTO diary_versions VALUES(?,?,?,?,?)",
                (
                    f"diary-version-{index}",
                    day,
                    f"fingerprint-{index}",
                    json.dumps(content),
                    timestamp,
                ),
            )
    return source_id


def test_disabled_export_is_not_found(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch, enabled=False)
    with client:
        response = client.get("/v1/integrations/assistant-context")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_auth_is_required_and_profile_selectors_are_rejected(tmp_path, monkeypatch):
    client, token = setup_client(tmp_path, monkeypatch)
    with client:
        path = "/v1/integrations/assistant-context"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        headers = {"Authorization": f"Bearer {token}", "X-Omi-Profile": "other-profile"}
        assert client.get(path, headers=headers).status_code == 400
        assert (
            client.get(
                path, headers={"Authorization": f"Bearer {token}"}, params={"profile_id": "default"}
            ).status_code
            == 400
        )
        assert (
            client.get(
                path, headers={"Authorization": f"Bearer {token}"}, params={"limit": "9" * 10000}
            ).status_code
            == 400
        )
        assert (
            client.get(
                path, headers={"Authorization": f"Bearer {token}"}, params={"limit": "21"}
            ).status_code
            == 400
        )


def test_fixed_profile_export_paginates_and_marks_deletion_coverage_false(tmp_path, monkeypatch):
    client, token = setup_client(tmp_path, monkeypatch)
    with client:
        add_diaries(client, 3)
        headers = {"Authorization": f"Bearer {token}"}
        path = "/v1/integrations/assistant-context"
        first = client.get(path, headers=headers, params={"limit": 1})
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["source"] == "daythread"
        assert body["coverage"]["window_complete"] is False
        assert body["coverage"]["deletion_complete"] is False
        assert "Dansk résumé" in body["items"][0]["payload"]["summary"]
        assert body["items"][0]["version_ref"].startswith("diary:")
        cursor = body["next_cursor"]
        assert cursor
        second = client.get(path, headers=headers, params={"limit": 1, "cursor": cursor})
        assert second.status_code == 200, second.text
        assert second.json()["items"][0]["object_id"] != body["items"][0]["object_id"]
        assert client.get(path, headers=headers, params={"cursor": cursor + "x"}).status_code == 400
        assert client.get(path, headers=headers).headers["cache-control"] == "no-store"


def test_stale_saved_summary_is_hidden_after_source_correction(tmp_path, monkeypatch):
    client, token = setup_client(tmp_path, monkeypatch)
    with client:
        source_id = add_diaries(client, 1)
        store = client.app.state.profiles.runtimes["default"].memory.store
        store.correct_transcript(source_id, "Corrected synthetic source")
        response = client.get(
            "/v1/integrations/assistant-context",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []


def test_only_explicit_commitments_export_without_evidence_quotes(tmp_path, monkeypatch):
    client, token = setup_client(tmp_path, monkeypatch)
    with client:
        store = client.app.state.profiles.runtimes["default"].memory.store
        now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        content = {
            "choice": {
                "text": "Send the proposal on Friday",
                "evidence": [
                    {
                        "source_id": "recording-1",
                        "source_version": "v3",
                        "quote": "PRIVATE QUOTATION",
                    }
                ],
            },
            "relationships": [{"kind": "commitment", "label": "Proposal"}],
        }
        metadata = {"review_status": "confirmed", "state": "final", "evidence_condition": "current"}
        with store.connect() as db:
            db.execute(
                "INSERT INTO decision_events VALUES(?,?,?,?,?,?)",
                ("decision-1", 1, "created", json.dumps(content), now, json.dumps(metadata)),
            )
            db.execute(
                "INSERT INTO decision_events VALUES(?,?,?,?,?,?)",
                (
                    "decision-2",
                    1,
                    "created",
                    json.dumps(content),
                    now,
                    json.dumps({**metadata, "review_status": "dismissed"}),
                ),
            )
        response = client.get(
            "/v1/integrations/assistant-context", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["event_type"] for item in items] == ["daythread.commitment"]
        exported = json.dumps(items[0])
        assert "Send the proposal on Friday" in exported
        assert "recording-1" in exported and "PRIVATE QUOTATION" not in exported


def test_configured_missing_profile_does_not_fallback(tmp_path, monkeypatch):
    profile = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    client, token = setup_client(tmp_path, monkeypatch, profile=profile)
    with client:
        response = client.get(
            "/v1/integrations/assistant-context", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 404


def test_token_file_permissions_are_required(tmp_path, monkeypatch):
    token = secrets.token_urlsafe(40)
    path = tmp_path / "token"
    path.write_text(token, encoding="ascii")
    path.chmod(0o644)
    monkeypatch.setenv("OMI_ASSISTANT_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("OMI_ASSISTANT_CONTEXT_PROFILE_ID", "default")
    monkeypatch.setenv("OMI_ASSISTANT_CONTEXT_TOKEN_FILE", str(path))
    with pytest.raises(ValueError, match="configuration invalid"):
        create_app(Settings(data_dir=tmp_path / "data", worker_enabled=False, _env_file=None))
