"""An independent accuracy pass must prove corrections against original speech."""

import json
from uuid import uuid4

import httpx
import pytest

from omi_local.accuracy_transport import AccuracySources
from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings
from omi_local.summary_accuracy import (
    EVIDENCE_BUDGET,
    AccuracyReplyError,
    contains_quote,
    corrections,
    evidence_batches,
    reconciliation,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_review_corrects_proposed_work_without_downgrading_completed_work(tmp_path):
    key = str(uuid4())
    source = {"id": key, "started_at": "2026-10-20T10:00:00Z",
              "text": "I'll test the integration. The assessment was completed."}
    draft = "Integration testing is underway. The assessment was completed."

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        supplied = json.loads(payload["evidence"])
        if payload["task"].startswith("Check proposed draft corrections"):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": '{"vetoes":[]}',
            }}]})
        assert supplied == {"draft": draft, "originals": [{**source, "id": "src_1"}],
                            "allowed_source_ids": ["src_1"]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"corrections": [{
                "claim": "Integration testing is underway",
                "replacement": "Integration testing is planned",
                "evidence": [{"source_id": "src_1", "quote": "I'll test the integration"}],
            }]}),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary([source], draft)
    assert result == f"Integration testing is planned [{key}]. The assessment was completed."
    assert trace == {"model_calls": 1, "correction_count": 1, "checked_source_ids": [key],
                     "vetoed_corrections": 0}


@pytest.mark.anyio
@pytest.mark.parametrize('scoped', [False, True])
async def test_later_completed_work_is_not_downgraded_by_an_earlier_proposal_batch(
    tmp_path, scoped,
):
    first, last = str(uuid4()), str(uuid4())
    sources = [{"id": first, "text": "We propose deployment next week. "
                + "context " * (1 if scoped else 8400)},
               {"id": last, "text": "The same deployment has now completed."}]
    draft = "Deployment was reported completed."
    aliases = AccuracySources(sources).aliases

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        supplied = json.loads(payload["evidence"])
        originals = supplied["originals"]
        if payload["task"].startswith("Check proposed draft corrections"):
            result = {"vetoes": ([{"index": 0, "source_id": aliases[last],
                                    "quote": sources[1]["text"]}]
                                   if any(s["id"] == aliases[last] for s in originals) else [])}
        else:
            result = {"corrections": ([{
                "claim": "Deployment was reported completed",
                "replacement": "Deployment was proposed",
                "evidence": [{"source_id": aliases[first],
                              "quote": "We propose deployment next week"}],
            }] if "We propose deployment" in originals[0]["text"] else [])}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        topics = ([{'title': 'Deployment planning', 'source_ids': [first]},
                   {'title': 'Deployment completion', 'source_ids': [last]}] if scoped else None)
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary(sources, draft, topics=topics)
    assert result == draft
    assert trace["correction_count"] == 0 and trace["vetoed_corrections"] == 1


@pytest.mark.parametrize("fault", ["invented_quote", "other_source", "unknown_claim"])
def test_unverifiable_corrections_are_rejected(fault):
    key = str(uuid4())
    source = {"id": key, "text": "The team is looking into deployment."}
    row = {"claim": "Deployment is underway", "replacement": "Deployment is being explored",
           "evidence": [{"source_id": key, "quote": source["text"]}]}
    if fault == "invented_quote":
        row["evidence"][0]["quote"] = "Deployment was approved and started."
    elif fault == "other_source":
        row["evidence"][0]["source_id"] = str(uuid4())
    else:
        row["claim"] = "A different claim"
    with pytest.raises(InferenceError, match="accuracy review"):
        corrections(json.dumps({"corrections": [row]}), "Deployment is underway.", [source])


def test_original_review_batches_are_bounded_and_keep_both_ends_of_long_sources():
    key = str(uuid4())
    text = "Initially proposed. " + "context " * 14000 + "Now reported completed."
    batches = evidence_batches([{"id": key, "text": text}])
    assert len(batches) > 1
    assert all(len(json.dumps(batch, ensure_ascii=False)) < EVIDENCE_BUDGET + 500
               for batch in batches)
    assert "Initially proposed." in batches[0][0]["text"]
    assert "Now reported completed." in batches[-1][-1]["text"]
    assert all(source["id"] == key for batch in batches for source in batch)


def test_review_validates_quotes_in_each_piece_of_the_same_original():
    key = str(uuid4())
    sources = [{"id": key, "text": "First proposal is not yet started."},
               {"id": key, "text": "Different context at the end."}]
    row = {"claim": "Work is underway", "replacement": "Work was proposed",
           "evidence": [{"source_id": key, "quote": sources[0]["text"]}]}
    assert corrections(json.dumps({"corrections": [row]}), "Work is underway.", sources)


def test_ellipses_must_join_original_spans_in_order_without_invented_words():
    original = "The feature is delayed. We discussed the issue. It might ship this week."
    assert contains_quote("The feature is delayed...It might ship this week.", original)
    assert not contains_quote("It might ship this week...The feature is delayed.", original)
    assert not contains_quote("The feature is delayed...It has shipped this week.", original)


