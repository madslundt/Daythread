"""Manual evidence contracts, including lifecycle races and ambiguous identities."""

import importlib.util
import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from omi_local.api import create_app
from omi_local.label_models import LabelError, LabelMutation
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


@pytest.fixture
def client(tmp_path):
    config = Settings(
        data_dir=tmp_path, timezone="Europe/Copenhagen", worker_enabled=False, _env_file=None
    )
    with TestClient(create_app(config)) as session:
        yield session


def source(client, at=AT, key=None):
    store = client.app.state.memory.store
    key = key or str(uuid4())
    with store.connect() as db:
        db.execute(
            "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text,"
            "audio_available,source_kind,source_id,segments) "
            "VALUES(?,?,?,?,?,?,'done',?,0,'omi_api',?,?)",
            (
                key,
                "omi-api",
                at.isoformat(),
                (at + timedelta(minutes=2)).isoformat(),
                "unused",
                str(store.audio_dir / f"{key}.wav"),
                "Alex: Discuss the garden",
                key,
                json.dumps([{"speaker_id": 1, "speaker_name": "Alex"}]),
            ),
        )
    return client.get(f"/v1/labels/sources/{key}").json()["source"]


def command(client, action, key=None, **kwargs):
    if action in ("entity.save", "link.save", "note.save"):
        kwargs.setdefault("create", key is None)
    return {
        "action": action,
        "id": key or str(uuid4()),
        "mutation_id": str(uuid4()),
        "expected_revision": client.get("/v1/labels").json()["revision"],
        **kwargs,
    }


def apply(client, action, key=None, **kwargs):
    body = command(client, action, key, **kwargs)
    response = client.post("/v1/labels/mutations", json=body)
    assert response.status_code == 200, response.text
    return body["id"]


def person(client, name="Alex", **kwargs):
    return apply(
        client,
        "entity.save",
        value={"kind": "person", "name": name, "aliases": ["Ålex", "Ａｌｅｘ"], **kwargs},
    )


def ref(value):
    return {"id": value["id"], "revision": value["source_version"]}


def link(client, entity, src, relation="related"):
    return apply(client, "link.save", entity_id=entity, source=ref(src), relation=relation)


def note(client, entity, sources, kind="decision"):
    return apply(
        client,
        "note.save",
        kind=kind,
        title="Plant a tree",
        body="User annotation",
        sources=[ref(s) for s in sources],
        entity_ids=[entity],
    )


def timeline(client, key, **query):
    response = client.get(f"/v1/labels/{key}/timeline", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def test_ambiguous_aliases_and_source_local_speakers_are_not_participation(client):
    first, second = person(client, description="Work"), person(client, description="Cycling")
    sources = [source(client), source(client, AT + timedelta(days=1))]
    assert len(client.get("/v1/labels", params={"q": "åLEX"}).json()["items"]) == 2
    assert len(client.get("/v1/labels", params={"q": "Ａｌｅｘ"}).json()["items"]) == 2
    assert not timeline(client, first)["items"]
    link(client, first, sources[0], "mentioned")
    link(client, second, sources[0], "participant")
    assert timeline(client, first)["items"][0]["relation"] == "mentioned"
    assert len(timeline(client, second)["items"]) == 1
    assert timeline(client, first)["items"][0]["sources"][0]["segments"][0]["speaker_id"] == 1


def test_cas_restart_idempotence_and_differing_retries(client):
    entity = person(client)
    src = source(client)
    body = command(client, "link.save", entity_id=entity, source=ref(src), relation="related")
    assert client.post("/v1/labels/mutations", json=body).status_code == 200
    assert client.post("/v1/labels/mutations", json=body).status_code == 200
    changed = body | {"relation": "participant"}
    assert client.post("/v1/labels/mutations", json=changed).status_code == 409
    stale = body | {"mutation_id": str(uuid4())}
    assert client.post("/v1/labels/mutations", json=stale).status_code == 409
    store = Store(client.app.state.memory.store.root)
    assert store.label_directory()["items"][0]["id"] == entity
    assert len(store.label_timeline(entity)["items"]) == 1


def test_manual_groups_notes_dates_and_revision_guarded_pagination(client):
    entity = person(client)
    a, b = source(client), source(client, AT + timedelta(days=1))
    link(client, entity, a)
    link(client, entity, b)
    conversation = note(client, entity, [a, b], "conversation")
    note(client, entity, [a], "open_question")
    all_items = timeline(client, entity)
    assert len(all_items["items"]) == 4
    group = next(i for i in all_items["items"] if i["id"] == conversation)
    assert len(group["sources"]) == 2
    assert len(timeline(client, entity, start_day="2026-10-26", end_day="2026-10-26")["items"]) == 2
    page = timeline(client, entity, limit=2)
    next_page = timeline(client, entity, offset=2, revision=page["revision"])
    assert not {i["id"] for i in page["items"]} & {i["id"] for i in next_page["items"]}
    person(client, "Elsewhere")
    assert (
        client.get(
            f"/v1/labels/{entity}/timeline", params={"offset": 2, "revision": page["revision"]}
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/v1/labels/{entity}/timeline",
            params={"start_day": "2026-10-26", "end_day": "2026-10-25"},
        ).status_code
        == 422
    )


def test_correction_review_reaffirm_and_purge_with_inflight_writes(client):
    entity = person(client)
    a, b = source(client), source(client)
    association = link(client, entity, a, "participant")
    note(client, entity, [a, b])
    pending = command(
        client, "note.save", kind="decision", title="stale", sources=[ref(a)], entity_ids=[entity]
    )
    corrected = client.patch(
        f"/v1/transcripts/{a['id']}", json={"text": "A corrected statement"}
    ).json()
    assert corrected["source_version"] != a["source_version"]
    assert all(i["needs_review"] for i in timeline(client, entity)["items"])
    # Even an updated collection token cannot reaffirm an old source revision.
    pending["expected_revision"] = client.get("/v1/labels").json()["revision"]
    assert client.post("/v1/labels/mutations", json=pending).status_code == 409
    apply(
        client,
        "link.save",
        association,
        entity_id=entity,
        source=ref(corrected),
        relation="participant",
    )
    assert not next(i for i in timeline(client, entity)["items"] if i["id"] == association)[
        "needs_review"
    ]
    assert client.delete(f"/v1/audio/{a['id']}").status_code == 200
    assert not timeline(client, entity)["items"]
    pending["expected_revision"] = client.get("/v1/labels").json()["revision"]
    assert client.post("/v1/labels/mutations", json=pending).status_code == 409
    with client.app.state.memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM label_events").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM note_sources").fetchone()[0] == 0
        assert db.execute("SELECT source_id FROM import_deletions").fetchone()[0] == a["id"]
    recreated = source(client, key=a["id"])
    assert recreated["source_version"] != a["source_version"]
    assert client.post("/v1/labels/mutations", json=pending).status_code == 409


