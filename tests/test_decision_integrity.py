"""Decision evidence integrity across extraction scopes, opposing claims and archive days."""

import json
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_decisions import AT, content, create, event, selected_decision, source

from omi_local.api import create_app
from omi_local.settings import Settings


@pytest.fixture
def client(tmp_path):
    config = Settings(
        data_dir=tmp_path, worker_enabled=False, timezone="Europe/Copenhagen", _env_file=None
    )
    with TestClient(create_app(config)) as session:
        yield session


class Proposal:
    def __init__(self, s, wording="Choose A", opposing=None):
        self.source = s
        self.wording = wording
        self.opposing = opposing
        self.calls = 0

    async def generate(self, task, evidence):
        self.calls += 1
        value = content(self.source, self.wording)
        if self.opposing:
            value["contradictions"] = [dict(content(self.opposing)["choice"], field="choice")]
        return json.dumps({"candidates": [selected_decision(value)]})


def extract(client, **scope):
    response = client.post("/v1/decision-extractions", json={"request_id": str(uuid4()), **scope})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("review", ["confirm", "dismiss"])
def test_choice_anchors_suppress_model_paraphrases_across_scopes(client, review):
    s = source(client)
    ai = Proposal(s)
    client.app.state.memory.inference = ai
    first = extract(client, day="2026-10-15")["items"][0]
    reviewed = event(client, first, review).json()
    ai.wording = "Adopt option A instead of the other option"
    assert extract(client, source_ids=[s["id"]])["items"] == []
    record = client.get(f"/v1/decisions/{first['id']}").json()
    assert record["version"] == reviewed["version"]
    assert record["review_status"] == reviewed["review_status"]


def test_manual_edits_preserve_anchor_suppression_without_model_wording(client):
    s = source(client)
    d = create(client, s, "Owner's interpretation")
    d = event(client, d, "confirm").json()
    edited = content(s, "Corrected owner wording")
    edited["owner_notes"] = "Owner-only explanation"
    d = event(client, d, "edit", content=edited).json()
    ai = Proposal(s, "A model paraphrase neither owner version used")
    client.app.state.memory.inference = ai
    assert extract(client, source_ids=[s["id"]])["items"] == []
    assert extract(client, day="2026-10-15")["items"] == []
    record = client.get(f"/v1/decisions/{d['id']}").json()
    assert record["version"] == d["version"]
    assert record["content"]["owner_notes"] == "Owner-only explanation"


def test_contradictions_block_confirmation_and_survive_explicit_resolution(client):
    s = source(client)
    opposing = source(client, "A does not work; choose B instead.")
    c = content(s)
    c["contradictions"] = [dict(content(opposing)["choice"], field="rationale")]
    response = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c})
    assert response.status_code == 200, response.text
    d = response.json()
    assert d["evidence_condition"] == "conflicted"
    assert event(client, d, "confirm").status_code == 409
    resolved = event(client, d, "resolve").json()
    assert resolved["content"]["contradictions"] == c["contradictions"]
    confirmed = event(client, resolved, "confirm").json()
    assert confirmed["review_status"] == "confirmed"
    markdown = client.get("/v1/diary/2026-10-15.md").text
    assert "Contradictory evidence for rationale" in markdown
    assert opposing["text"] in markdown
    detail = client.get(f"/v1/decisions/{d['id']}").json()
    assert detail["history"][0]["content"]["contradictions"] == c["contradictions"]
    edited = event(client, confirmed, "edit", content=c).json()
    assert edited["evidence_condition"] == "conflicted"


@pytest.mark.parametrize("mutation", ["correct", "delete"])
def test_opposing_evidence_correction_and_deletion_propagate(client, mutation):
    s = source(client)
    opposing = source(client, "A is unsuitable.")
    c = content(s)
    c["contradictions"] = [dict(content(opposing)["choice"], field="choice")]
    d = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c}).json()
    d = event(client, d, "resolve").json()
    if mutation == "correct":
        client.patch(f"/v1/transcripts/{opposing['id']}", json={"text": "A is suitable."})
    else:
        client.delete(f"/v1/audio/{opposing['id']}")
    detail = client.get(f"/v1/decisions/{d['id']}").json()
    assert detail["evidence_condition"] == ("stale" if mutation == "correct" else "removed")
    if mutation == "delete":
        assert detail["content"] is None
        assert all(v["content"] is None for v in detail["history"])


def test_extraction_retains_separate_contradictions(client):
    s = source(client)
    opposing = source(client, "I reject A because it fails the requirement.")
    client.app.state.memory.inference = Proposal(s, opposing=opposing)
    d = extract(client, day="2026-10-15")["items"][0]
    assert d["evidence_condition"] == "conflicted"
    assert d["content"]["contradictions"][0]["field"] == "choice"
    assert d["content"]["contradictions"][0]["evidence"][0]["source_id"] == opposing["id"]


@pytest.mark.parametrize("had_decision", [True, False])
def test_deleted_source_uuid_cannot_automatically_recreate_after_reinsert(client, had_decision):
    s = source(client)
    store = client.app.state.memory.store
    row = store.get_audio(s["id"])
    data = Path(row["path"]).read_bytes()
    client.app.state.memory.inference = Proposal(s)
    if had_decision:
        assert len(extract(client, day="2026-10-15")["items"]) == 1
    client.delete(f"/v1/audio/{s['id']}")
    store.ingest(UUID(s["id"]), "test", AT, data, 1)
    store.complete(s["id"], s["text"])
    restored = client.get(f"/v1/transcripts/{s['id']}").json()
    assert restored["source_version"] != s["source_version"]
    ai = Proposal(restored)
    client.app.state.memory.inference = ai
    result = extract(client, source_ids=[s["id"]])
    assert result["items"] == []
    assert result["suppressed_sources"] == 1
    assert ai.calls == 0
    assert create(client, restored)["review_status"] == "proposed"