@pytest.mark.anyio
async def test_bounded_day_writer_keeps_all_original_qualifications_without_reduction(tmp_path):
    sources = [{"id": f"00000000-0000-4000-8000-{index:012}", "started_at": "",
                "text": "We are looking into deployment. " + "context " * 500}
               for index in range(1, 6)]
    sources[-1]["text"] += " A separate assessment was completed."
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        originals = json.loads(payload["evidence"])
        calls.append(payload)
        assert [s["text"] for s in originals] == [s["text"] for s in sources]
        assert len(originals) == 5
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": "Deployment is being explored. [src_1] The assessment completed. [src_5]",
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                 ).summarize_day_overview(sources, "a compact day recap")
    assert len(calls) == 1
    assert sources[0]["id"] in result and sources[-1]["id"] in result


@pytest.mark.anyio
async def test_topic_review_cannot_transfer_another_projects_completion(tmp_path):
    planned, completed = str(uuid4()), str(uuid4())
    sources = [{"id": planned, "text": "Metrics are agreed. Atlas could be an option."},
               {"id": completed, "text": "The unrelated beta deployment completed."}]
    topics = [{"title": "Analytics", "source_ids": [planned]},
              {"title": "Beta", "source_ids": [completed]}]
    draft = "Atlas was selected. The beta deployment completed."
    aliases = AccuracySources(sources).aliases
    reviewed = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        if payload["task"].startswith("Check proposed draft corrections"):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": '{"vetoes":[]}',
            }}]})
        originals = json.loads(payload["evidence"])["originals"]
        ids = [source["id"] for source in originals]
        reviewed.append(ids)
        assert len(ids) == 1
        rows = ([{"claim": "Atlas was selected", "replacement": "Atlas remains an option",
                  "evidence": [{"source_id": aliases[planned], "quote": sources[0]["text"]}]}]
                if ids == [aliases[planned]] else [])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"corrections": rows}),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary(sources, draft, topics=topics)
    assert result == f"Atlas remains an option [{planned}]. The beta deployment completed."
    assert sorted(reviewed) == sorted([[aliases[planned]], [aliases[completed]]])
    assert trace["model_calls"] == 3 and trace["correction_count"] == 1


@pytest.mark.anyio
async def test_overlapping_edits_are_reviewed_against_the_updated_prose(tmp_path):
    key = str(uuid4())
    sources = [{"id": key, "text": "We plan deployment. I will test the integration."}]
    draft = "Deployment is underway and testing is underway."
    seen = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        current = json.loads(payload["evidence"])["draft"]
        seen.append(current)
        pairs = ([('Deployment is underway', 'Deployment is planned'),
                  (draft, 'Deployment and testing are planned.')]
                 if len(seen) == 1 else [('testing is underway', 'testing is planned')])
        rows = [{"claim": claim, "replacement": replacement,
                 "evidence": [{"source_id": key, "quote": sources[0]["text"]}]}
                for claim, replacement in pairs]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"corrections": rows}),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary(sources, draft)
    assert seen[0] == draft and 'Deployment is planned' in seen[1]
    assert 'testing is planned' in result and 'underway' not in result
    assert trace['overlap_reviews'] == 1 and trace['model_calls'] == 2


@pytest.mark.anyio
async def test_later_completion_in_an_unseen_piece_of_the_same_source_is_preserved(tmp_path):
    key = str(uuid4())
    completed = 'The deployment has now completed.'
    sources = [{'id': key, 'text': 'We propose deployment next week. '
                + 'context ' * 8400 + completed}]
    draft = 'Deployment was reported completed.'

    def provider(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        text = '\n'.join(source['text']
                         for source in json.loads(payload['evidence'])['originals'])
        if payload['task'].startswith('Check proposed draft corrections'):
            result = {'vetoes': ([{'index': 0, 'source_id': key, 'quote': completed}]
                                 if completed in text else [])}
        else:
            result = {'corrections': ([{
                'claim': 'Deployment was reported completed',
                'replacement': 'Deployment was proposed',
                'evidence': [{'source_id': key, 'quote': 'We propose deployment next week.'}],
            }] if 'We propose deployment' in text else [])}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(result),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary(sources, draft)
    assert result == draft and trace['vetoed_corrections'] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["claim_not_unique", "evidence_required"])
