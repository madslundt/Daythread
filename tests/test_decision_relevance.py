"""One-pass relevance-gate persistence tests; these do not evaluate model judgment quality."""

import copy
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from test_decisions import AT, content, create, event, selected_decision, source
from test_decisions import client as client


class SelectionOutput:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0

    async def generate(self, task, evidence):
        self.calls += 1
        return json.dumps({"candidates": self.candidates})


def extract(client, source_ids):
    return client.post(
        "/v1/decision-extractions",
        json={"request_id": str(uuid4()), "source_ids": source_ids},
    )


def test_low_utility_choices_are_not_saved_but_meaningful_tentative_choices_survive(client):
    routine = [
        "I'll have a cappuccino today.",
        "Let's eat today's lunch in the kitchen.",
        "My uncle chose a red boat many years ago.",
        "We could try a different app someday.",
        "I prefer blue labels.",
        "Which city is the capital of France? Paris. Right.",
    ]
    important = [
        "We selected CoffeeLab as our supplier for a year; the contract commits our budget.",
        "If security approves, we tentatively chose the new database for next quarter's migration.",
    ]
    sources = [
        source(client, text, AT + timedelta(minutes=index))
        for index, text in enumerate(routine + important)
    ]
    candidates = []
    for index, s in enumerate(sources):
        candidate = selected_decision(content(s, s["text"]), state="final")
        if index < len(routine):
            candidate["selection"].update(
                criterion="none", reason="This speech establishes no lasting relevant effect."
            )
        elif index == len(routine):
            candidate["selection"]["reason"] = "The supplier agreement commits the ongoing budget."
        else:
            candidate["state"] = "tentative"
            candidate["selection"]["reason"] = "The conditional choice directs next quarter's work."
        candidates.append(candidate)
    model = SelectionOutput(candidates)
    client.app.state.memory.inference = model
    response = extract(client, [s["id"] for s in sources])
    assert response.status_code == 200, response.text
    records = client.get("/v1/decisions").json()["items"]
    assert len(records) == 2 and model.calls == 1
    by_choice = {record["content"]["choice"]["text"]: record for record in records}
    assert set(by_choice) == set(important)
    assert by_choice[important[0]]["state"] == "final"
    assert by_choice[important[1]]["state"] == "tentative"
    for record in records:
        assert record["review_status"] == "proposed"
        assert record["content"]["rationale"] is None
        assert record["content"]["owner_notes"] == ""
        assert "selection" not in json.dumps(record)


@pytest.mark.parametrize(
    "gate,expected", [("missing", 502), ("none", 200), ("blank_reason", 502),
                      ("different_utility", 502), ("malformed_evidence", 502)]
)
def test_missing_or_inapplicable_selection_never_defaults_to_keeping_a_decision(
    client, gate, expected
):
    s = source(client, "We selected a supplier for next year's contract.")
    candidate = selected_decision(content(s))
    if gate == "missing":
        candidate.pop("selection")
    elif gate == "none":
        candidate["selection"]["criterion"] = "none"
    elif gate == "blank_reason":
        candidate["selection"]["reason"] = "  \n "
    elif gate == "different_utility":
        candidate["selection"]["criterion"] = "durable_reference"
    else:
        candidate["selection"]["evidence"] = []
    client.app.state.memory.inference = SelectionOutput([candidate])
    response = extract(client, [s["id"]])
    assert response.status_code == expected, response.text
    assert client.get("/v1/decisions").json()["total"] == 0
    if expected == 502:
        with client.app.state.memory.store.connect() as db:
            assert db.execute("SELECT cursor FROM decision_extractions").fetchone()[0] == 0


