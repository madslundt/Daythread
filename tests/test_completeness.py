"""Truthful observations and evidence repair, using disposable archives and model fixtures."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_extended import FakeInference, audio_data

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import process_one

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
        )
    ) as client:
        client.app.state.memory.inference = FakeInference()
        yield client


def insert(client, start=AT, duration=60, text="Kitchen delivery Friday", *, source_kind=""):
    store = client.app.state.memory.store
    key = str(uuid4())
    store.ingest(key, "omi", start, audio_data(), duration, source_kind=source_kind)
    if text is not None:
        store.complete(key, text)
    return key


def complete(client, start=AT, end=AT + timedelta(hours=1), **extra):
    result = client.get(
        "/v1/completeness", params={"start": stamp(start), "end": stamp(end), **extra}
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_receipt_freshness_is_not_capture_freshness_and_retries_keep_original_time(client):
    key = insert(client)
    store = client.app.state.memory.store
    first = store.get_audio(key)["received_at"]
    store.ingest(key, "omi", AT, audio_data(), 60)
    status = client.get("/v1/status").json()
    assert status["ingestion"]["last_received_at"] == first
    assert status["ingestion"]["last_captured_at"] == stamp(AT + timedelta(seconds=60))
    assert status["phones"] == []
    assert datetime.fromisoformat(first) > AT + timedelta(hours=1)


def test_imports_do_not_establish_local_audio_freshness(client):
    key = insert(client)
    store = client.app.state.memory.store
    with store.connect() as db:
        db.execute("UPDATE audio SET source_kind='omi_api',audio_available=0 WHERE id=?", (key,))
    status = client.get("/v1/status").json()
    assert status["audio"]["done"] == 1
    assert status["ingestion"] == {"last_received_at": None, "last_captured_at": None}
    result = complete(client)
    assert result["seconds"]["available_audio"] == 0
    assert result["seconds"]["transcript"] == 60
    assert result["counts"]["transcript_only_imports"] == 1
    assert result["counts"]["expired_audio"] == 0


def test_union_tracks_distinguish_overlap_missing_expiry_and_empty_results(client):
    first = insert(client)
    second = insert(client, AT + timedelta(seconds=30))
    third = insert(client, AT + timedelta(seconds=120), text="")
    insert(client, AT + timedelta(seconds=180), text="")
    store = client.app.state.memory.store
    store.complete(third, "", outcome="silence_gate")
    Path(store.get_audio(second)["path"]).unlink()
    with store.connect() as db:
        db.execute("UPDATE audio SET audio_available=0 WHERE id=?", (first,))
    result = complete(client)
    assert result["seconds"]["transcript"] == 90
    assert result["seconds"]["available_audio"] == 120
    assert result["counts"]["missing_audio"] == 1
    assert result["counts"]["expired_audio"] == 1
    assert result["counts"]["silence_gate"] == result["counts"]["empty_stt"] == 1
    assert result["unknown_count"] == 2


def test_missing_files_block_and_can_explicitly_retry_after_restoration(client):
    key = insert(client, text=None)
    memory = client.app.state.memory
    file = Path(memory.store.get_audio(key)["path"])
    file.unlink()
    assert asyncio.run(process_one(memory))
    status = client.get("/v1/status").json()["processing"]
    assert (status["queued"], status["retrying"], status["blocked"]) == (0, 0, 1)
    assert status["recent_errors"][0]["error_code"] == "missing_audio"
    assert client.post(f"/v1/audio/{key}/retry").status_code == 409
    file.write_bytes(audio_data())
    assert client.post(f"/v1/audio/{key}/retry").status_code == 200
    assert client.get("/v1/status").json()["processing"]["queued"] == 1


def test_transient_failure_counts_retry_as_subset_of_queue_and_survives_restart(client):
    key = insert(client, text=None)
    store = client.app.state.memory.store
    row = store.claim()
    store.retry(row, "Transcription failed: TimeoutError")
    processing = Store(store.root).observations()["processing"]
    assert (processing["queued"], processing["retrying"], processing["blocked"]) == (1, 1, 0)
    assert processing["oldest_received_at"] == store.get_audio(key)["received_at"]
    assert store.get_audio(key)["processing_started_at"] is not None


def test_processing_stages_and_eta_use_completed_timings_and_current_elapsed_time(client):
    store = client.app.state.memory.store
    now = datetime.now(UTC)
    sample = insert(client, duration=60)
    store.start_processing(sample)
    store.finish_processing(sample)
    with store.connect() as db:
        db.execute(
            "UPDATE processing_progress SET started_at=?,finished_at=? WHERE audio_id=?",
            (stamp(now - timedelta(seconds=40)), stamp(now - timedelta(seconds=10)), sample),
        )
    active = insert(client, start=AT + timedelta(minutes=1), duration=60, text=None)
    insert(client, start=AT + timedelta(minutes=2), duration=120, text=None)
    assert store.claim()["id"] == active
    store.start_processing(active)
    store.update_processing(active, "transcribing", 1, 2)
    with store.connect() as db:
        db.execute("UPDATE processing_progress SET started_at=? WHERE audio_id=?",
                   (stamp(now - timedelta(seconds=10)), active))
    processing = store.observations(now)["processing"]
    assert processing["queued"] == 1 and processing["transcribing"] == 1
    assert processing["active"] == [{
        "id": active, "stage": "transcribing", "completed_chunks": 1,
        "total_chunks": 2, "elapsed_seconds": 10,
    }]
    assert processing["estimate_sample_count"] == 1
    assert processing["estimated_remaining_seconds"] == 80
    assert Store(store.root).observations(now)["processing"]["active"] == processing["active"]
    assert store.observations(now + timedelta(minutes=2))["processing"][
        "estimated_remaining_seconds"
    ] is None
    store.retry(store.get_audio(active), "Transcription failed: TimeoutError")
    retrying = store.observations(now)["processing"]
    assert retrying["estimate_sample_count"] == 1
    assert retrying["estimated_remaining_seconds"] is None


def test_processing_eta_is_unknown_without_samples_or_during_retry(client):
    store = client.app.state.memory.store
    key = insert(client, text=None)
    assert store.observations()["processing"]["estimated_remaining_seconds"] is None
    row = store.claim()
    store.start_processing(key)
    store.update_processing(key, "transcribing", 0, 2)
    store.retry(row, "Transcription failed: TimeoutError")
    processing = store.observations()["processing"]
    assert processing["active"] == []
    assert processing["estimated_remaining_seconds"] is None


def test_completed_work_does_not_keep_a_progress_indicator(client):
    store = client.app.state.memory.store
    key = insert(client, text=None)
    store.claim()
    store.start_processing(key)
    store.update_processing(key, "transcribing", 1, 1)
    store.complete(key, "Done", claimed=True)
    store.finish_processing(key)
    processing = store.observations()["processing"]
    assert processing["active"] == []
    assert processing["estimated_remaining_seconds"] == 0


def observation(sequence=1):
    return {
        "sequence": sequence,
        "observed_at": stamp(AT),
        "capture_state": "disconnected",
        "cause": "connection",
        "requested_pause": False,
        "pending_recordings": 2,
        "recovery_items": 1,
        "pending_seconds": 61,
        "upload_policy": "wifi_only",
        "upload_state": "waiting",
        "storage_bytes": 1000,
        "storage_budget_bytes": 2_000_000_000,
    }


def test_phone_observation_is_ordered_idempotent_and_stale_after_late_delivery(client):
    path = f"/v1/phones/{uuid4()}/status"
    body = observation(2)
    saved = client.put(path, json=body)
    assert saved.status_code == 200
    assert client.put(path, json=body).json()["received_at"] == saved.json()["received_at"]
    assert client.put(path, json=observation(1)).status_code == 409
    assert client.put(path, json=body | {"pending_recordings": 3}).status_code == 409
    phone = client.get("/v1/status").json()["phones"][0]
    assert not phone["fresh"]
    assert phone["capture_state"] == "disconnected"
    assert phone["upload_policy"] == "wifi_only"
    assert (
        client.put(
            path,
            json=body
            | {"sequence": 3, "observed_at": stamp(datetime.now(UTC) + timedelta(hours=1))},
        ).status_code
        == 422
    )


def test_pause_transitions_are_idempotent_and_do_not_turn_unknown_into_capture(client):
    phone = str(uuid4())
    body = {"phone_id": phone, "occurred_at": stamp(AT), "state": "paused", "cause": "user_pause"}
    path = f"/v1/capture-state/{uuid4()}"
    assert client.put(path, json=body).status_code == 200
    assert client.put(path, json=body).status_code == 200
    assert client.put(path, json=body | {"state": "capturing"}).status_code == 409
    assert (
        client.put(f"/v1/capture-state/{uuid4()}", json=body | {"cause": "storage"}).status_code
        == 422
    )
    assert (
        client.put(
            f"/v1/capture-state/{uuid4()}",
            json=body
            | {
                "occurred_at": stamp(AT + timedelta(minutes=10)),
                "state": "unknown",
                "cause": "resumed",
            },
        ).status_code
        == 200
    )
    result = complete(client)
    assert result["seconds"]["intentional_pause"] == 600
    assert result["seconds"]["available_audio"] == 0
    assert result["unknown_intervals"][0]["start"] == stamp(AT + timedelta(minutes=10))
    assert client.get("/v1/status").json()["capture_paused"] is False


def test_gap_count_is_exact_details_paginate_and_legacy_reasons_stay_unknown(client):
    store = client.app.state.memory.store
    with store.connect() as db:
        db.executemany(
            "INSERT INTO capture_gaps VALUES(?,?,?,?,?,?)",
            [
                (
                    str(uuid4()),
                    "legacy",
                    stamp(AT),
                    stamp(AT + timedelta(seconds=1)),
                    "Disconnected or paused",
                    "unknown",
                )
                for _ in range(1001)
            ],
        )
    assert client.get("/v1/status").json()["capture_gaps"] == 1001
    result = complete(client, limit=1)
    assert result["capture_gap_count"] == 1001 and result["truncated"]
    assert result["capture_gaps"][0]["cause"] == "unknown"
    assert result["seconds"]["reported_interruption"] == 1


def test_output_identity_selection_feedback_correction_and_resolution(client):
    key = insert(client)
    other = insert(client, AT + timedelta(minutes=10), text="Unrelated word")
    response = client.post("/v1/chat", json={"question": "Kitchen", "limit": 1}).json()
    assert response["output_id"] == response["message_id"]
    evidence = client.get(f"/v1/outputs/{response['output_id']}/evidence").json()
    assert [s["id"] for s in evidence["selected"]] == [key]
    assert evidence["eligible_transcripts"] == 2
    assert evidence["cited_ids"] == [key]
    session = client.get(f"/v1/chat/sessions/{response['session_id']}").json()
    assert session["messages"][-1]["id"] == response["message_id"]
    report = {
        "issue": "missing",
        "output_id": response["output_id"],
        "expected_source_ids": [other],
        "note": "Expected this detail",
    }
    path = f"/v1/feedback/{uuid4()}"
    first = client.put(path, json=report)
    assert first.status_code == 200
    assert first.json()["suggested_stage"] == "retrieval"
    assert client.put(path, json=report).json() == first.json()
    assert client.put(path, json=report | {"note": "different"}).status_code == 409
    assert (
        client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected transcript"}).status_code
        == 200
    )
    assert client.get(f"/v1/outputs/{response['output_id']}/evidence").json()[
        "changed_source_ids"
    ] == [key]
    event = {
        "event_id": str(uuid4()),
        "status": "resolved",
        "stage": "retrieval",
        "note": "Re-asked with expected source",
    }
    assert client.post(path + "/events", json=event).json()["status"] == "resolved"
    assert len(client.post(path + "/events", json=event).json()["events"]) == 1
    assert client.get("/v1/feedback").json()["total"] == 1
    assert client.delete(path).status_code == 200
    assert client.get("/v1/feedback").json()["total"] == 0


def test_deletion_and_transcript_retention_purge_feedback_and_outputs(client):
    key = insert(client)
    response = client.post("/v1/chat", json={"question": "Kitchen"}).json()
    path = f"/v1/feedback/{uuid4()}"
    body = {
        "issue": "incorrect",
        "source_id": key,
        "output_id": response["output_id"],
        "note": "Private detail",
    }
    assert client.put(path, json=body).status_code == 200
    assert client.delete(f"/v1/audio/{key}").status_code == 200
    assert client.get("/v1/feedback").json()["total"] == 0
    assert client.get(f"/v1/outputs/{response['output_id']}/evidence").status_code == 404
    assert client.put(path, json=body).status_code == 409
    key = insert(client)
    assert (
        client.put(
            f"/v1/feedback/{uuid4()}", json={"issue": "missing", "source_id": key}
        ).status_code
        == 200
    )
    client.app.state.memory.store.apply_retention(0, 1, now=AT + timedelta(days=3))
    assert client.get("/v1/feedback").json()["total"] == 0


def test_audio_only_retention_preserves_transcript_evidence_and_feedback(client):
    key = insert(client)
    response = client.post("/v1/chat", json={"question": "Kitchen"}).json()
    assert (
        client.put(
            f"/v1/feedback/{uuid4()}", json={"issue": "missing", "source_id": key}
        ).status_code
        == 200
    )
    client.app.state.memory.store.apply_retention(1, 0, now=AT + timedelta(days=3))
    assert client.get("/v1/feedback").json()["total"] == 1
    assert (
        client.get(f"/v1/outputs/{response['output_id']}/evidence").json()["changed_source_ids"]
        == []
    )
    assert complete(client)["counts"]["expired_audio"] == 1
    session = client.get(f"/v1/chat/sessions/{response['session_id']}").json()
    saved_source = session["messages"][-1]["sources"][0]
    assert saved_source["audio_availability"] == "expired"
    assert saved_source["text"] == "Kitchen delivery Friday"


def test_recap_version_id_matches_output_and_survives_backup(client, tmp_path):
    insert(client, source_kind="manual_upload")
    recap = client.post("/v1/recaps/2026-10-15").json()
    versions = client.get("/v1/diary/2026-10-15/versions").json()["items"]
    assert versions[0]["id"] == recap["output_id"]
    store = client.app.state.memory.store
    report_id = str(uuid4())
    assert (
        client.put(
            f"/v1/feedback/{report_id}", json={"issue": "missing", "output_id": recap["output_id"]}
        ).status_code
        == 200
    )
    phone_id = str(uuid4())
    recent = observation() | {"observed_at": stamp(datetime.now(UTC))}
    assert client.put(f"/v1/phones/{phone_id}/status", json=recent).status_code == 200
    assert store.observations()["phones"][0]["fresh"]
    output = store.export_archive()
    destination = tmp_path / "restored"
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "archive", Path(__file__).parents[1] / "scripts/archive.py"
    )
    archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archive)
    archive.restore(output, destination, 100_000_000)
    restored = Store(destination)
    assert restored.feedback()["items"][0]["id"] == report_id
    assert restored.evidence(recap["output_id"])["selected"] == recap["evidence"]["selected"]
    restored_phone = restored.observations()["phones"][0]
    assert restored_phone["historical"] and not restored_phone["fresh"]
    restored.save_phone(phone_id, observation(2) | {"observed_at": stamp(datetime.now(UTC))})
    assert restored.observations()["phones"][0]["fresh"]
    assert recap["output_id"] in client.get("/v1/diary/2026-10-15.md").text


def test_legacy_migration_does_not_invent_receive_times(tmp_path):
    from omi_local.store import SCHEMA

    with sqlite3.connect(tmp_path / "omi.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path) VALUES(?,?,?,?,?,?)",
            (
                str(uuid4()),
                "legacy",
                stamp(AT),
                stamp(AT + timedelta(seconds=1)),
                "digest",
                "old.wav",
            ),
        )
    store = Store(tmp_path)
    assert store.observations()["ingestion"]["last_received_at"] is None
    assert store.observations()["ingestion"]["last_captured_at"] is not None


def test_empty_or_invalid_feedback_references_are_rejected(client):
    path = f"/v1/feedback/{uuid4()}"
    assert client.put(path, json={"issue": "missing"}).status_code == 422
    assert client.put(path, json={"issue": "missing", "source_id": str(uuid4())}).status_code == 409
    assert (
        client.put(
            path, json={"issue": "missing", "start": stamp(AT), "end": stamp(AT)}
        ).status_code
        == 422
    )
    assert (
        client.put(
            path,
            json={
                "issue": "missing",
                "start": stamp(AT),
                "end": stamp(AT + timedelta(minutes=1)),
                "note": "x" * 2001,
            },
        ).status_code
        == 422
    )


def test_resend_repairs_missing_file_without_refreshing_ingestion_or_reversing_retention(client):
    key = insert(client, text=None)
    store = client.app.state.memory.store
    original = store.get_audio(key)
    Path(original["path"]).unlink()
    asyncio.run(process_one(client.app.state.memory))
    repaired = store.ingest(key, "omi", AT, audio_data(), 60)
    assert repaired["state"] == "queued"
    assert repaired["received_at"] == original["received_at"]
    store.apply_retention(1, 0, now=AT + timedelta(days=3))
    retained = store.ingest(key, "omi", AT, audio_data(), 60)
    assert retained["audio_available"] == 0 and retained["state"] == "expired"
    assert not Path(retained["path"]).exists()


@pytest.mark.parametrize("pending_kind,regrouped", [("manual_upload", True), ("omi_api", False)])
def test_pending_completion_during_generation_preserves_snapshot_evidence(
    client, pending_kind, regrouped,
):
    key = insert(client, source_kind="manual_upload")
    pending = insert(client, AT + timedelta(minutes=2), text=None, source_kind=pending_kind)
    memory = client.app.state.memory

    class CompleteDuringSummary(FakeInference):
        finished = False

        async def summarize(self, sources, purpose):
            if not self.finished:
                memory.store.complete(pending, "Late transcript")
                self.finished = True
            return "Recap " + " ".join(f"[{source['id']}]" for source in sources)

    memory.inference = CompleteDuringSummary()
    response = client.post("/v1/recaps/2026-10-15")
    if regrouped:
        # Identical uploaded audio is linked when transcription completes. The
        # old selection is no longer a whole group and must not be published.
        assert response.status_code == 409
        with memory.store.connect() as db:
            assert db.execute("SELECT count(*) FROM output_evidence").fetchone()[0] == 0
    else:
        initial = response.json()
        assert initial["summary"] and initial["stale"]
        assert initial["evidence"]["pending_chunks"] == 1
        assert {r["id"] for r in initial["evidence"]["selected"]} == {key}
    result = client.post("/v1/recaps/2026-10-15").json()
    assert result["evidence"]["eligible_transcripts"] == 2
    assert result["evidence"]["pending_chunks"] == 0
    assert {r["id"] for r in result["evidence"]["selected"]} == {key, pending}
    assert client.get("/v1/status").json()["processing"]["queued"] == 0


def test_deletion_during_generation_cannot_leave_evidence_or_feedback(client):
    key = insert(client, source_kind="manual_upload")
    memory = client.app.state.memory

    class DeleteDuringSummary(FakeInference):
        async def summarize(self, sources, purpose):
            memory.store.delete_audio(key)
            return "Recap " + " ".join(f"[{source['id']}]" for source in sources)

    memory.inference = DeleteDuringSummary()
    assert client.post("/v1/recaps/2026-10-15").status_code == 409
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM output_evidence").fetchone()[0] == 0
    assert client.get("/v1/feedback").json()["total"] == 0


def test_uncited_answer_records_zero_citations_without_claiming_verification(client):
    insert(client)

    class Uncited(FakeInference):
        async def chat(self, *args):
            return "An uncited answer."

    client.app.state.memory.inference = Uncited()
    result = client.post("/v1/chat", json={"question": "Kitchen"}).json()
    assert len(result["evidence"]["selected"]) == 1
    assert result["evidence"]["cited_ids"] == []
    assert "do not verify" in result["evidence"]["limitation"]


def test_reduction_tracks_source_ids_remaining_in_final_prompt_without_claiming_support(tmp_path):
    import httpx

    from omi_local.inference import Inference

    sources = [
        {"id": str(uuid4()), "started_at": stamp(AT), "text": "detail " * 3000} for _ in range(3)
    ]

    async def exercise():
        async with httpx.AsyncClient() as client:
            inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)

            async def generate(task, evidence):
                # A retained ID must belong to the current batch; simulate
                # omission of later batches without fabricating a cross-batch cite.
                return (f"Reduced only [{sources[0]['id']}]" if sources[0]["id"] in evidence
                        else "[[NO_RECAP_CONTENT]]")

            inference.generate = generate
            answer, trace = await inference.summarize_traced(sources, "fixture recap")
            assert trace["reduction_used"]
            assert trace["model_calls"] > 1
            assert trace["final_prompt_source_ids"] == [sources[0]["id"]]
            assert sources[0]["id"] in answer

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("zone", "day", "hours"),
    [("Europe/Copenhagen", "2026-03-29", 23), ("America/New_York", "2025-11-02", 25)],
)
def test_completeness_preserves_real_local_day_lengths(client, zone, day, hours):
    from datetime import date

    memory = client.app.state.memory
    memory.settings = memory.settings.model_copy(update={"timezone": zone})
    start, end = memory.day_window(date.fromisoformat(day))
    result = complete(client, start, end)
    assert (end - start).total_seconds() == hours * 3600
    assert len(result["unknown_intervals"]) == 1
    unknown = result["unknown_intervals"][0]
    assert (
        datetime.fromisoformat(unknown["end"]) - datetime.fromisoformat(unknown["start"])
    ).total_seconds() == hours * 3600


def test_future_time_is_not_reported_as_missing_capture(client):
    start = datetime.now(UTC) + timedelta(days=1)
    result = complete(client, start, start + timedelta(hours=1))
    assert result["unknown_intervals"] == []
    assert result["seconds"]["available_audio"] == 0


def test_pending_or_expired_expected_sources_are_not_misdiagnosed_as_retrieval(client):
    insert(client)
    pending = insert(client, AT + timedelta(minutes=3), text=None)
    answer = client.post("/v1/chat", json={"question": "Kitchen"}).json()
    body = {"issue": "missing", "output_id": answer["output_id"], "expected_source_ids": [pending]}
    path = f"/v1/feedback/{uuid4()}"
    assert client.put(path, json=body).json()["suggested_stage"] == "transcription_pending"
    with client.app.state.memory.store.connect() as db:
        db.execute("UPDATE audio SET state='expired' WHERE id=?", (pending,))
    assert (
        client.get("/v1/feedback").json()["items"][0]["suggested_stage"] == "evidence_unavailable"
    )
