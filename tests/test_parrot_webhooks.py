"""Parrot's signed meeting/test deliveries through the shared transcript archive."""

import hashlib
import hmac
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp, utc_now
from omi_local.settings import Settings

PATH = "/v1/integrations/parrot/webhook"
SECRET = "parrot-test-signing-secret"
AT = utc_now().replace(microsecond=0) - timedelta(hours=2)


def event(**changes):
    return {"event": "meeting.finished", "sent_at": stamp(utc_now()), "meeting": {
        "id": "869ad523-6256-4dc9-aa2a-03d88178da79", "title": "Kitchen planning",
        "date": stamp(AT), "duration_seconds": 4500,
        "people": [{"name": "Ada", "email": "ada@example.com"}],
        "summary": "A generated claim that must not enter evidence",
        "next_steps": ["Another generated claim"], "coaching": "Generated coaching",
        "bookmarks": [{"time": "70:05", "label": "Delivery"}], "notes": "Private notes",
        "transcript": [
            {"time": "00:01", "speaker": "Ada", "text": "Kitchen delivery Friday."},
            {"time": "70:05", "speaker": "Bob", "text": "I will arrange the installation."},
        ], **changes,
    }}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, worker_enabled=False, timezone="UTC",
                    parrot_webhook_enabled=True, parrot_webhook_secret=SECRET, _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as session:
        yield session


def send(client, payload=None, *, secret=SECRET, raw=None, headers=None, **kwargs):
    raw = raw if raw is not None else json.dumps(payload or event()).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(PATH, content=raw, headers={
        "Content-Type": "application/json", "X-Parrot-Signature": f"sha256={signature}",
        **(headers or {}),
    }, **kwargs)