@pytest.mark.parametrize("bad,expected", [("quote", 502), ("source", 502), ("version", 409)])
def test_selection_basis_requires_exact_current_supplied_original_evidence(client, bad, expected):
    s = source(client, "We selected the new database because the old contract expires next month.")
    valid = selected_decision(content(s))
    invalid = copy.deepcopy(valid)
    anchor = invalid["selection"]["evidence"][0]
    if bad == "quote":
        anchor["quote"] = "An invented consequence"
    elif bad == "source":
        other = source(
            client, "A real quotation outside the selected input.", AT + timedelta(hours=1)
        )
        invalid["selection"]["evidence"] += content(other)["choice"]["evidence"]
    else:
        stale = dict(anchor, source_version="an-old-transcript-version")
        invalid["selection"]["evidence"].append(stale)
    # Even an earlier valid candidate must not be saved from a rejected output batch.
    client.app.state.memory.inference = SelectionOutput([valid, invalid])
    response = extract(client, [s["id"]])
    assert response.status_code == expected, response.text
    assert client.get("/v1/decisions").json()["total"] == 0


def test_selection_cannot_cite_an_omitted_fragment_even_if_its_quote_exists_in_the_source(client):
    choice = "We tentatively selected the new database. "
    consequence = "The old contract expires before next quarter's migration."
    s = source(client, choice + "Background context. " * 1500 + consequence)
    value = content(s, choice.strip())
    value["choice"]["evidence"][0].update(quote=choice, start=0, end=len(choice))
    candidate = selected_decision(value)
    offset = s["text"].index(consequence)
    candidate["selection"]["evidence"] = [
        copy.deepcopy(value["choice"]["evidence"][0]),
        dict(value["choice"]["evidence"][0], quote=consequence, start=offset,
             end=offset + len(consequence))
    ]
    client.app.state.memory.inference = SelectionOutput([candidate])
    response = extract(client, [s["id"]])
    assert response.status_code == 502, response.text
    assert client.get("/v1/decisions").json()["total"] == 0
    with client.app.state.memory.store.connect() as db:
        assert db.execute("SELECT cursor FROM decision_extractions").fetchone()[0] == 0


def test_relevance_reassessment_does_not_modify_an_existing_reviewed_choice(client):
    s = source(client, "We chose coffee today.")
    record = create(client, s, "Coffee today")
    confirmed = event(client, record, "confirm").json()
    before = client.get(f"/v1/decisions/{confirmed['id']}").json()
    candidate = selected_decision(content(s, "Coffee today"))
    candidate["selection"].update(criterion="none", reason="This was a fleeting everyday choice.")
    client.app.state.memory.inference = SelectionOutput([candidate])
    assert extract(client, [s["id"]]).status_code == 200
    assert client.get(f"/v1/decisions/{confirmed['id']}").json() == before


def test_a_trivial_choice_cannot_borrow_an_unrelated_consequential_quote(client):
    trivial = "I'll have coffee today. "
    consequential = "We signed the supplier contract committing next year's budget."
    s = source(client, trivial + consequential)
    value = content(s, "Coffee today")
    value["choice"]["evidence"][0].update(quote=trivial, start=0, end=len(trivial))
    candidate = selected_decision(value)
    candidate["selection"]["evidence"] = [
        dict(value["choice"]["evidence"][0], quote=consequential,
             start=len(trivial), end=len(s["text"]))
    ]
    client.app.state.memory.inference = SelectionOutput([candidate])
    response = extract(client, [s["id"]])
    assert response.status_code == 502, response.text
    assert client.get("/v1/decisions").json()["total"] == 0


def test_identical_choice_wording_with_distinct_owners_and_effects_is_preserved(client):
    sources = [
        source(client, "Alice chose to postpone launch until her security review is complete."),
        source(client, "Bob chose to postpone launch until his supplier contract is signed.",
               AT + timedelta(minutes=1)),
    ]
    client.app.state.memory.inference = SelectionOutput([
        selected_decision(content(s, "Postpone launch")) for s in sources
    ])
    response = extract(client, [s["id"] for s in sources])
    assert response.status_code == 200, response.text
    records = client.get("/v1/decisions").json()["items"]
    assert len(records) == 2
    assert {r["content"]["choice"]["text"] for r in records} == {"Postpone launch"}
    assert {r["content"]["choice"]["evidence"][0]["source_id"] for r in records} == {
        s["id"] for s in sources
    }
