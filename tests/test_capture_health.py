"""Capture reminders use observed expectations, never old imports or inference backlog."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from test_extended import audio_data

from omi_local.capture_health import capture_health_status, check_capture_health
from omi_local.domain import stamp
from omi_local.settings import Settings
from omi_local.store import Store

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
PHONE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


def memory(tmp_path, **changes):
    return SimpleNamespace(
        store=Store(tmp_path), settings=Settings(
            data_dir=tmp_path, _env_file=None, worker_enabled=False,
            timezone="UTC", capture_health_enabled=True, **changes,
        ),
    )


def observation(mem, *, phone_id=PHONE, observed=NOW, progress=None, **changes):
    body = {
        "sequence": 1, "observed_at": stamp(observed), "device_id": "pendant",
        "capture_source": "omi", "capture_state": "capturing", "cause": "resumed",
        "requested_pause": False, "last_decoded_at": stamp(progress or NOW - timedelta(hours=1)),
        "last_persisted_at": stamp(progress or NOW - timedelta(hours=1)),
        "pending_recordings": 0, "recovery_items": 0, "pending_seconds": 0,
        "oldest_capture_at": None, "upload_policy": "any_network", "upload_state": "idle",
        "storage_bytes": 0, "storage_budget_bytes": 1024,
    } | changes
    mem.store.save_phone(phone_id, body)


def feed(mem):
    return mem.store.notifications_after(0)["notifications"]


def test_stall_is_opt_in_and_import_backlog_does_not_establish_expectation(tmp_path):
    mem = memory(tmp_path)
    mem.store.ingest(str(uuid4()), "import", NOW - timedelta(days=3), audio_data(), 1,
                     source_kind="manual_upload")
    assert check_capture_health(mem, now=NOW) == []
    observation(mem)
    mem.settings.capture_health_enabled = False
    assert check_capture_health(mem, now=NOW) == []
    assert feed(mem) == []


def test_one_notice_per_incident_survives_restart_and_only_matching_phone_gets_push(tmp_path):
    mem = memory(tmp_path)
    mem.store.register_notification_device("first", "ab" * 32, "sandbox", False, phone_id=PHONE)
    mem.store.register_notification_device("other", "cd" * 32, "sandbox", False, phone_id=OTHER)
    mem.store.register_notification_device("old-client", "ef" * 32, "sandbox", False)
    observation(mem)
    event, = check_capture_health(mem, now=NOW)
    assert event["kind"] == "capture_health"
    assert event["target"]["phone_id"] == PHONE
    assert event["target"]["issue"] == "capture_stalled"
    assert [value["device_id"] for value in mem.store.pending_notifications()] == ["first"]
    mem.store = Store(tmp_path)
    assert check_capture_health(mem, now=NOW + timedelta(hours=7)) == []
    assert len(feed(mem)) == 1
    hidden = mem.store.notifications_after(0, phone_id=OTHER)
    assert hidden == {"notifications": [], "cursor": event["sequence"]}


def test_fresh_progress_suppresses_alert_even_with_processing_backlog(tmp_path):
    mem = memory(tmp_path)
    observation(mem, progress=NOW - timedelta(seconds=30), pending_recordings=100)
    for _ in range(3):
        mem.store.ingest(str(uuid4()), "import", NOW - timedelta(days=2), audio_data(), 1,
                         source_kind="manual_upload")
    assert check_capture_health(mem, now=NOW) == []
    status = capture_health_status(mem.store, mem.settings, now=NOW)
    assert status["phones"][0]["status"] == "healthy"


def test_switch_to_phone_recording_does_not_reuse_pendant_progress(tmp_path):
    mem = memory(tmp_path)
    observation(mem)
    assert len(check_capture_health(mem, now=NOW)) == 1
    # The previous native client sent the old pendant ID and persisted clock
    # while a phone-microphone session was recording. Neither proves a stall.
    observation(mem, sequence=2, capture_source="phone", last_decoded_at=stamp(NOW))
    mem.store.save_capture_state(str(uuid4()), {
        "phone_id": PHONE, "occurred_at": stamp(NOW - timedelta(hours=2)),
        "state": "capturing", "cause": "resumed",
    })
    assert check_capture_health(mem, now=NOW) == []
    assert feed(mem) == []
    status = capture_health_status(mem.store, mem.settings, now=NOW)["phones"][0]
    assert status["status"] == "unknown"
    assert status["last_progress_at"] is None


def test_received_live_audio_is_progress_but_recent_old_import_receipt_is_not(tmp_path):
    mem = memory(tmp_path)
    observation(mem, observed=NOW - timedelta(hours=1))
    mem.store.ingest(str(uuid4()), "pendant", NOW - timedelta(minutes=1), audio_data(), 1)
    assert check_capture_health(mem, now=NOW) == []
    mem.store = Store(tmp_path / "other")
    observation(mem, observed=NOW - timedelta(hours=1))
    mem.store.ingest(str(uuid4()), "pendant", NOW - timedelta(days=2), audio_data(), 1,
                     source_kind="manual_upload")
    event, = check_capture_health(mem, now=NOW)
    assert event["target"]["issue"] == "report_stale"
    assert "unknown" in event["body"]
    assert "lost" not in event["body"]


def test_pause_newer_than_observation_cancels_pending_reminder(tmp_path):
    mem = memory(tmp_path)
    observation(mem, observed=NOW - timedelta(minutes=1))
    mem.store.register_notification_device("first", "ab" * 32, "sandbox", False, phone_id=PHONE)
    assert len(check_capture_health(mem, now=NOW)) == 1
    mem.store.save_capture_state(str(uuid4()), {
        "phone_id": PHONE, "occurred_at": stamp(NOW), "state": "paused", "cause": "user_pause",
    })
    assert check_capture_health(mem, now=NOW) == []
    assert feed(mem) == []
    assert mem.store.pending_notifications() == []


def test_paused_unknown_historical_and_ancient_reports_stay_quiet(tmp_path):
    for index, changes in enumerate((
        {"requested_pause": True}, {"capture_state": "paused"},
        {"capture_source": "unknown"}, {"capture_state": "unknown"},
        {"observed": NOW - timedelta(days=2)},
    )):
        mem = memory(tmp_path / str(index))
        observation(mem, **changes)
        assert check_capture_health(mem, now=NOW) == []
    mem = memory(tmp_path / "restored")
    observation(mem)
    with mem.store.connect() as db:
        db.execute("UPDATE phone_observations SET historical=1")
    assert check_capture_health(mem, now=NOW) == []


def test_quiet_hours_defer_without_consuming_incident_and_use_archive_timezone(tmp_path):
    mem = memory(tmp_path)
    observation(mem, observed=NOW, progress=NOW - timedelta(hours=1))
    mem.settings.timezone = "Pacific/Auckland"  # 01:00 next day at this UTC instant
    assert capture_health_status(mem.store, mem.settings, now=NOW)["quiet_hours"]
    assert check_capture_health(mem, now=NOW) == []
    mem.settings.capture_health_quiet_start_hour = 8
    mem.settings.capture_health_quiet_end_hour = 8
    assert len(check_capture_health(mem, now=NOW)) == 1


def test_cross_incident_cooldown_does_not_hide_next_incident_forever(tmp_path):
    mem = memory(tmp_path)
    observation(mem)
    assert len(check_capture_health(mem, now=NOW)) == 1
    observation(mem, sequence=2, observed=NOW + timedelta(minutes=1),
                progress=NOW + timedelta(minutes=1))
    assert check_capture_health(mem, now=NOW + timedelta(minutes=1)) == []
    assert feed(mem) == []
    assert check_capture_health(mem, now=NOW + timedelta(minutes=45)) == []
    assert len(check_capture_health(mem, now=NOW + timedelta(hours=7))) == 1


def test_decoder_activity_without_durable_progress_does_not_hide_storage_stall(tmp_path):
    mem = memory(tmp_path)
    observation(mem, last_decoded_at=stamp(NOW), capture_state="blocked", cause="storage")
    assert len(check_capture_health(mem, now=NOW)) == 1


def test_delivery_rechecks_recovery_opt_out_and_quiet_hours(tmp_path, monkeypatch):
    import asyncio

    import omi_local.capture_health as health
    from omi_local.notifications import NotificationService

    monkeypatch.setattr(health, "utc_now", lambda: NOW)

    async def scenario():
        mem = memory(tmp_path)
        observation(mem)
        mem.store.register_notification_device("first", "ab" * 32, "sandbox", False, phone_id=PHONE)
        check_capture_health(mem, now=NOW)
        service = NotificationService(mem.store, mem.settings)
        sent = []

        class Sender:
            configured = True

            async def send(self, delivery, profile_id):
                sent.append(delivery)
                return 200, ""

        await service.sender.close()
        service.sender = Sender()
        try:
            mem.settings.timezone = "Pacific/Auckland"
            await service.deliver_pending()
            assert sent == []
            assert len(mem.store.pending_notifications()) == 1
            mem.settings.timezone = "UTC"
            observation(mem, sequence=2, progress=NOW)
            await service.deliver_pending()
            assert sent == []
            assert feed(mem) == []
            # A later incident is eligible after the previous cooldown.
            observation(mem, sequence=3, progress=NOW - timedelta(hours=2))
            with mem.store.connect() as db:
                db.execute("DELETE FROM capture_health_state")
            check_capture_health(mem, now=NOW)
            assert len(feed(mem)) == 1
            mem.settings.capture_health_enabled = False
            await service.deliver_pending()
            assert feed(mem) == []
            assert sent == []
        finally:
            # The real sender was closed before replacement.
            pass

    asyncio.run(scenario())


def test_phone_binding_registration_feed_filter_and_health_status_route(tmp_path):
    from fastapi.testclient import TestClient

    from omi_local.api import create_app

    mem = memory(tmp_path, background_processing_enabled=False)
    with TestClient(create_app(mem.settings)) as client:
        store = client.app.state.memory.store
        body = {"token": "ab" * 32, "environment": "sandbox", "active": False,
                "phone_id": PHONE}
        assert client.put("/v1/notifications/devices/first", json=body).status_code == 200
        store.publish_notification("capture_health", "Capture needs a check", "Check capture.",
                                   {"phone_id": PHONE, "issue": "capture_stalled"}, "health-test")
        own = client.get("/v1/notifications", params={"phone_id": PHONE}).json()
        other = client.get("/v1/notifications", params={"phone_id": OTHER}).json()
        assert len(own["notifications"]) == 1
        assert other["notifications"] == []
        assert own["cursor"] == other["cursor"]
        assert client.get("/v1/notifications/capture-health").json()["enabled"]
        assert client.get("/v1/notifications", params={"phone_id": "invalid"}).status_code == 422
        # Re-registering an old client explicitly clears the previous phone mapping.
        del body["phone_id"]
        assert client.put("/v1/notifications/devices/first", json=body).status_code == 200
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM notification_device_phones").fetchone()[0] == 0


def test_capture_health_delivery_copy_is_localized_without_mutating_stored_text(tmp_path):
    from omi_local.notification_localization import CAPTURE_HEALTH_TITLES

    mem = memory(tmp_path)
    observation(mem)
    event, = check_capture_health(mem, now=NOW)
    for language, title in CAPTURE_HEALTH_TITLES.items():
        localized, = mem.store.notifications_after(0, interface_language=language)["notifications"]
        assert localized["title"] == title
        if language != "en":
            assert localized["body"] != event["body"]
    assert feed(mem) == [event]


def test_changing_phone_binding_fences_already_pending_health_deliveries(tmp_path):
    mem = memory(tmp_path)
    observation(mem)
    mem.store.register_notification_device("first", "ab" * 32, "sandbox", False, phone_id=PHONE)
    event, = check_capture_health(mem, now=NOW)
    assert mem.store.notification_still_pending(event["id"], "first")
    mem.store.register_notification_device("first", "ab" * 32, "sandbox", False, phone_id=OTHER)
    assert mem.store.pending_notifications() == []
    assert not mem.store.notification_still_pending(event["id"], "first")
