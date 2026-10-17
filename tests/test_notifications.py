"""Notification delivery is durable, scoped, and suppressed while the app is visible."""

import asyncio
import json
import time
from uuid import uuid4

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from omi_local.notifications import NotificationService
from omi_local.settings import Settings
from omi_local.store import Store

TOKEN = "ab" * 32


def event(service, key="recap:1"):
    return service.publish("recap", "Your 17 October recap is ready",
                           "3 recordings · English", {"screen": "recap", "date": "2026-10-17"},
                           key)


def settings(tmp_path, **values):
    return Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None, **values)


def test_feed_survives_restart_and_publish_retries_are_idempotent(tmp_path):
    service = NotificationService(Store(tmp_path), settings(tmp_path))
    first = event(service)
    assert event(service) == first
    second = event(service, "recap:2")
    reopened = Store(tmp_path)
    page = reopened.notifications_after(0, limit=1)
    assert page == {"notifications": [first], "cursor": first["sequence"]}
    assert reopened.notifications_after(page["cursor"])["notifications"] == [second]
    assert reopened.notifications_after(second["sequence"])["notifications"] == []
    asyncio.run(service.close())


def test_active_lease_blocks_push_until_ack_or_expiration(tmp_path):
    service = NotificationService(Store(tmp_path), settings(tmp_path))
    store = service.store
    store.register_notification_device("phone", TOKEN, "sandbox", True)
    seen = event(service)
    assert store.pending_notifications() == []
    store.acknowledge_notifications("phone", [seen["id"]])
    missed = event(service, "recap:2")
    # Abrupt process termination without a background callback still permits push
    # after the last foreground lease expires. Already-consumed results stay quiet.
    with store.connect() as db:
        db.execute("UPDATE notification_devices SET active_until=?", (time.time() - 1,))
    pending = store.pending_notifications()
    assert [row["id"] for row in pending] == [missed["id"]]
    assert store.notification_presence("phone", True)
    assert not store.notification_still_pending(missed["id"], "phone")
    assert store.notification_presence("phone", False)
    assert store.notification_still_pending(missed["id"], "phone")
    asyncio.run(service.close())


def test_registration_does_not_push_old_history_and_unregister_clears_outbox(tmp_path):
    service = NotificationService(Store(tmp_path), settings(tmp_path))
    event(service)
    service.store.register_notification_device("phone", TOKEN, "sandbox", False)
    assert service.store.pending_notifications() == []
    event(service, "recap:2")
    assert len(service.store.pending_notifications()) == 1
    service.store.unregister_notification_device("phone")
    assert service.store.pending_notifications() == []
    assert len(service.store.notifications_after(0)["notifications"]) == 2
    asyncio.run(service.close())


def test_delivery_retry_survives_restart_and_bad_token_unregisters(tmp_path):
    async def scenario():
        service = NotificationService(Store(tmp_path), settings(tmp_path))
        service.store.register_notification_device("phone", TOKEN, "production", False)
        notification = event(service)

        class Sender:
            configured = True
            response = (503, "ServiceUnavailable")

            async def send(self, delivery, profile_id):
                assert delivery["id"] == notification["id"]
                return self.response

        await service.sender.close()
        service.sender = Sender()
        await service.deliver_pending()
        with Store(tmp_path).connect() as db:
            row = db.execute("SELECT * FROM notification_deliveries").fetchone()
            assert row["attempts"] == 1 and row["state"] == "pending"
            assert row["retry_at"] > time.time()
            db.execute("UPDATE notification_deliveries SET retry_at=0")
        service.sender.response = (410, "Unregistered")
        await service.deliver_pending()
        with service.store.connect() as db:
            assert db.execute("SELECT count(*) FROM notification_devices").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0] == 0

    asyncio.run(scenario())


def test_apns_signing_payload_routing_and_size(tmp_path):
    async def scenario():
        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        config = settings(tmp_path, apns_team_id="ABCDEFGHIJ", apns_key_id="KLMNOPQRST",
                          apns_private_key=pem, apns_topic="com.example.daythread")
        service = NotificationService(Store(tmp_path), config, "profile-one")
        service.store.register_notification_device("phone", TOKEN, "sandbox", False)
        service.publish("translation", "Danish translation ready", "Æ" * 600,
                        {"source_id": str(uuid4()), "language": "da"}, "translation:1")
        delivery = service.store.pending_notifications()[0]

        def response(request):
            assert request.url.host == "api.sandbox.push.apple.com"
            assert request.headers["apns-topic"] == "com.example.daythread"
            assert request.headers["apns-push-type"] == "alert"
            assert request.headers["apns-id"] == delivery["id"]
            token = request.headers["authorization"].removeprefix("bearer ")
            assert jwt.get_unverified_header(token)["kid"] == "KLMNOPQRST"
            assert jwt.decode(token, private.public_key(), algorithms=["ES256"])["iss"] == (
                "ABCDEFGHIJ"
            )
            assert len(request.content) <= 4096
            payload = json.loads(request.content)
            assert payload["device_id"] == "phone" and payload["profile_id"] == "profile-one"
            assert payload["notification"]["target"]["language"] == "da"
            assert payload["aps"]["alert"]["title"] == "Danish translation ready"
            return httpx.Response(200)

        await service.sender.client.aclose()
        service.sender.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        await service.deliver_pending()
        with service.store.connect() as db:
            assert db.execute("SELECT state FROM notification_deliveries").fetchone()[0] == "sent"
        await service.close()

    asyncio.run(scenario())


