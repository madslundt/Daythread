"""Exact-scope durable generation, retry polling, and stale result protection."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_conversation_tags import edit, source_path
from test_location_zones import save as save_zone
from test_recaps import AT, insert
from test_recaps import client as client
from test_scoped_recaps import DAY, PATH, located, tagged

from omi_local.domain import EvidenceChanged
from omi_local.generation_jobs import GenerationRequest


def jobs(client):
    return client.app.state.profiles.runtimes["default"].jobs


def process(client):
    assert asyncio.run(jobs(client).process_one()) is True


def retry(client, tag):
    response = client.post("/v1/jobs", json={
        "id": str(uuid4()), "kind": "recap", "parameters": {"day": DAY, "tag_id": tag},
    })
    assert response.status_code == 202, response.text
    return response.json()


def test_get_enqueues_without_inference_and_polls_the_same_exact_scope(client):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    memory = client.app.state.memory
    first_response = client.get(PATH, params={"tag_id": tag})
    first = first_response.json()
    assert first_response.headers["cache-control"] == "no-store"
    assert first["stale"] is True and first["summary"] is None
    assert first["generation_state"] == "queued"
    assert first["generation_error"] is None
    for _ in range(2):
        assert client.get(PATH, params={"tag_id": tag}).json() == first
    assert memory.inference.calls == []
    assert jobs(client).get(first["generation_job_id"])["parameters"] == {
        "day": DAY, "tag_id": tag, "scope_fingerprint": first["scope_fingerprint"],
    }
    process(client)
    completed = client.get(PATH, params={"tag_id": tag}).json()
    assert completed["generation_state"] == "completed"
    assert completed["generation_job_id"] == first["generation_job_id"]
    assert completed["summary"] and completed["stale"] is False
    assert completed["sources"][0]["id"] == key
    assert memory.store.diary(DAY) is None
    with memory.store.connect() as db:
        row = db.execute("SELECT notified FROM generation_jobs").fetchone()
        assert row["notified"] == 1  # Scope cannot deep-link into the full-day recap.
        assert db.execute("SELECT count(*) FROM notifications").fetchone()[0] == 0


def test_failed_scope_waits_for_explicit_retry_and_poll_reflects_new_job(client):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    first = client.get(PATH, params={"tag_id": tag}).json()
    original = client.app.state.memory.inference.summarize

    async def fail(*args):
        raise RuntimeError("Do not expose provider transcript or token")

    client.app.state.memory.inference.summarize = fail
    process(client)
    failed = client.get(PATH, params={"tag_id": tag}).json()
    assert failed["generation_state"] == "failed" and failed["summary"] is None
    assert failed["generation_job_id"] == first["generation_job_id"]
    assert "token" not in failed["generation_error"]
    assert client.get(PATH, params={"tag_id": tag}).json() == failed
    client.app.state.memory.inference.summarize = original
    new = retry(client, tag)
    queued = client.get(PATH, params={"tag_id": tag}).json()
    assert queued["generation_job_id"] == new["id"] != first["generation_job_id"]
    assert queued["generation_state"] == "queued" and queued["generation_error"] is None
    with jobs(client).store.connect() as db:
        db.execute("UPDATE generation_jobs SET state='running' WHERE id=?", (new["id"],))
    assert client.get(PATH, params={"tag_id": tag}).json()["generation_state"] == "running"
    with jobs(client).store.connect() as db:
        db.execute("UPDATE generation_jobs SET state='queued' WHERE id=?", (new["id"],))
    process(client)
    assert client.get(PATH, params={"tag_id": tag}).json()["generation_state"] == "completed"


@pytest.mark.parametrize("mutation", ["membership", "deleted_tag", "zone", "transcript"])
def test_saved_durable_result_revalidates_membership_and_originals(client, mutation):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    located(client, key)
    zone = save_zone(client).json()
    initial = client.get(PATH, params={"tag_id": tag, "zone_id": zone["id"]}).json()
    process(client)
    old_id = initial["generation_job_id"]
    assert jobs(client).get(old_id)["result"]["summary"]
    store = client.app.state.memory.store
    if mutation == "membership":
        edit(client, source_path({"id": key}), action="remove", tag_id=tag)
    elif mutation == "deleted_tag":
        with store.connect() as db:
            db.execute("DELETE FROM conversation_tags WHERE id=?", (tag,))
    elif mutation == "zone":
        save_zone(client, key=zone["id"], expected_version=1, name="Renamed work")
    else:
        store.correct_transcript(key, "Corrected evidence")
    old = client.get(f"/v1/jobs/{old_id}").json()
    assert old["state"] == "failed" and old["result"] is None
    assert old["error"] == "Recorded evidence changed; generate again"
    with store.connect() as db:
        assert db.execute("SELECT result FROM generation_jobs WHERE id=?", (old_id,)).fetchone()[
            0] is None
    fresh = client.get(PATH, params={"tag_id": tag, "zone_id": zone["id"]})
    if mutation == "deleted_tag":
        assert fresh.status_code == 404
    else:
        assert fresh.json()["summary"] is None
        assert fresh.json()["generation_job_id"] != old_id


def test_queued_scope_rejects_changed_selection_and_does_not_accept_client_fingerprint(client):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    initial = client.get(PATH, params={"tag_id": tag}).json()
    edit(client, source_path({"id": key}), action="remove", tag_id=tag)
    process(client)
    old = jobs(client).get(initial["generation_job_id"])
    assert old["state"] == "failed" and old["result"] is None
    assert client.app.state.memory.inference.calls == []
    assert client.post("/v1/jobs", json={
        "id": str(uuid4()), "kind": "recap",
        "parameters": {"day": DAY, "tag_id": tag, "scope_fingerprint": "pretend-current"},
    }).status_code == 422
    assert client.post("/v1/jobs", json={
        "id": str(uuid4()), "kind": "recap", "parameters": {"day": DAY, "tag_id": str(uuid4())},
    }).status_code == 404


def test_membership_race_after_generation_cannot_commit_durable_result(client):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    job = jobs(client).enqueue(GenerationRequest(
        id=uuid4(), kind="recap", parameters={"day": DAY, "tag_id": tag},
    ))
    result = asyncio.run(jobs(client).execute(job))
    edit(client, source_path({"id": key}), action="remove", tag_id=tag)
    with pytest.raises(EvidenceChanged):
        jobs(client).complete(job, result)
    assert jobs(client).get(job["id"])["result"] is None


def test_full_day_retry_identity_stays_compatible_and_scope_retry_freezes_snapshot(client):
    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    full = {"id": str(uuid4()), "kind": "recap", "parameters": {"day": DAY}}
    first = client.post("/v1/jobs", json=full).json()
    assert first["parameters"] == {"day": DAY}
    assert client.post("/v1/jobs", json=full).json()["id"] == first["id"]
    scope = retry(client, tag)
    insert(client, AT + timedelta(minutes=10), "Unrelated recording", "manual_upload")
    edit(client, source_path({"id": key}), action="remove", tag_id=tag)
    body = {"id": scope["id"], "kind": "recap", "parameters": {"day": DAY, "tag_id": tag}}
    again = client.post("/v1/jobs", json=body)
    assert again.status_code == 202
    assert again.json()["parameters"] == scope["parameters"]
    with jobs(client).store.connect() as db:
        assert json.loads(db.execute(
            "SELECT parameters FROM generation_jobs WHERE id=?", (scope["id"],)
        ).fetchone()[0])["scope_fingerprint"] == scope["parameters"]["scope_fingerprint"]


@pytest.mark.parametrize("cause", ["eviction", "unrelated_audio_change"])
def test_disposable_cache_loss_recovers_only_validated_exact_scope_job_result(client, cause):
    from omi_local.scoped_recap_cache import ScopedRecapCache

    key = insert(client, kind="manual_upload")
    tag = tagged(client, key)
    initial = client.get(PATH, params={"tag_id": tag}).json()
    process(client)
    completed = client.get(PATH, params={"tag_id": tag}).json()
    store = client.app.state.memory.store
    calls = len(client.app.state.memory.inference.calls)
    if cause == "eviction":
        cache = ScopedRecapCache(store)
        for index in range(70):
            cache.save(str(index), "Other cached note", [], lambda db: None)
    else:
        unrelated = insert(client, AT + timedelta(minutes=10), "Unrelated", "manual_upload")
        store.correct_transcript(unrelated, "Unrelated correction")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM scoped_recap_cache WHERE fingerprint=?", (
            "day:" + initial["scope_fingerprint"],
        )).fetchone()[0] == 0
    recovered = client.get(PATH, params={"tag_id": tag}).json()
    assert recovered["generation_state"] == "completed" and recovered["stale"] is False
    assert recovered["scope_fingerprint"] == completed["scope_fingerprint"]
    assert recovered["summary"] == completed["summary"]
    assert recovered["sources"] == completed["sources"]
    assert len(client.app.state.memory.inference.calls) == calls
    # Recovery must not bypass a subsequent selection change.
    edit(client, source_path({"id": key}), action="remove", tag_id=tag)
    invalid = client.get(PATH, params={"tag_id": tag}).json()
    assert invalid["summary"] is None and invalid["no_matching_sources"] is True


@pytest.mark.parametrize("removal", ["delete", "retention"])
def test_removed_scoped_prose_is_absent_from_portable_backup(client, removal):
    import io
    import zipfile

    from omi_local.archive_stream import write_archive

    private = "Distinct private scoped evidence to remove from backup"
    key = insert(client, text=private, kind="manual_upload")
    tag = tagged(client, key)
    initial = client.get(PATH, params={"tag_id": tag}).json()
    process(client)
    assert private in jobs(client).get(initial["generation_job_id"])["result"]["summary"]
    store = client.app.state.memory.store
    if removal == "delete":
        assert store.delete_audio(key)
    else:
        assert store.apply_retention(0, 1, AT + timedelta(days=2))["transcripts_removed"] == 1
    assert jobs(client).get(initial["generation_job_id"])["result"] is None
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM scoped_recap_cache").fetchone()[0] == 0
    output = io.BytesIO()
    write_archive(store, output)
    with zipfile.ZipFile(output) as archive:
        assert private.encode() not in archive.read("omi.sqlite3")
