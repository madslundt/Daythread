import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.usage import UsageLedger

AT = "2026-10-18T22:30:00+00:00"


def filtering(recording_id="recording", **changes):
    return {
        "kind": "filter", "recording_id": recording_id, "original_seconds": 960,
        "detected_speech_seconds": 200, "retained_seconds": 240,
        "filter_status": "filtered", "created_at": AT, **changes,
    }


def attempt(**changes):
    return {
        "kind": "stt", "submitted_seconds": 240, "billing": "credits",
        "outcome": "succeeded", "credit_units": 12000, "created_at": AT, **changes,
    }


def summary_filter(**changes):
    return {
        "kind": "summary_filter", "summary_run_id": str(uuid4()),
        "filter_version": "test-v1", "original_characters": 1000,
        "retained_characters": 600, "removed_fillers": 12, "omitted_sources": 1,
        "source_count": 3, "created_at": AT, **changes,
    }


def summary_attempt(**changes):
    return {
        "kind": "llm", "task": "summary", "attempt_id": str(uuid4()),
        "submitted_characters": 900, "input_tokens": 250, "output_tokens": 45,
        "billing": "credits", "credit_units": 1200, "outcome": "succeeded",
        "created_at": AT, **changes,
    }


def test_usage_survives_restart_and_recording_deletion_without_content(tmp_path):
    store = Store(tmp_path)
    ledger = UsageLedger(store)
    key = uuid4()
    store.ingest(key, "omi", datetime.now(UTC), b"audio", 960)
    ledger.record(filtering(str(key), prompt="private prompt", audio="secret", text="secret"))
    ledger.record(attempt(recording_id=str(key)))
    store.delete_audio(str(key))
    totals = UsageLedger(Store(tmp_path)).report()["totals"]
    assert totals["recordings"] == 1
    assert totals["captured_minutes"] == 16
    assert totals["retained_minutes"] == totals["submitted_minutes"] == 4
    assert totals["filtered_percent"] == 75
    assert totals["transcription_credits"] == 12
    with store.connect() as db:
        events = db.execute("SELECT payload FROM usage_events").fetchall()
        assert all(not {"audio", "text", "prompt"} & json.loads(row[0]).keys() for row in events)


def test_usage_schema_and_portable_backup_preserve_measurements(tmp_path):
    store = Store(tmp_path)
    with store.connect() as db:
        # Store creates the schema even without Memory; managed portable exports
        # reconstruct this same schema before copying PostgreSQL table contents.
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 0
    UsageLedger(store).record(attempt())
    archive = store.export_archive()
    with zipfile.ZipFile(archive) as exported:
        database = tmp_path / "snapshot.sqlite3"
        database.write_bytes(exported.read("omi.sqlite3"))
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 1


