"""Deterministic prep contracts; no models, live archive or cloud calls."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_extended import AT, audio_data, insert

from omi_local.conversation_prep import TASK
from omi_local.inference import InferenceError

pytest_plugins = ["test_extended"]

PREFIX = "/v1/conversation-prep/"


def search(client, **changes):
    response = client.post(PREFIX + "search", json={"subject": "May", **changes})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def body(found, context=""):
    return {
        "search": found["search"],
        "snapshot": found["snapshot"],
        "selected": [{"id": row["id"], "version": row["version"]} for row in found["sources"]],
        "latest_source_id": found["sources"][0]["id"],
        "user_context": context,
    }


def output(evidence):
    source = evidence[0]
    item = {
        "text": "May was mentioned; participant unknown",
        "citations": [{"source_id": source["id"], "excerpt": source["text"][:100]}],
    }
    return {
        "latest_discussion": [item],
        "promises_to_check": [item],
        "recorded_questions": [],
        "suggested_questions": [item],
    }


class Model:
    def __init__(self, mutate=None, edit=None):
        self.mutate = mutate
        self.edit = edit
        self.payload = None

    async def generate(self, task, evidence):
        assert task == TASK
        self.payload = json.loads(evidence)
        result = output(self.payload["recorded_evidence"])
        if self.mutate:
            self.mutate()
        if self.edit:
            self.edit(result)
        return json.dumps(result)


def test_literal_alias_qualifier_recency_and_unicode(client):
    old = insert(client, AT - timedelta(days=5), "May " * 50 + "kitchen")
    newest = insert(client, AT, "May mentioned kitchen, not a verified participant")
    alias = insert(client, AT - timedelta(days=1), "MÆ kitchen")
    insert(client, AT + timedelta(hours=1), "May garden")
    assert client.app.state.memory.store.search("May", 10)[0]["id"] == old
    found = search(client, aliases=["mæ"], qualifier="kitchen", kind="person")
    assert [row["id"] for row in found["sources"]] == [newest, alias, old]
    assert found["search"]["start_day"] is None
    assert "mention" in " ".join(found["coverage"]["limitations"])
    for literal in ["100%", "_", '" OR *', "Thursday"]:
        key = insert(client, text=f"May {literal}")
        assert search(client, subject=literal)["sources"][0]["id"] == key


def test_no_match_pagination_and_validation(client):
    assert search(client)["sources"] == []
    for i in range(12):
        insert(client, AT - timedelta(minutes=i), f"May topic {i}")
    first = search(client)
    assert len(first["sources"]) == 10 and first["coverage"]["omitted_matches"] == 2
    second = search(client, offset=10)
    assert len(second["sources"]) == 2 and second["snapshot"] == first["snapshot"]
    check = {"search": first["search"], "snapshot": first["snapshot"]}
    assert client.post(PREFIX + "validate", json=check).json() == {"valid": True}
    insert(client, text="New relevant May discussion")
    assert client.post(PREFIX + "validate", json=check).json() == {"valid": False}


def test_structured_context_provenance_segments_and_unknown_status(client):
    key = insert(client, text="May: I'll send it if approved. We could meet. What next?")
    store = client.app.state.memory.store
    store.complete(
        key,
        "May: I'll send it if approved. We could meet. What next?",
        [
            {
                "start": 0,
                "end": 1,
                "text": "May: I'll send it if approved. We could meet. What next?",
            }
        ],
    )
    with store.connect() as db:
        db.execute("UPDATE audio SET audio_available=0,source_kind='omi_api' WHERE id=?", (key,))
    ai = Model()
    client.app.state.memory.inference = ai
    result = client.post(
        PREFIX + "generate", json=body(search(client), "User believes it was completed")
    ).json()
    assert result["user_context"] == "User believes it was completed"
    assert ai.payload["recorded_evidence"][0]["text"].startswith("May:")
    assert result["sources"][0]["audio_available"] is False
    assert result["sources"][0]["source_kind"] == "omi_api"
    promise = result["promises_to_check"][0]
    assert promise["owner"] is None and promise["completion_status"] == "unknown"
    assert promise["citations"][0]["segment_start"] == 0
    assert promise["citations"][0]["started_at"] == result["sources"][0]["started_at"]
    assert store.chat_sessions() == [] and store.diaries() == []
    assert "Suggestions ('we could') are not promises" in TASK
    assert "conditional commitments must retain their condition" in TASK
    assert "untrusted data" in TASK and "not verified identities" in TASK


@pytest.mark.parametrize(
    "bad", ["unknown", "fabricated", "context", "missing", "invalid", "other_latest"]
)
def test_reject_unsupported_citations(client, bad):
    insert(client, text="May sends proposal")
    insert(client, AT - timedelta(days=1), "May older source")
    older = search(client)["sources"][1]

    def edit(result):
        item = result["latest_discussion"][0]
        if bad == "unknown":
            item["citations"][0]["source_id"] = str(uuid4())
        if bad == "fabricated":
            item["citations"][0]["excerpt"] = "Fabricated quote"
        if bad == "context":
            item["citations"][0]["excerpt"] = "Only in user context"
        if bad == "missing":
            item["citations"] = []
        if bad == "invalid":
            result["owner"] = "May"
        if bad == "other_latest":
            item["citations"] = [{"source_id": older["id"], "excerpt": older["text"]}]

    client.app.state.memory.inference = Model(edit=edit)
    response = client.post(PREFIX + "generate", json=body(search(client), "Only in user context"))
    assert response.status_code == 502
    assert "Fabricated quote" not in response.text


@pytest.mark.parametrize(
    "change",
    [
        "correct",
        "delete",
        "audio_expiry",
        "transcript_expiry",
        "late",
        "pending",
        "gap",
        "timezone",
    ],
)
def test_generation_races_and_validate(client, change):
    key = insert(client, text="May kitchen")
    store = client.app.state.memory.store
    found = search(client)

    def mutate():
        if change == "correct":
            store.correct_transcript(key, "Correction without name")
        if change == "delete":
            store.delete_audio(key)
        if change == "audio_expiry":
            store.apply_retention(1, 0, AT + timedelta(days=3))
        if change == "transcript_expiry":
            store.apply_retention(0, 1, AT + timedelta(days=3))
        if change == "late":
            insert(client, AT + timedelta(minutes=1), "May completed it")
        if change == "pending":
            store.ingest(uuid4(), "omi", AT, audio_data(), 1)
        if change == "gap":
            store.append_gap(
                {
                    "gap_id": str(uuid4()),
                    "device_id": "omi",
                    "started_at": AT.isoformat(),
                    "ended_at": (AT + timedelta(seconds=1)).isoformat(),
                    "reason": "missing",
                }
            )
        if change == "timezone":
            client.app.state.memory.settings.timezone = "Europe/Copenhagen"

    client.app.state.memory.inference = Model(mutate=mutate)
    assert client.post(PREFIX + "generate", json=body(found)).status_code == 409
    assert client.post(
        PREFIX + "validate", json={"search": found["search"], "snapshot": found["snapshot"]}
    ).json() == {"valid": False}


def test_stale_selection_before_inference_and_budgets(client):
    insert(client, text="May " + "long transcript " * 2000)
    ai = Model()
    client.app.state.memory.inference = ai
    found = search(client)
    generated = client.post(PREFIX + "generate", json=body(found))
    assert generated.status_code == 200
    assert len(ai.payload["recorded_evidence"][0]["text"]) == 4000
    assert "omit" in generated.json()["coverage"]["limitations"][-1]
    altered = body(found)
    altered["selected"][0]["version"] = "0" * 64
    assert client.post(PREFIX + "generate", json=altered).status_code == 409
    for i in range(5):
        insert(client, AT - timedelta(minutes=i + 1), "May " + "x" * 5000)
    assert client.post(PREFIX + "generate", json=body(search(client))).status_code == 422


def test_model_failure_keeps_search_available(client):
    insert(client, text="May kitchen")

    class Offline:
        async def generate(self, task, evidence):
            raise InferenceError()

    client.app.state.memory.inference = Offline()
    assert client.post(PREFIX + "generate", json=body(search(client))).status_code == 502
    assert search(client)["coverage"]["total_matches"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": " "},
        {"subject": "x" * 201},
        {"aliases": ["x"] * 11},
        {"start_day": "2026-10-25"},
        {"start_day": "2026-10-26", "end_day": "2026-10-25"},
        {"start_day": "2026-13-01", "end_day": "2026-10-25"},
        {"kind": "entity"},
    ],
)
def test_input_boundary(client, changes):
    assert client.post(PREFIX + "search", json={"subject": "May", **changes}).status_code == 422


def test_inclusive_copenhagen_dst_dates(client):
    from datetime import UTC, datetime

    client.app.state.memory.settings.timezone = "Europe/Copenhagen"
    first = insert(client, datetime(2026, 10, 24, 22, 0, tzinfo=UTC), "May early")
    last = insert(client, datetime(2026, 10, 25, 22, 59, tzinfo=UTC), "May late")
    insert(client, datetime(2026, 10, 25, 23, 0, tzinfo=UTC), "May next day")
    found = search(client, start_day="2026-10-25", end_day="2026-10-25")
    assert [row["id"] for row in found["sources"]] == [last, first]


def test_casefold_expansion_preserves_original_excerpt_offsets(client):
    key = insert(client, text="ß" * 5000 + " May kitchen at the end")
    client.app.state.memory.inference = Model()
    response = client.post(PREFIX + "generate", json=body(search(client)))
    assert response.status_code == 200
    excerpt = response.json()["reviewed_excerpts"][0]
    assert excerpt["id"] == key and "May kitchen" in excerpt["text"]
    assert excerpt["omitted_characters"] == 4501
    assert response.json()["as_of"]