def test_merge_split_rename_and_label_deletion_preserve_sources(client):
    a, b = person(client, "Work Alex"), person(client, "Cycling Alex")
    src = source(client)
    mentioned = link(client, a, src, "mentioned")
    participant = link(client, b, src, "participant")
    annotation = note(client, a, [src])
    apply(client, "entity.merge", a, target_id=b)
    result = timeline(client, a)
    assert result["entity"]["id"] == b
    assert {i.get("relation") for i in result["items"]} >= {"mentioned", "participant"}
    split = str(uuid4())
    apply(
        client,
        "entity.split",
        b,
        new_id=split,
        value={"kind": "person", "name": "Other Alex"},
        link_ids=[mentioned],
        note_ids=[annotation],
    )
    assert {i["id"] for i in timeline(client, split)["items"]} == {mentioned, annotation}
    assert {i["id"] for i in timeline(client, b)["items"]} == {participant}
    apply(
        client, "entity.save", split, value={"kind": "person", "name": "Alex (home)", "aliases": []}
    )
    assert timeline(client, split)["entity"]["name"] == "Alex (home)"
    apply(client, "entity.delete", split)
    assert client.app.state.memory.store.get_audio(src["id"]) is not None
    assert len(timeline(client, b)["items"]) == 1


def test_invalid_meanings_rollback_split_and_audio_vs_transcript_retention(client):
    entity = apply(client, "entity.save", value={"kind": "project", "name": "Garden"})
    store = client.app.state.memory.store
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        wav.writeframes(b"\0\0" * 16000)
    key = str(uuid4())
    store.ingest(UUID(key), "omi", AT, output.getvalue(), 1)
    store.complete(key, "The garden needs an oak tree")
    src = client.get(f"/v1/labels/sources/{key}").json()["source"]
    body = command(client, "link.save", entity_id=entity, source=ref(src), relation="participant")
    assert client.post("/v1/labels/mutations", json=body).status_code == 422
    link(client, entity, src, "about_project")
    note(client, entity, [src])
    new = str(uuid4())
    body = command(
        client,
        "entity.split",
        entity,
        new_id=new,
        value={"kind": "project", "name": "New"},
        link_ids=[str(uuid4())],
    )
    assert client.post("/v1/labels/mutations", json=body).status_code == 409
    assert all(i["id"] != new for i in client.get("/v1/labels").json()["items"])
    result = store.apply_retention(1, 0, now=AT + timedelta(days=5))
    assert result["audio_files_removed"] == 1
    assert store.get_audio(key)["audio_available"] == 0
    assert not (store.audio_dir / f"{key}.wav").exists()
    assert len(timeline(client, entity)["items"]) == 2
    store.apply_retention(0, 1, now=AT + timedelta(days=5))
    assert not timeline(client, entity)["items"]