def test_reprocessing_updates_capture_once_but_counts_extra_provider_attempts(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    ledger.record(filtering())
    ledger.record(filtering(retained_seconds=120, created_at="2026-10-20T12:00:00Z"))
    ledger.record(attempt(submitted_seconds=120))
    ledger.record(attempt(submitted_seconds=120, billing="provider", credit_units=None))
    report = ledger.report("Europe/Copenhagen")
    totals = report["totals"]
    assert totals["captured_minutes"] == 16
    assert totals["retained_minutes"] == 2
    assert totals["submitted_minutes"] == 4
    assert totals["provider_attempts"] == 1
    assert totals["transcription_credits"] == 12
    assert report["days"][0]["date"] == "2026-10-19"
    assert report["credits_complete"]


def test_pending_attempts_survive_restart_then_settle_without_double_counting(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    pending = attempt(attempt_id="attempt", outcome="pending", credit_units=None)
    ledger.record(pending)
    restarted = UsageLedger(Store(tmp_path))
    assert not restarted.report()["credits_complete"]
    assert restarted.report()["totals"]["unknown_credit_attempts"] == 1
    restarted.record({**pending, "outcome": "failed"})
    failed = restarted.report()["totals"]
    assert failed["failed_attempts"] == 1
    assert failed["submitted_minutes"] == 4
    restarted.record({**pending, "outcome": "succeeded", "credit_units": 3500})
    restarted.record(pending)
    report = restarted.report()
    assert report["credits_complete"]
    assert report["totals"]["transcription_credits"] == 3.5
    assert report["totals"]["submitted_minutes"] == 4


def test_managed_operation_dedup_prefers_known_charge_and_preserves_first_day(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    ledger.record(attempt(attempt_id="first", operation_id="operation", credit_units=None))
    ledger.record(attempt(attempt_id="retry", operation_id="operation",
                          created_at="2026-10-20T12:00:00Z"))
    ledger.record(attempt(attempt_id="stale", operation_id="operation",
                          outcome="pending", credit_units=None))
    report = ledger.report()
    assert report["totals"]["submitted_minutes"] == 4
    assert report["totals"]["transcription_credits"] == 12
    assert report["credits_complete"]
    assert report["days"][0]["date"] == "2026-10-18"


def test_unknown_charges_are_incomplete_and_direct_providers_are_separate(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    ledger.record(attempt(credit_units=None, outcome="failed"))
    ledger.record(attempt(billing="unknown", credit_units=None))
    ledger.record(attempt(billing="provider", credit_units=None))
    ledger.record({"kind": "llm", "task": "summary", "billing": "credits",
                   "credit_units": 1250, "outcome": "succeeded"})
    ledger.record({"kind": "llm", "task": "chat", "billing": "credits",
                   "credit_units": 750, "outcome": "succeeded"})
    ledger.record(filtering(filter_status="unavailable", retained_seconds=960,
                            detected_speech_seconds=None))
    report = ledger.report()
    assert not report["credits_complete"]
    totals = report["totals"]
    assert totals["unknown_credit_attempts"] == 2
    assert totals["provider_attempts"] == 1
    assert totals["summary_credits"] == 1.25
    assert totals["other_credits"] == .75
    assert totals["transcription_credits"] == 0
    assert totals["filter_unavailable_recordings"] == 1
    assert totals["filtered_percent"] == 0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_durations_are_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        UsageLedger(Store(tmp_path)).record(attempt(submitted_seconds=value))


def test_usage_endpoint_is_selected_profile_scoped_and_empty_until_measured(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        initial = client.get("/v1/usage")
        assert initial.status_code == 200
        assert initial.headers["Cache-Control"] == "no-store"
        assert initial.json()["totals"]["captured_minutes"] == 0
        client.app.state.memory.usage.record(filtering())
        profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
        assert client.get("/v1/usage").json()["totals"]["captured_minutes"] == 16
        other = client.get("/v1/usage", headers={"X-Omi-Profile": profile})
        assert other.json()["totals"]["captured_minutes"] == 0
        assert client.get("/v1/usage?profile_id=missing").status_code == 404


def test_summary_runs_and_usage_survive_restart_without_storing_text(tmp_path):
    store = Store(tmp_path)
    ledger = UsageLedger(store)
    run = summary_filter(original_text="private transcript", retained_text="private words")
    ledger.record(run)
    ledger.record({**run, "created_at": "2026-10-20T12:00:00Z"})
    ledger.record(summary_attempt(summary_run_id=run["summary_run_id"],
                                  messages=[{"content": "private prompt"}], api_key="secret"))
    report = UsageLedger(Store(tmp_path)).report("Europe/Copenhagen")
    totals = report["totals"]
    assert totals["summary_filter_runs"] == 1
    assert totals["summary_original_characters"] == 1000
    assert totals["summary_retained_characters"] == 600
    assert totals["summary_filtered_percent"] == 40
    assert totals["summary_submitted_characters"] == 900
    assert totals["summary_removed_fillers"] == 12
    assert totals["summary_omitted_sources"] == 1
    assert totals["summary_requests"] == 1
    assert totals["summary_input_tokens"] == 250
    assert totals["summary_output_tokens"] == 45
    assert totals["summary_unknown_character_requests"] == 0
    assert totals["summary_unknown_token_requests"] == 0
    assert report["days"][0]["date"] == "2026-10-19"
    with store.connect() as db:
        persisted = " ".join(row[0] for row in db.execute("SELECT payload FROM usage_events"))
    assert "private" not in persisted
    assert "secret" not in persisted


def test_summary_attempts_count_request_text_but_deduplicate_charge_and_token_receipts(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    first = summary_attempt(operation_id="same-operation")
    ledger.record(first)
    ledger.record(first)
    ledger.record(summary_attempt(operation_id="same-operation", submitted_characters=1000,
                                  created_at="2026-10-20T12:00:00Z"))
    report = ledger.report()
    totals = report["totals"]
    assert totals["summary_requests"] == 2
    assert totals["summary_submitted_characters"] == 1900
    assert totals["summary_credits"] == 1.2
    assert totals["summary_input_tokens"] == 250
    assert totals["summary_output_tokens"] == 45
    assert report["days"][0]["summary_submitted_characters"] == 1000
    assert report["days"][0]["summary_input_tokens"] == 0
    assert report["days"][1]["summary_submitted_characters"] == 900
    assert report["days"][1]["summary_input_tokens"] == 250


def test_summary_pending_and_historical_requests_stay_explicitly_unknown(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    pending = summary_attempt(outcome="pending", credit_units=None,
                              input_tokens=None, output_tokens=None)
    ledger.record(pending)
    # Previously persisted LLM payloads had none of the new character/token fields.
    with ledger.store.connect() as db:
        db.execute("INSERT INTO usage_events VALUES(?,?,?,?)", (
            "historical", AT, json.dumps({"kind": "llm", "task": "weekly",
                                          "outcome": "succeeded", "billing": "credits",
                                          "credit_units": 500}), "succeeded",
        ))
    restarted = UsageLedger(Store(tmp_path))
    totals = restarted.report()["totals"]
    assert totals["summary_requests"] == 2
    assert totals["summary_unknown_token_requests"] == 2
    assert totals["summary_unknown_character_requests"] == 1
    assert totals["summary_input_tokens"] == totals["summary_output_tokens"] == 0
    assert totals["summary_filtered_percent"] is None
    restarted.record({**pending, "outcome": "succeeded", "credit_units": 1200,
                      "input_tokens": 200, "output_tokens": 30})
    restarted.record(pending)
    settled = restarted.report()["totals"]
    assert settled["summary_requests"] == 2
    assert settled["summary_submitted_characters"] == 900
    assert settled["summary_unknown_token_requests"] == 1
    assert settled["summary_input_tokens"] == 200
    assert settled["summary_output_tokens"] == 30


def test_each_logical_summary_pass_counts_filtering_and_all_filler_pass_skips_request(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    ledger.record(summary_filter())
    ledger.record(summary_filter(original_characters=100, retained_characters=0,
                                 omitted_sources=3))
    totals = ledger.report()["totals"]
    assert totals["summary_filter_runs"] == 2
    assert totals["summary_skipped_runs"] == 1
    assert totals["summary_original_characters"] == 1100
    assert totals["summary_retained_characters"] == 600
    assert totals["summary_requests"] == 0
    assert totals["summary_unknown_token_requests"] == 0
    assert totals["summary_credits"] == 0


def test_summary_minutes_are_per_pass_durable_and_historical_duration_stays_unknown(tmp_path):
    store = Store(tmp_path)
    ledger = UsageLedger(store)
    event = summary_filter(source_seconds=1800, source_duration_complete=True)
    ledger.record(event)
    ledger.record(event)  # Re-observation of a logical pass does not add minutes.
    ledger.record(summary_filter(source_seconds=120, source_duration_complete=False))
    ledger.record(summary_filter())  # Older events never measured duration.
    report = UsageLedger(Store(tmp_path)).report("Europe/Copenhagen")
    assert report["totals"]["summary_source_minutes"] == 32
    assert report["totals"]["summary_unknown_duration_runs"] == 2
    assert report["days"][0]["summary_source_minutes"] == 32
    assert report["days"][0]["summary_unknown_duration_runs"] == 2


@pytest.mark.parametrize("changes", [
    {"source_seconds": -1}, {"source_seconds": float("inf")},
    {"source_duration_complete": "yes"}, {"source_duration_complete": True},
])
def test_invalid_summary_source_duration_metadata_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        UsageLedger(Store(tmp_path)).record(summary_filter(**changes))


@pytest.mark.parametrize("field,value", [
    ("retained_characters", 1001), ("original_characters", -1),
    ("removed_fillers", True), ("omitted_sources", 4), ("source_count", 1.2),
])
def test_invalid_summary_measurements_are_rejected(tmp_path, field, value):
    with pytest.raises(ValueError):
        UsageLedger(Store(tmp_path)).record(summary_filter(**{field: value}))
