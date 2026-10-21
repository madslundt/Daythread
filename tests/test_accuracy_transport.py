"""Source aliases change JSON identity fields without changing evidentiary words."""

import json

import pytest

from omi_local.accuracy_transport import AccuracySources
from omi_local.summary_accuracy import AccuracyReplyError, corrections, reconciliation


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_requests_hide_other_batch_provenance_and_preserve_verbatim_content():
    sources = [{"id": "original-b", "text": "Literal src_1 and original-a are spoken."},
               {"id": "original-a", "text": "We plan deployment tomorrow."}]
    transport = AccuracySources(sources)
    draft = "Deployment completed. [original-a]"
    candidate = {"claim": "Deployment completed", "replacement": "Deployment is planned",
                 "source_ids": ["original-a"], "reviewed_source_ids": ["original-a"]}
    supplied = transport.request(draft, [sources[0]], [candidate])
    assert supplied == {"draft": draft,
                        "originals": [{**sources[0], "id": "src_2"}],
                        "allowed_source_ids": ["src_2"],
                        "corrections": [{"index": 0, "claim": candidate["claim"],
                                         "replacement": candidate["replacement"]}]}
    assert transport.request(draft, [sources[1]])["originals"][0]["id"] == "src_1"
    assert sources[0]["id"] == "original-b" and "source_ids" in candidate


def test_response_expands_only_identity_fields_before_strict_quote_validation():
    sources = [{"id": "original", "text": "We plan src_1 deployment tomorrow."}]
    transport = AccuracySources(sources)
    row = {"claim": "src_1 deployment completed", "replacement": "src_1 deployment is planned",
           "evidence": [{"source_id": "src_1", "quote": sources[0]["text"]}]}
    expanded = transport.expand(json.dumps({"corrections": [row]}))
    decoded = json.loads(expanded)["corrections"][0]
    assert decoded == {**row, "evidence": [{"source_id": "original",
                                          "quote": sources[0]["text"]}]}
    assert corrections(expanded, row["claim"], sources)[0]["source_ids"] == ["original"]
    row["evidence"][0]["quote"] = "We completed src_1 deployment tomorrow."
    with pytest.raises(AccuracyReplyError, match="source-grounded"):
        corrections(transport.expand(json.dumps({"corrections": [row]})), row["claim"], sources)


@pytest.mark.parametrize("reference", ["src_1", "src_3", "SRC_2", "invented-original"])
def test_unknown_or_other_batch_alias_still_fails_closed(reference):
    sources = [{"id": "original-a", "text": "The deployment completed yesterday."},
               {"id": "original-b", "text": "The deployment completed yesterday."}]
    answer = json.dumps({"vetoes": [{"index": 0, "source_id": reference,
                                     "quote": sources[1]["text"]}]})
    with pytest.raises(AccuracyReplyError, match="cross-recording"):
        reconciliation(AccuracySources(sources).expand(answer), [{}], [sources[1]], [[]])


def test_reconciliation_expands_alias_but_preserves_previously_reviewed_context_attestation():
    source = {"id": "original", "text": "We propose deployment tomorrow."}
    answer = json.dumps({"vetoes": [{"index": 0, "source_id": "src_1",
                                     "quote": source["text"]}]})
    expanded = AccuracySources([source]).expand(answer)
    assert reconciliation(expanded, [{}], [source], [[source]]) == set()
    assert reconciliation(expanded, [{}], [source], [[]]) == {0}


@pytest.mark.parametrize("answer", ["not JSON", "null", "[]", '{"vetoes":[null]}'])
def test_alias_transport_preserves_malformed_responses_for_strict_validator(answer):
    expanded = AccuracySources([]).expand(answer)
    with pytest.raises(AccuracyReplyError):
        reconciliation(expanded, [{}], [], [[]])


@pytest.mark.anyio
async def test_cross_batch_alias_and_quote_are_rejected_on_both_attempts_with_safe_logging(
    monkeypatch, caplog,
):
    from omi_local import summary_accuracy

    monkeypatch.setattr(summary_accuracy, "EVIDENCE_BUDGET", 100)
    sources = [{"id": "private-original-a", "text": "We propose deployment tomorrow."},
               {"id": "private-original-b", "text": "A separate review completed yesterday."}]
    attempts = []

    class Reviewer:
        def for_task(self, _):
            return self

        async def generate_json(self, task, evidence):
            supplied = json.loads(evidence)
            original = supplied["originals"][0]
            if task.startswith("Check proposed draft corrections"):
                assert "private-original" not in evidence
                assert set(supplied["corrections"][0]) == {"index", "claim", "replacement"}
                if original["id"] == "src_1":
                    return '{"vetoes":[]}'
                attempts.append(task)
                assert supplied["allowed_source_ids"] == ["src_2"]
                return json.dumps({"vetoes": [{"index": 0, "source_id": "src_1",
                                                "quote": sources[0]["text"]}]})
            return json.dumps({"corrections": [{
                "claim": "Deployment is underway", "replacement": "Deployment is proposed",
                "evidence": [{"source_id": "src_1", "quote": sources[0]["text"]}],
            }] if original["id"] == "src_1" else []})

    with pytest.raises(AccuracyReplyError, match="cross-recording"):
        await summary_accuracy.review_summary(Reviewer(), sources, "Deployment is underway.")
    assert len(attempts) == 2 and '"received": "src_1"' in attempts[1]
    assert "THIS batch" in attempts[1] and "veto_source_unknown" in attempts[1]
    assert "stage=cross-recording code=veto_source_unknown path=vetoes[0].source_id" in caplog.text
    assert all(source["id"] not in caplog.text and source["text"] not in caplog.text
               for source in sources)