async def test_review_repairs_exact_claim_case_and_missing_evidence(tmp_path, fault):
    key = str(uuid4())
    source = {"id": key, "text": "June is only proposed; no delivery date has been agreed."}
    draft = "During planning, the delivery date is confirmed for June."
    correct = {"claim": "the delivery date is confirmed for June",
               "replacement": "the delivery date remains tentative",
               "evidence": [{"source_id": key, "quote": source["text"]}]}
    malformed = dict(correct)
    if fault == "claim_not_unique":
        malformed["claim"] = "The delivery date is confirmed for June"
    else:
        malformed["evidence"] = []
    tasks = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        tasks.append(payload["task"])
        repaired = fault in payload["task"]
        if repaired and fault == "claim_not_unique":
            expected = json.dumps({"copy_this_exact_claim": correct["claim"]})[1:-1]
            assert expected in payload["task"]
        result = {"corrections": [correct if repaired else malformed]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary([source], draft)
    assert result == f"During planning, the delivery date remains tentative [{key}]."
    assert len(tasks) == 2 and fault not in tasks[0] and fault in tasks[1]
    assert trace["correction_count"] == 1


def test_case_only_claim_suggestion_is_exact_but_never_accepts_the_invalid_claim():
    key = str(uuid4())
    source = {"id": key, "text": "The delivery date is only proposed."}
    invalid = {"claim": "The delivery date is confirmed",
               "replacement": "the delivery date remains tentative",
               "evidence": [{"source_id": key, "quote": source["text"]}]}
    with pytest.raises(AccuracyReplyError) as caught:
        corrections(json.dumps({"corrections": [invalid]}),
                    "During planning, the delivery date is confirmed.", [source])
    assert caught.value.feedback["copy_this_exact_claim"] == "the delivery date is confirmed"
    # Multiple case-only spans are ambiguous; no suggested match may pick one for the model.
    invalid["claim"] = "THE DELIVERY DATE IS CONFIRMED"
    with pytest.raises(AccuracyReplyError) as ambiguous:
        corrections(json.dumps({"corrections": [invalid]}),
                    "The delivery date is confirmed; the delivery date is confirmed.", [source])
    assert "copy_this_exact_claim" not in ambiguous.value.feedback


@pytest.mark.anyio
@pytest.mark.parametrize("fault", [
    "veto_index_invalid", "veto_source_unknown", "veto_quote_unattested",
])
async def test_reconciliation_repairs_invalid_index_source_and_quote_without_loosening(
    tmp_path, fault,
):
    first, last = str(uuid4()), str(uuid4())
    sources = [{"id": first, "text": "We propose deployment next week."},
               {"id": last, "text": "The same deployment has now completed."}]
    draft = "Deployment was reported completed."
    aliases = AccuracySources(sources).aliases
    correction = {"claim": "Deployment was reported completed",
                  "replacement": "Deployment was proposed",
                  "evidence": [{"source_id": aliases[first], "quote": sources[0]["text"]}]}
    valid = {"index": 0, "source_id": aliases[last], "quote": sources[1]["text"]}
    invalid = dict(valid)
    invalid.update({"index": 1} if fault == "veto_index_invalid" else
                   {"source_id": str(uuid4())} if fault == "veto_source_unknown" else
                   {"quote": "An invented completion statement was supplied."})
    reconciliations = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        supplied = json.loads(payload["evidence"])
        if payload["task"].startswith("Check proposed draft corrections"):
            reconciliations.append((payload["task"], supplied))
            result = {"vetoes": [valid if fault in payload["task"] else invalid]}
        else:
            result = {"corrections": [correction]
                      if supplied["originals"][0]["id"] == aliases[first]
                      else []}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        topics = [{"title": "Planning", "source_ids": [first]},
                  {"title": "Completion", "source_ids": [last]}]
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).review_summary(sources, draft, topics=topics)
    assert result == draft and trace["vetoed_corrections"] == 1
    assert len(reconciliations) == 2 and fault in reconciliations[1][0]
    assert reconciliations[0][1]["corrections"][0]["index"] == 0
    assert set(reconciliations[0][1]["allowed_source_ids"]) == set(aliases.values())
    assert set(reconciliations[0][1]["corrections"][0]) == {"index", "claim", "replacement"}


@pytest.mark.parametrize("invalid", [
    {"index": True, "source_id": "known", "quote": "The work has been completed."},
    {"index": "0", "source_id": "known", "quote": "The work has been completed."},
    {"index": 1, "source_id": "known", "quote": "The work has been completed."},
    {"index": 0, "source_id": "other", "quote": "The work has been completed."},
    {"index": 0, "source_id": "known", "quote": "Done."},
    {"index": 0, "source_id": "known", "quote": "The work is only proposed."},
])
def test_reconciliation_does_not_coerce_indices_or_accept_unattested_vetoes(invalid):
    sources = [{"id": "known", "text": "The work has been completed."}]
    with pytest.raises(AccuracyReplyError, match="Invalid cross-recording accuracy review"):
        reconciliation(json.dumps({"vetoes": [invalid]}), [{}], sources, [[]])


@pytest.mark.anyio
async def test_review_still_fails_closed_when_repair_remains_invalid(tmp_path, caplog):
    key = str(uuid4())
    source = {"id": key, "text": "The delivery date is only proposed."}
    calls = []

    def provider(request):
        calls.append(request)
        result = {"corrections": [{"claim": "Delivery is confirmed", "replacement": "Proposed",
                                  "evidence": []}]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result),
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(AccuracyReplyError, match="Invalid source-grounded accuracy review"):
            await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                            ).review_summary([source], "Delivery is confirmed.")
    assert len(calls) == 2
    assert ("stage=source-grounded code=evidence_required path=corrections[0].evidence"
            in caplog.text)
    assert key not in caplog.text and source["text"] not in caplog.text
    assert "Delivery is confirmed" not in caplog.text
