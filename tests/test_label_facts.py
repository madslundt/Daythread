"""Reviewed fact versions preserve evidence and never survive source deletion."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from test_labels import AT, apply, command, person, ref, source
from test_labels import client as client

from omi_local.label_models import LabelError, LabelMutation
from omi_local.label_schema import validate_labels
from omi_local.store import Store


def fact(client, entity, evidence, *, replaces=None, title="Alex prefers morning meetings"):
    return apply(client, "fact.save", create=True, kind="fact", title=title,
                 body="Reviewed interpretation", entity_ids=[entity],
                 sources=[ref(evidence)], supersedes=replaces)


def facts(client, entity, scope="current", **params):
    response = client.get(f"/v1/labels/{entity}/facts", params={"scope": scope, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_fact_replacement_is_atomic_immutable_and_reviewable(client):
    entity, evidence = person(client), source(client)
    first = fact(client, entity, evidence)
    second = fact(client, entity, evidence, replaces=first, title="Alex prefers afternoons")
    current, history = facts(client, entity), facts(client, entity, "history")
    assert [row["id"] for row in current["items"]] == [second]
    assert [row["id"] for row in history["items"]] == [first]
    assert history["items"][0]["title"] == "Alex prefers morning meetings"
    assert history["items"][0]["fact"]["superseded_by"] == second
    assert current["items"][0]["fact"]["status"] == "confirmed"
    assert not current["items"][0]["needs_review"]
    apply(client, "fact.reject", second)
    assert not facts(client, entity)["items"]
    assert len(facts(client, entity, "history")["items"]) == 2
    with client.app.state.memory.store.connect() as db:
        validate_labels(db)


def test_correction_requires_new_exact_source_review_and_preserves_old_version(client):
    entity, evidence = person(client), source(client)
    first = fact(client, entity, evidence)
    response = client.patch(f"/v1/transcripts/{evidence['id']}",
                            json={"text": "Corrected evidence"})
    assert response.status_code == 200
    assert facts(client, entity)["items"][0]["needs_review"]
    stale = command(client, "fact.save", create=True, title="Reviewed again", kind="fact",
                    sources=[ref(evidence)], entity_ids=[entity], supersedes=first)
    assert client.post("/v1/labels/mutations", json=stale).status_code == 409
    assert len(facts(client, entity)["items"]) == 1
    updated = client.get(f"/v1/labels/sources/{evidence['id']}").json()["source"]
    fact(client, entity, updated, replaces=first)
    assert not facts(client, entity)["items"][0]["needs_review"]
    assert facts(client, entity, "history")["items"][0]["needs_review"]


@pytest.mark.parametrize("which,expire", [(0, False), (1, False), (0, True), (1, True)])
def test_removing_either_version_evidence_purges_whole_fact_history(client, which, expire):
    entity = person(client)
    evidence = [source(client), source(client, AT + timedelta(days=1))]
    first = fact(client, entity, evidence[0])
    fact(client, entity, evidence[1], replaces=first, title="Private replacement content")
    if expire:
        with client.app.state.memory.store.connect() as db:
            db.execute("UPDATE audio SET text=NULL WHERE id=?", (evidence[which]["id"],))
    else:
        assert client.delete(f"/v1/audio/{evidence[which]['id']}").status_code == 200
    assert not facts(client, entity)["items"]
    assert not facts(client, entity, "history")["items"]
    with client.app.state.memory.store.connect() as db:
        for table in ("label_notes", "note_entities", "note_sources", "label_facts",
                      "label_fact_sources", "label_events"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        validate_labels(db)


def test_fact_cas_idempotency_and_parallel_replacement_have_one_winner(client):
    entity, evidence = person(client), source(client)
    first = fact(client, entity, evidence)
    body = command(client, "fact.save", create=True, title="New current fact", kind="fact",
                   sources=[ref(evidence)], entity_ids=[entity], supersedes=first)
    other = body | {"id": str(uuid4()), "mutation_id": str(uuid4())}
    store = client.app.state.memory.store

    def save(value):
        try:
            store.label_mutate(TypeAdapter(LabelMutation).validate_python(value))
            return True
        except LabelError:
            return False

    with ThreadPoolExecutor(2) as executor:
        outcomes = list(executor.map(save, [body, other]))
    assert sorted(outcomes) == [False, True]
    winner = body if outcomes[0] else other
    assert client.post("/v1/labels/mutations", json=winner).status_code == 200
    assert client.post("/v1/labels/mutations",
                       json=winner | {"title": "Changed retry"}).status_code == 409
    assert len(facts(client, entity)["items"]) == 1
    assert len(facts(client, entity, "history")["items"]) == 1


def test_merge_split_moves_entire_fact_family_and_forget_keeps_recordings(client):
    owner, target, evidence = person(client, "Alex"), person(client, "Sam"), source(client)
    first = fact(client, owner, evidence)
    second = fact(client, owner, evidence, replaces=first)
    apply(client, "entity.merge", owner, target_id=target)
    assert facts(client, owner)["entity"]["id"] == target
    split = str(uuid4())
    apply(client, "entity.split", target, new_id=split,
          value={"kind": "person", "name": "Separate Alex"}, note_ids=[second])
    assert not facts(client, target)["items"]
    assert facts(client, split)["items"][0]["id"] == second
    assert facts(client, split, "history")["items"][0]["id"] == first
    apply(client, "note.delete", second)
    assert not facts(client, split, "history")["items"]
    assert client.get(f"/v1/labels/sources/{evidence['id']}").status_code == 200


def test_fact_endpoints_are_profile_isolated_and_revision_paginated(client):
    entity, evidence = person(client), source(client)
    fact(client, entity, evidence)
    first_page = facts(client, entity, limit=1)
    fact(client, entity, evidence, title="Another independent fact")
    assert client.get(f"/v1/labels/{entity}/facts", params={
        "offset": 1, "revision": first_page["revision"],
    }).status_code == 409
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    response = client.get(f"/v1/labels/{entity}/facts", headers={"X-Omi-Profile": profile})
    assert response.status_code == 404


def test_fact_backup_restore_validates_lifecycle_and_provenance(client, tmp_path):
    entity, evidence = person(client), source(client)
    first = fact(client, entity, evidence)
    fact(client, entity, evidence, replaces=first)
    spec = importlib.util.spec_from_file_location("archive", Path("scripts/archive.py"))
    archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archive)
    target = tmp_path / "restored-facts"
    archive.restore(client.app.state.memory.store.export_archive(), target, 100_000_000)
    restored = Store(target)
    assert len(restored.label_timeline(entity)["items"]) == 2
    with restored.connect() as db:
        validate_labels(db)
        db.execute("DELETE FROM label_fact_sources")
    with pytest.raises(ValueError, match="invalid label"):
        archive.validate_database(target)


def test_fact_cannot_be_overwritten_as_an_unversioned_note(client):
    entity, evidence = person(client), source(client)
    current = fact(client, entity, evidence)
    overwrite = command(client, "note.save", current, create=False, title="Bypass",
                        kind="conversation", sources=[ref(evidence)], entity_ids=[entity])
    assert client.post("/v1/labels/mutations", json=overwrite).status_code == 409
    assert facts(client, entity)["items"][0]["kind"] == "fact"