def test_missing_credentials_keeps_in_app_notifications_without_network(tmp_path):
    async def scenario():
        service = NotificationService(Store(tmp_path), settings(tmp_path))
        service.store.register_notification_device("phone", TOKEN, "production", False)
        notification = event(service)
        assert not service.sender.configured
        await service.deliver_pending()
        assert service.store.notifications_after(0)["notifications"] == [notification]
        assert len(service.store.pending_notifications()) == 1
        await service.close()

    asyncio.run(scenario())


def test_configuration_redacts_apns_private_key(tmp_path):
    from omi_local.configuration import Configuration

    config = settings(tmp_path, apns_private_key="private-key-never-public")
    public = Configuration(config).view(config)
    assert "private-key-never-public" not in json.dumps(public)
    assert public["secrets"]["apns_private_key"] is True


def test_apns_configuration_changes_reach_running_profiles(tmp_path):
    from fastapi.testclient import TestClient

    from omi_local.api import create_app

    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/v1/profiles", json={"name": "Second"})
        revision = client.get("/v1/configuration").json()["revision"]
        response = client.patch("/v1/configuration", json={
            "revision": revision, "changes": {"apns_team_id": "ABCDEFGHIJ"},
        })
        assert response.status_code == 200
        for profile in client.app.state.profiles.runtimes.values():
            assert profile.memory.notifications.settings.apns_team_id == "ABCDEFGHIJ"
            assert profile.memory.notifications.settings is profile.memory.settings


def test_expired_pending_push_is_not_sent(tmp_path):
    service = NotificationService(Store(tmp_path), settings(tmp_path))
    service.store.register_notification_device("phone", TOKEN, "sandbox", False)
    event(service)
    with service.store.connect() as db:
        db.execute("UPDATE notification_deliveries SET expires_at=?", (time.time() - 1,))
    assert service.store.pending_notifications() == []
    asyncio.run(service.close())


def test_api_profile_switch_moves_token_and_foreground_ack_cancels_push(tmp_path):
    from fastapi.testclient import TestClient

    from omi_local.api import create_app

    with TestClient(create_app(settings(tmp_path))) as client:
        original = client.app.state.profiles.runtimes["default"].memory.notifications
        device = {"token": TOKEN, "environment": "sandbox", "active": False}
        path = "/v1/notifications/devices/phone"
        assert client.put(path, json=device).status_code == 200
        event(original)
        assert len(original.store.pending_notifications()) == 1
        profile = client.post("/v1/profiles", json={"name": "Second"}).json()["id"]
        headers = {"X-Omi-Profile": profile}
        assert client.get("/v1/notifications", headers=headers).json()["notifications"] == []
        assert client.put(path, json=device, headers=headers).status_code == 200
        assert original.store.pending_notifications() == []
        selected = client.app.state.profiles.runtimes[profile].memory.notifications
        notification = event(selected)
        assert client.post(path + "/presence", json={"active": True},
                           headers=headers).status_code == 200
        assert selected.store.pending_notifications() == []
        assert client.get("/v1/notifications", headers=headers).json()["notifications"] == [
            notification
        ]
        assert client.post("/v1/notifications/ack", headers=headers,
                           json={"installation_id": "phone", "ids": [notification["id"]]}
                           ).status_code == 200
        client.post(path + "/presence", json={"active": False}, headers=headers)
        assert selected.store.pending_notifications() == []
        assert client.get("/v1/notifications/status").json() == {"push_configured": False}
        assert client.delete(path, headers=headers).status_code == 200


def test_transcript_deletion_or_correction_purges_notification_previews(tmp_path):
    from test_backend import AT, wav

    service = NotificationService(Store(tmp_path), settings(tmp_path))
    source = str(uuid4())
    service.store.ingest(source, "phone", AT, wav([7] * 16000), 1)
    with service.store.connect() as db:
        db.execute("UPDATE audio SET text='original' WHERE id=?", (source,))
    service.store.register_notification_device("phone", TOKEN, "sandbox", False)
    event(service)
    with service.store.connect() as db:
        db.execute("UPDATE audio SET text='corrected' WHERE id=?", (source,))
    assert service.store.notifications_after(0)["notifications"] == []
    assert service.store.pending_notifications() == []
    event(service, "recap:2")
    service.store.delete_audio(source)
    assert service.store.notifications_after(0)["notifications"] == []
    assert service.store.pending_notifications() == []
    asyncio.run(service.close())


def test_publish_checks_current_evidence_inside_transaction(tmp_path):
    import pytest

    service = NotificationService(Store(tmp_path), settings(tmp_path))

    def guard(db):
        assert db.in_transaction
        raise ValueError("Evidence was removed")

    with pytest.raises(ValueError, match="Evidence was removed"):
        service.publish("recap", "Ready", "Details", {}, "recap:1", guard=guard)
    assert service.store.notifications_after(0)["notifications"] == []
    asyncio.run(service.close())


def test_rejected_previous_token_does_not_unregister_rotated_token(tmp_path):
    store = Store(tmp_path)
    store.register_notification_device("phone", TOKEN, "sandbox", False)
    rotated = "cd" * 32
    store.register_notification_device("phone", rotated, "sandbox", False)
    store.retire_notification_token("phone", TOKEN)
    with store.connect() as db:
        assert db.execute("SELECT token FROM notification_devices").fetchone()[0] == rotated
