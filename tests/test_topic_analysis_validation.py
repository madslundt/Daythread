"""Invalid model responses get bounded, evidence-checked repair within the failed part."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from omi_local.recording_topics import analyze

TEXT = "Planning the launch. " + "Review the dates and responsibilities. " * 12


class RepairModel:
    def __init__(self, invalid):
        self.invalid = invalid
        self.calls = []

    async def generate_json(self, task, evidence):
        self.calls.append((task, evidence))
        if len(self.calls) == 1:
            return self.invalid
        return json.dumps({"title": "Launch planning", "sections": [
            {"title": "Launch planning", "start_quote": evidence[:80]},
        ]})


@pytest.mark.parametrize("invalid", [
    json.dumps({"title": "Launch planning", "sections": [
        {"title": "Launch planning", "start_quote": TEXT},
    ]}),
    json.dumps({"title": "Launch planning", "sections": [
        {"title": "Launch planning", "start_quote": "A quote absent from the transcript"},
    ]}),
    '{"title": "Launch planning", "sections": ',
])
def test_invalid_topic_response_is_repaired_for_the_same_excerpt(invalid):
    model = RepairModel(invalid)
    memory = SimpleNamespace(inference=model, settings=SimpleNamespace(summary_language="auto"))

    result = asyncio.run(analyze(memory, {"text": TEXT}))

    assert len(model.calls) == 2
    assert [evidence for _, evidence in model.calls] == [TEXT, TEXT]
    assert "repair" in model.calls[1][0].lower()
    assert result["title"] == "Launch planning"
    assert result["sections"] == [{
        "title": "Launch planning", "start_offset": 0, "end_offset": len(TEXT),
    }]


def test_repeated_opening_quote_still_identifies_the_known_start():
    text = "A recurring opening phrase. Another subject. A recurring opening phrase."
    model = RepairModel(json.dumps({"title": "Planning", "sections": [
        {"title": "Planning", "start_quote": "A recurring opening phrase."},
    ]}))
    memory = SimpleNamespace(inference=model, settings=SimpleNamespace(summary_language="auto"))
    result = asyncio.run(analyze(memory, {"text": text}))
    assert len(model.calls) == 1
    assert result["sections"][0]["start_offset"] == 0


def test_first_topic_extends_to_the_opening_without_repeating_model_work():
    text = "An introductory remark. Planning the launch. Review the budget."
    model = RepairModel(json.dumps({"title": "Planning", "sections": [
        {"title": "Launch", "start_quote": "Planning the launch."},
        {"title": "Budget", "start_quote": "Review the budget."},
    ]}))
    memory = SimpleNamespace(inference=model, settings=SimpleNamespace(summary_language="auto"))

    result = asyncio.run(analyze(memory, {"text": text}))

    assert len(model.calls) == 1
    assert result["sections"] == [
        {"title": "Launch", "start_offset": 0, "end_offset": text.index("Review")},
        {"title": "Budget", "start_offset": text.index("Review"), "end_offset": len(text)},
    ]
    assert "".join(text[s["start_offset"]:s["end_offset"]]
                   for s in result["sections"]) == text


def test_extending_first_topic_does_not_accept_reversed_quotes():
    from omi_local.topic_analysis import Analysis, topic_offsets

    text = "Opening. Earlier subject. Later subject."
    result = Analysis(title="Subjects", sections=[
        {"title": "Later", "start_quote": "Later subject."},
        {"title": "Earlier", "start_quote": "Earlier subject."},
    ])
    with pytest.raises(ValueError, match="ordered"):
        topic_offsets(result, text)


def test_schema_diagnostics_do_not_disclose_rejected_field_names_or_values(caplog):
    private = "private-untrusted-value"
    model = RepairModel(json.dumps({private: private}))
    memory = SimpleNamespace(inference=model, settings=SimpleNamespace(summary_language="auto"))
    asyncio.run(analyze(memory, {"text": TEXT}))
    assert private not in caplog.text
    assert private not in model.calls[1][0]
