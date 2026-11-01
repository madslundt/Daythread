"""Reported capture stops release received audio without treating missing audio as silence."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from test_recaps import AT, insert
from test_recaps import client as client

from omi_local.domain import stamp
from omi_local.worker import RecapBatcher, scheduled_recaps


def report_capture(client, at, state="disconnected", *, phone=None, sequence=1, **changes):
    phone = phone or uuid4()
    body = {
        "sequence": sequence, "observed_at": stamp(at), "device_id": "omi",
        "capture_source": "omi", "capture_state": state,
        "cause": "user_pause" if state == "paused" else "connection",
        "requested_pause": state == "paused", "pending_recordings": 0,
        "pending_seconds": 0, "recovery_items": 0, "upload_policy": "any_network",
        "upload_state": "idle", "storage_bytes": 100, "storage_budget_bytes": 1000000,
        **changes,
    }
    assert client.put(f"/v1/capture-state/{uuid4()}", json={
        "phone_id": str(phone), "occurred_at": stamp(at), "state": state,
        "cause": body["cause"],
    }).status_code == 200
    assert client.put(f"/v1/phones/{phone}/status", json=body).status_code == 200
    return phone


@pytest.mark.parametrize("state,requested_pause", [
    ("paused", True), ("paused", False), ("disconnected", False), ("blocked", False),
], ids=["manual-pause", "automated-pause", "disconnected", "blocked"])
@pytest.mark.parametrize("elapsed,ready", [(119, False), (120, True)])
def test_reported_capture_stop_releases_recap_after_grace_and_upload_drain(
    client, monkeypatch, state, requested_pause, elapsed, ready,
):
    key = insert(client)
    stopped = AT + timedelta(minutes=1)
    report_capture(client, stopped, state, requested_pause=requested_pause)
    monkeypatch.setattr("omi_local.service.utc_now", lambda: stopped + timedelta(seconds=elapsed))

    result = client.post("/v1/recaps/2026-10-16").json()

    assert bool(result["summary"]) == ready
    assert result["waiting_for_pause"] == (0 if ready else 1)
    if ready:
        assert {row["id"] for row in result["sources"]} == {key}
        group = client.app.state.memory.recaps.groups(stopped + timedelta(seconds=elapsed))[0]
        assert group.basis == "capture_stopped"
        purpose = client.app.state.memory.inference.calls[0][1]
        assert "interrupted capture" in purpose
        assert "not evidence of silence or that the conversation ended" in purpose


def test_automatic_summary_starts_at_two_minutes_without_an_extra_batch_wait(client, monkeypatch):
    key = insert(client)
    stopped = AT + timedelta(minutes=1)
    report_capture(client, stopped, "paused", requested_pause=False)
    now = [stopped + timedelta(seconds=119)]
    monkeypatch.setattr("omi_local.service.utc_now", lambda: now[0])
    monkeypatch.setattr("omi_local.worker.utc_now", lambda: now[0])
    memory = client.app.state.memory
    batcher = RecapBatcher()

    asyncio.run(scheduled_recaps(memory, batcher))
    assert memory.store.diary("2026-10-16") is None
    now[0] = stopped + timedelta(seconds=120)
    asyncio.run(scheduled_recaps(memory, batcher))

    result = client.get("/v1/recaps/2026-10-16").json()
    assert result["summary"] and key in result["summary"]
    assert result["waiting_for_pause"] == 0


@pytest.mark.parametrize("changes", [
    {"pending_recordings": 1}, {"pending_seconds": 1}, {"recovery_items": 1},
    {"oldest_capture_at": stamp(AT)}, {"upload_state": "retrying"}, {"device_id": "other"},
])
def test_capture_stop_does_not_finalize_unreceived_audio_or_another_device(client, changes):
    insert(client)
    report_capture(client, AT + timedelta(minutes=1), **changes)
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["summary"] is None
    assert result["waiting_for_pause"] == 1


def test_brief_bluetooth_disconnect_does_not_split_a_conversation(client):
    insert(client)
    phone = report_capture(client, AT + timedelta(minutes=1))
    report_capture(client, AT + timedelta(minutes=2), "capturing", phone=phone, sequence=2)
    insert(client, AT + timedelta(minutes=2), "Continuing the discussion")
    assert client.post("/v1/recaps/2026-10-16").json()["summary"] is None


def test_recapture_preserves_completed_stop_boundary_and_leaves_new_audio_pending(client):
    first = insert(client)
    phone = report_capture(client, AT + timedelta(minutes=1))
    original = client.post("/v1/recaps/2026-10-16").json()
    assert original["summary"] and first in original["summary"]
    report_capture(client, AT + timedelta(minutes=10), "capturing", phone=phone, sequence=2)
    second = insert(client, AT + timedelta(minutes=10), "A later conversation")

    result = client.get("/v1/recaps/2026-10-16").json()
    assert result["summary"] == original["summary"]
    assert {row["id"] for row in result["sources"]} == {first}
    assert result["waiting_for_pause"] == 1
    assert second not in result["summary"]


def test_late_audio_after_stop_reopens_the_unfinished_capture(client):
    first = insert(client)
    report_capture(client, AT + timedelta(minutes=1))
    assert client.post("/v1/recaps/2026-10-16").json()["summary"]
    insert(client, AT + timedelta(minutes=2), "Delayed final audio")
    result = client.get("/v1/recaps/2026-10-16").json()
    assert result["summary"] is None
    assert result["waiting_for_pause"] == 1
    assert client.app.state.memory.store.get_audio(first)["text"]


def test_final_audio_flush_between_pause_and_disconnect_is_included(client):
    key = insert(client, duration=60.02)
    phone = report_capture(client, AT + timedelta(minutes=1), "paused")
    report_capture(client, AT + timedelta(minutes=1, milliseconds=170),
                   phone=phone, sequence=2)
    result = client.post("/v1/recaps/2026-10-16").json()
    assert {source["id"] for source in result["sources"]} == {key}
    assert result["waiting_for_pause"] == 0


def test_repeated_stopped_reports_do_not_regenerate_an_unchanged_recap(client):
    insert(client)
    phone = report_capture(client, AT + timedelta(minutes=1))
    original = client.post("/v1/recaps/2026-10-16").json()
    calls = len(client.app.state.memory.inference.calls)
    report_capture(client, AT + timedelta(minutes=20), phone=phone, sequence=2)
    result = client.post("/v1/recaps/2026-10-16").json()
    assert result["output_id"] == original["output_id"]
    assert len(client.app.state.memory.inference.calls) == calls