def test_backup_integrity_and_restore_labels(client, tmp_path):
    entity = person(client)
    src = source(client)
    link(client, entity, src)
    note(client, entity, [src])
    spec = importlib.util.spec_from_file_location("archive", Path("scripts/archive.py"))
    archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archive)
    backup = client.app.state.memory.store.export_archive()
    target = tmp_path / "restored"
    archive.restore(backup, target, 100_000_000)
    restored = Store(target)
    assert len(restored.label_timeline(entity)["items"]) == 2
    with restored.connect() as db:
        db.execute("UPDATE entities SET merged_into=id")
    with pytest.raises(ValueError, match="invalid label"):
        archive.validate_database(target)


def test_parallel_source_deletion_and_annotation_never_resurrect(client):
    entity, src = person(client), source(client)
    body = command(
        client,
        "note.save",
        kind="decision",
        title="Private",
        sources=[ref(src)],
        entity_ids=[entity],
    )
    model = TypeAdapter(LabelMutation).validate_python(body)
    store = client.app.state.memory.store

    def write():
        try:
            store.label_mutate(model)
        except LabelError:
            pass

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(write), pool.submit(store.delete_audio, src["id"])]
        for future in futures:
            future.result()
    assert not store.label_timeline(entity)["items"]


def test_removed_records_require_explicit_create_and_retry_cannot_restore_content(client):
    entity, src = person(client), source(client)
    body = command(
        client,
        "note.save",
        kind="decision",
        title="Private",
        sources=[ref(src)],
        entity_ids=[entity],
    )
    assert client.post("/v1/labels/mutations", json=body).status_code == 200
    apply(client, "note.delete", body["id"])
    # A lost success response can be retried after removal, without reapplying its payload.
    assert client.post("/v1/labels/mutations", json=body).status_code == 200
    assert not timeline(client, entity)["items"]
    edit = command(
        client,
        "note.save",
        body["id"],
        kind="decision",
        title="Old draft",
        sources=[ref(src)],
        entity_ids=[entity],
    )
    assert client.post("/v1/labels/mutations", json=edit).status_code == 409
    apply(client, "entity.delete", entity)
    edit = command(client, "entity.save", entity, value={"kind": "person", "name": "Old draft"})
    assert client.post("/v1/labels/mutations", json=edit).status_code == 409


def test_dst_repeated_hour_and_group_date_filter_uses_selected_evidence(client):
    entity = person(client)
    early = source(client, AT)
    later = source(client, AT + timedelta(hours=1))
    third_day = source(client, AT + timedelta(days=2))
    first_id = link(client, entity, early)
    later_id = link(client, entity, later)
    group = note(client, entity, [early, third_day], "conversation")
    day = timeline(client, entity, start_day="2026-10-25", end_day="2026-10-25")
    assert [i["id"] for i in day["items"] if i["entry_type"] == "source"] == [later_id, first_id]
    assert group not in [
        i["id"]
        for i in timeline(client, entity, start_day="2026-10-26", end_day="2026-10-26")["items"]
    ]


def test_merging_stale_duplicate_does_not_confirm_and_remains_restorable(client):
    a, b = person(client), person(client)
    src = source(client)
    link(client, a, src, "participant")
    current = client.patch(f"/v1/transcripts/{src['id']}", json={"text": "Correction"}).json()
    link(client, b, current, "participant")
    apply(client, "entity.merge", a, target_id=b)
    values = timeline(client, b)["items"]
    assert len(values) == 1 and values[0]["needs_review"]
    from omi_local.label_schema import validate_labels

    with client.app.state.memory.store.connect() as db:
        validate_labels(db)


def test_capture_does_not_invalidate_metadata_edits_or_offline_chat_cache(client):
    entity = person(client)
    pending = command(
        client, "entity.save", entity, value={"kind": "person", "name": "Alex revised"}
    )
    before = client.get("/v1/status").json()["archive_revision"]
    source(client)
    assert client.post("/v1/labels/mutations", json=pending).status_code == 200
    assert client.get("/v1/status").json()["archive_revision"] == before


def test_existing_archive_migrates_source_identity_without_fabricating_labels(tmp_path):
    import sqlite3

    from omi_local.store import SCHEMA

    root = tmp_path / "old-archive"
    root.mkdir()
    key = str(uuid4())
    with sqlite3.connect(root / "omi.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,text) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                key,
                "omi",
                AT.isoformat(),
                (AT + timedelta(seconds=30)).isoformat(),
                "digest",
                "old-path",
                "Alex mentioned a garden",
            ),
        )
    migrated = Store(root)
    row = migrated.get_audio(key)
    assert len(row["source_version"]) == 32 and row["text"] == "Alex mentioned a garden"
    assert migrated.label_directory()["items"] == []
    again = Store(root)
    assert again.get_audio(key)["source_version"] == row["source_version"]