@pytest.mark.parametrize(
    "start,end,days",
    [
        ("2026-10-15T21:59:00+00:00", "2026-10-15T22:01:00+00:00", ["2026-10-15", "2026-10-16"]),
        ("2026-10-15T21:59:00+00:00", "2026-10-15T22:00:00+00:00", ["2026-10-15"]),
        (
            "2026-10-24T21:59:00+00:00",
            "2026-10-25T23:01:00+00:00",
            ["2026-10-24", "2026-10-25", "2026-10-26"],
        ),
    ],
)
def test_backup_exports_every_overlapping_local_day_without_recaps(client, start, end, days):
    first = source(client, "We choose A.", datetime.fromisoformat(start))
    last = source(client, "We still choose A.", datetime.fromisoformat(end) - timedelta(seconds=1))
    store = client.app.state.memory.store
    c = content(first)
    c["choice"]["evidence"].append(content(last)["choice"]["evidence"][0])
    response = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c})
    assert response.status_code == 200, response.text
    d = response.json()
    backup = store.export_archive()
    try:
        with zipfile.ZipFile(backup) as archive:
            assert sorted(name for name in archive.namelist() if name.startswith("diary/")) == [
                f"diary/{day}.md" for day in days
            ]
            for day in days:
                assert d["id"] in archive.read(f"diary/{day}.md").decode()
    finally:
        shutil.rmtree(backup.parent)


def test_existing_wording_keys_are_backfilled_from_manual_history(client):
    s = source(client)
    d = create(client, s, "An older manual record")
    event(client, d, "dismiss")
    # Emulate the previous release's wording-dependent index, retaining authoritative history.
    with client.app.state.memory.store.connect() as db:
        db.execute("DELETE FROM decision_dedup_keys")
        db.execute("DELETE FROM decision_metadata WHERE key='anchor_dedup_v2'")
        db.execute("INSERT INTO decision_dedup_keys VALUES(?,?)", ("old-wording-key", d["id"]))
    client.app.state.memory.inference = Proposal(s, "A new wording of the same choice")
    assert extract(client, day="2026-10-15")["items"] == []
    assert client.get(f"/v1/decisions/{d['id']}").json()["review_status"] == "dismissed"


def test_exact_anchor_set_normalizes_order_and_duplicates_without_semantic_matching(client):
    s = source(client, "We choose A. We choose B.")
    c = content(s)
    first = dict(c["choice"]["evidence"][0], quote="We choose A.", start=0, end=12)
    second = dict(c["choice"]["evidence"][0], quote="We choose B.", start=13, end=25)
    c["choice"]["evidence"] = [first, second, first]
    created = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c})
    assert created.status_code == 200

    class DifferentPassage:
        async def generate(self, task, evidence):
            claims = [
                dict(c, choice={"text": "Paraphrased A", "evidence": [second, first]}),
                dict(c, choice={"text": "A different choice", "evidence": [second]}),
            ]
            return json.dumps(
                {
                    "candidates": [
                        selected_decision(value) for value in claims
                    ]
                }
            )

    client.app.state.memory.inference = DifferentPassage()
    result = extract(client, source_ids=[s["id"]])
    assert len(result["items"]) == 1
    assert result["items"][0]["content"]["choice"]["text"] == "A different choice"


def test_contradiction_default_and_restore_validate_opposing_anchors(client):
    from omi_local.decision_restore import validate_decisions

    s = source(client)
    old = create(client, s)
    assert old["content"]["contradictions"] == []
    opposing = source(client, "A was never agreed.")
    c = content(s)
    c["contradictions"] = [content(opposing)["choice"]]  # Default field is choice.
    response = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c})
    assert response.status_code == 200
    assert response.json()["content"]["contradictions"][0]["field"] == "choice"
    store = client.app.state.memory.store
    with store.connect() as db:
        validate_decisions(db)
        malformed = response.json()["content"]
        malformed["contradictions"][0]["evidence"][0]["quote"] = "fabricated"
        db.execute(
            "UPDATE decisions SET content=? WHERE id=?",
            (json.dumps(malformed), response.json()["id"]),
        )
        with pytest.raises(ValueError, match="quotation"):
            validate_decisions(db)


@pytest.mark.parametrize("operation", ["correct", "delete"])
def test_opposing_source_change_during_extraction_cannot_persist(client, operation):
    s = source(client)
    opposing = source(client, "This contradicts A.")

    class RacingProposal(Proposal):
        async def generate(self, task, evidence):
            payload = await super().generate(task, evidence)
            store = client.app.state.memory.store
            if operation == "correct":
                store.correct_transcript(opposing["id"], "Corrected disagreement")
            else:
                store.delete_audio(opposing["id"])
            return payload

    client.app.state.memory.inference = RacingProposal(s, opposing=opposing)
    response = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    )
    assert response.status_code == 409
    assert client.get("/v1/decisions").json()["total"] == 0


def test_absent_contradictions_remains_idempotent_for_preupgrade_commands(client):
    from omi_local.decision_store import fingerprint

    s = source(client)
    payload = {"command_id": str(uuid4()), "state": "tentative", "content": content(s)}
    d = client.post("/v1/decisions", json=payload).json()
    with client.app.state.memory.store.connect() as db:
        db.execute(
            "UPDATE decision_commands SET fingerprint=? WHERE id=?",
            (fingerprint(payload), payload["command_id"]),
        )
    replay = client.post("/v1/decisions", json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == d["id"]
    assert len(replay.json()["history"]) == 1