def test_signed_meeting_is_searchable_finished_transcript_with_original_times(client):
    response = send(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["imported"] == 1
    row = result["items"][0]
    assert row["source_kind"] == "parrot_export"
    assert row["vendor_id"] == "parrot" and row["vendor_label"] == "Parrot"
    assert row["source_id"] == event()["meeting"]["id"]
    assert row["timestamp_basis"] == "provider_meeting_date"
    assert row["source_metadata"]["parrot_meeting_date"] == stamp(AT)
    assert row["source_metadata"]["transcript_timing"] == "start_only"
    assert row["started_at"] == stamp(AT)
    assert row["ended_at"] == stamp(AT + timedelta(seconds=4500))
    assert not row["audio_available"]
    assert row["text"] == "Ada: Kitchen delivery Friday.\nBob: I will arrange the installation."
    assert row["segments"][1]["start"] == row["segments"][1]["end"] == 4205
    assert row["segments"][1]["speaker_name"] == "Bob"
    assert client.get("/v1/transcripts", params={"q": "Kitchen"}).json()["total"] == 1
    assert client.get("/v1/transcripts", params={"q": "generated"}).json()["total"] == 0
    assert client.get(f"/v1/audio/{row['id']}").status_code == 404
    groups = client.app.state.memory.recaps.groups(utc_now())
    assert len(groups) == 1 and groups[0].ready and groups[0].basis == "finished_import"


def test_retries_preserve_corrections_and_deletions(client):
    first = send(client).json()["items"][0]
    key = first["id"]
    assert client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected"}).status_code == 200
    retry = send(client).json()
    assert retry["existing"] == 1 and retry["items"][0]["text"] == "Corrected"
    assert client.delete(f"/v1/audio/{key}").status_code == 200
    retry = send(client).json()
    assert retry["skipped"] == 1 and retry["items"] == []


def test_retention_does_not_restore_old_meetings_on_redelivery(client):
    old = event(date=stamp(AT - timedelta(days=60)))
    first = send(client, old)
    assert first.status_code == 200 and first.json()["items"][0]["text"] is None
    retried = send(client, old).json()
    assert retried["existing"] == 1 and retried["imported"] == 0
    assert retried["items"][0]["state"] == "expired" and retried["items"][0]["text"] is None
    assert client.get("/v1/transcripts", params={"q": "Kitchen"}).json()["total"] == 0


def test_final_turn_accepts_parrots_one_second_duration_rounding(client):
    response = send(client, event(duration_seconds=4204))
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["segments"][-1]["start"] == 4205
    assert row["source_metadata"]["parrot_duration_seconds"] == "4204"


def test_recent_file_modification_date_cannot_create_a_future_recording(client):
    response = send(client, event(date=stamp(utc_now())))
    assert response.status_code == 422
    assert client.get("/v1/transcripts").json()["total"] == 0


def test_missing_and_duplicate_signatures_are_rejected(client):
    raw = json.dumps(event()).encode()
    assert client.post(PATH, content=raw).status_code == 401
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post(PATH, content=raw, headers=[
        ("Content-Type", "application/json"), ("X-Parrot-Signature", signature),
        ("X-Parrot-Signature", signature),
    ])
    assert response.status_code == 401


def test_sender_profile_name_does_not_override_url_destination(client):
    profile = client.post("/v1/profiles", json={"name": "Work"}).json()["id"]
    assert send(client, event(profile="Default"), params={"profile_id": profile}).status_code == 200
    assert client.get("/v1/transcripts").json()["total"] == 0
    assert client.get("/v1/transcripts", params={"profile_id": profile}).json()["total"] == 1
    assert send(client, params={"profile_id": str(uuid4())}).status_code == 404


def test_signed_test_event_needs_no_meeting_and_does_not_import(client):
    result = send(client, {"event": "test", "sent_at": stamp(utc_now()),
                           "message": "Hello from Parrot."})
    assert result.status_code == 200 and result.json()["event"] == "test"
    assert client.get("/v1/transcripts").json()["total"] == 0


@pytest.mark.parametrize("headers", [
    {"X-Parrot-Signature": ""}, {"X-Parrot-Signature": "sha256=" + "0" * 64},
    {"X-Parrot-Signature": b"sha256=\xe9"},
])
def test_invalid_signatures_are_rejected_without_import(client, headers):
    assert send(client, headers=headers).status_code == 401
    assert client.get("/v1/transcripts").json()["total"] == 0


def test_signature_verifies_exact_bytes_and_cannot_be_reused_for_altered_body(client):
    raw = json.dumps(event(), indent=2).encode()
    assert send(client, raw=raw).status_code == 200
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    changed = send(client, raw=raw + b" ", headers={"X-Parrot-Signature": signature})
    assert changed.status_code == 401


@pytest.mark.parametrize("changes", [
    {"transcript": None}, {"transcript": []}, {"date": "2026-01-01T12:00:00"},
    {"id": "not-a-uuid"}, {"duration_seconds": 0}, {"duration_seconds": True},
    {"transcript": [{"time": "00:60", "speaker": "Ada", "text": "hello"}]},
    {"transcript": [{"time": "80:00", "speaker": "Ada", "text": "hello"}]},
    {"transcript": [{"time": "00:00", "speaker": "Ada", "text": " "}]},
    {"transcript": [{"time": "00:00", "speaker": "Ada", "text": "\ud800"}]},
    {"transcript": [{"time": "01:00", "text": "First"},
                    {"time": "00:00", "text": "Out of order"}]},
])
def test_invalid_or_summary_only_meetings_do_not_create_evidence(client, changes):
    response = send(client, event(**changes))
    assert response.status_code == 422, response.text
    assert "generated claim" not in response.text
    assert client.get("/v1/transcripts").json()["total"] == 0


def test_bounded_json_only_receiver(client):
    assert send(client, raw=b"{").status_code == 422
    assert send(client, {"event": "other", "sent_at": stamp(utc_now())}).status_code == 422
    assert send(client, headers={"Content-Type": "text/plain"}).status_code == 415
    assert send(client, headers={"Content-Encoding": "gzip"}).status_code == 415
    assert send(client, headers={"Content-Length": "16000001"}).status_code == 413


def test_disabled_and_unconfigured_receiver_and_private_configuration(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        assert send(client).status_code == 404
        config = client.get("/v1/configuration").json()
        saved = client.patch("/v1/configuration", json={"revision": config["revision"],
            "changes": {"parrot_webhook_enabled": True}})
        assert saved.status_code == 200
        assert send(client).status_code == 503
        config = client.get("/v1/configuration").json()
        saved = client.patch("/v1/configuration", json={"revision": config["revision"],
            "changes": {"parrot_webhook_secret": SECRET}})
        assert saved.status_code == 200 and SECRET not in saved.text
        assert send(client).status_code == 200
        assert SECRET not in client.get("/v1/vendors").text
        assert SECRET not in client.get("/v1/settings").text
    with TestClient(create_app(settings)) as client:
        assert send(client).json()["existing"] == 1


def test_saved_secret_is_available_only_in_dedicated_uncached_settings(client):
    response = client.get("/v1/configuration/parrot")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "enabled": True, "secret": SECRET,
        "revision": client.get("/v1/configuration").json()["revision"],
    }
    assert SECRET not in client.get("/v1/configuration").text
    assert SECRET not in client.get("/v1/vendors").text


def test_rotating_secret_immediately_revokes_old_signatures_and_survives_restart(settings):
    replacement = "replacement-signing-secret-for-test"
    payload = {"event": "test", "sent_at": stamp(utc_now())}
    with TestClient(create_app(settings)) as client:
        revision = client.get("/v1/configuration").json()["revision"]
        response = client.patch("/v1/configuration", json={"revision": revision,
            "changes": {"parrot_webhook_secret": replacement}})
        assert response.status_code == 200 and replacement not in response.text
        assert send(client, payload, secret=SECRET).status_code == 401
        assert send(client, payload, secret=replacement).status_code == 200
        assert client.get("/v1/configuration/parrot").json()["secret"] == replacement
        assert client.get("/v1/transcripts").json()["total"] == 0
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/configuration/parrot").json()["secret"] == replacement
        assert send(client, payload, secret=SECRET).status_code == 401
        assert send(client, payload, secret=replacement).status_code == 200
