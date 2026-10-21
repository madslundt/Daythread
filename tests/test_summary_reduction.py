"""Long recording notes must converge without losing late evidence or looping."""

import json
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import SYSTEM, Inference, InferenceError, validate_citations
from omi_local.settings import Settings
from omi_local.summary_filter import NO_CONTENT


@pytest.fixture
def anyio_backend():
    return "asyncio"


def sources():
    return [{"id": str(uuid4()), "started_at": "2026-10-20T10:00:00Z",
             "text": label + " context" * 1000}
            for label in ("Early useful decision", "Late useful update")]


def response(content):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": content},
    }]})


@pytest.mark.anyio
async def test_long_recording_reduction_compacts_notes_before_final_whole_day_synthesis(tmp_path):
    evidence = sources()
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        points = [f"{label} [{source['id']}]" for label, source in zip(
            ("Early useful decision", "Late useful update"), evidence, strict=True
        ) if label in payload["evidence"]]
        if "intermediate evidence notes" in payload["task"] or len(points) == 2:
            return response("\n".join(points))
        # Models can reproduce equally long notes forever if asked for the final
        # full recap at every map/reduce level. Each note fits, but both do not.
        return response(points[0] + " supporting context" * 400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_traced(evidence, "a whole-day recap")
    assert "Early useful decision" in result and "Late useful update" in result
    assert all(f"[{source['id']}]" in result for source in evidence)
    assert len(calls) == trace["model_calls"] == 3
    assert trace["reduction_used"]
    assert "intermediate evidence notes" not in calls[-1]["task"]
    assert "a whole-day recap" not in calls[0]["task"]


@pytest.mark.anyio
async def test_nonshrinking_intermediate_notes_fail_after_one_repair_instead_of_looping(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return response("Substantive but uncompressed notes. " * 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        model = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        with pytest.raises(InferenceError, match="shortened safely"):
            await model.summarize(sources(), "a whole-day recap")
    assert len(calls) == 2


@pytest.mark.anyio
async def test_useful_citation_heavy_notes_can_exceed_four_thousand_chars_and_still_converge(
    tmp_path,
):
    evidence = sources()
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        points = [f"{label} [{source['id']}]" for label, source in zip(
            ("Early useful decision", "Late useful update"), evidence, strict=True
        ) if label in payload["evidence"]]
        return response("\n".join(points) + (" supporting context" * 230 if len(points) == 1
                                             else ""))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                 ).summarize(evidence, "a whole-day recap")
    assert "Early useful decision" in result and "Late useful update" in result
    assert len(calls) == 3


@pytest.mark.anyio
async def test_empty_intermediate_batch_does_not_contaminate_useful_later_notes(tmp_path):
    evidence = sources()
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if "Early useful decision" in payload["evidence"]:
            return response(NO_CONTENT)
        assert NO_CONTENT not in payload["evidence"]
        return response(f"Late useful update [{evidence[1]['id']}]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                 ).summarize(evidence, "a whole-day recap")
    assert "Late useful update" in result
    assert len(calls) == 3


@pytest.mark.anyio
async def test_intermediate_citations_are_repaired_against_the_current_batch_only(tmp_path):
    evidence = sources()
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            # This ID belongs to the overall day but not this batch's evidence.
            return response(f"Early useful decision [{evidence[1]['id']}]")
        points = [f"{label} [{source['id']}]" for label, source in zip(
            ("Early useful decision", "Late useful update"), evidence, strict=True
        ) if label in payload["evidence"]]
        return response("\n".join(points))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_traced(evidence, "a whole-day recap")
    assert all(f"[{source['id']}]" in result for source in evidence)
    assert len(calls) == trace["model_calls"] == 4


@pytest.mark.anyio
async def test_summary_system_accepts_grounded_notes_while_chat_keeps_original_evidence_policy(
    tmp_path,
):
    systems = []

    def provider(request):
        systems.append(json.loads(request.content)["messages"][0]["content"])
        return response("Supported summary")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        await inference.generate("Combine prepared notes", "Substantive notes")
        await inference.for_task("chat").generate("Recall the evidence", "Source excerpts")
    assert "Do not reject substantive notes" in systems[0]
    assert "support factual claims with the original transcript excerpts" not in systems[0]
    assert systems[1] == SYSTEM


def test_grouped_and_malformed_citations_cannot_bypass_source_validation():
    first, second = str(uuid4()), str(uuid4())
    known = [{"id": first}, {"id": second}]
    assert validate_citations(f"Plan [{first}, {second}]", known)
    with pytest.raises(InferenceError, match="unknown source"):
        validate_citations(f"Plan [{first}, {uuid4()}]", known)
    with pytest.raises(InferenceError, match="unknown source"):
        validate_citations(f"Nested [[{uuid4()}]]", known)
    malformed = "eef540cb-ba6d-4956-9020-fa47-46a4-b6dc-0aaa7c36fc27"
    with pytest.raises(InferenceError, match="unknown source"):
        validate_citations(f"Plan [{malformed}]", known)
    assert validate_citations("Keep [the original] and hash [deadbeef]", [])


@pytest.mark.anyio
@pytest.mark.parametrize("repair_succeeds", [True, False])
async def test_final_summary_repairs_invalid_citations_once(tmp_path, repair_succeeds):
    source = {"id": str(uuid4()), "started_at": "2026-10-20T10:00:00Z",
              "text": "The release is planned for next week, subject to testing."}
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        key = source["id"] if repair_succeeds and len(calls) == 2 else str(uuid4())
        return response(f"Release proposed for next week, subject to testing. [{key}]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        model = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        if repair_succeeds:
            result, trace = await model.summarize_traced([source], "a daily recap")
            assert f"[{source['id']}]" in result and "subject to testing" in result
            assert trace["model_calls"] == 2
        else:
            with pytest.raises(InferenceError, match="unknown source"):
                await model.summarize([source], "a daily recap")
    assert len(calls) == 2
    assert calls[1]["evidence"] == calls[0]["evidence"]
    assert "previous draft contained invalid source citations" in calls[1]["task"]


@pytest.mark.anyio
async def test_prepared_hour_notes_consolidate_without_unnecessary_recompression(tmp_path):
    evidence = sources()
    for source in evidence:
        source['citation_source_ids'] = [source['id']]
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        return response("\n".join(f"{label} [{source['id']}]" for label, source in zip(
            ("Early useful decision", "Late useful update"), evidence, strict=True
        )))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_traced(evidence, "a daily recap")
    assert "Early useful decision" in result and "Late useful update" in result
    assert len(calls) == 1 and len(calls[0]['evidence']) > 12000
    assert not trace['reduction_used']


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_first", [False, True])
async def test_prepared_reference_keys_expand_to_original_ids_and_unknown_keys_get_one_repair(
    tmp_path, invalid_first,
):
    ids = sorted(str(uuid4()) for _ in range(4))
    evidence = [{"id": key, "started_at": "2026-10-20T10:00:00Z",
                 "text": "Useful supported project update.", "citation_source_ids": [key]}
                for key in ids]
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        assert all(key not in payload['task'] + payload['evidence'] for key in ids)
        assert 'src_1' in payload['evidence'] and 'src_4' in payload['evidence']
        return response("Supported plans [src_999]" if invalid_first and len(calls) == 1
                        else "Supported plans [src_1, SRC_4]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                        ).summarize_traced(evidence, "a daily recap")
    assert result == f"Supported plans [{ids[0]}, {ids[3]}]"
    assert len(calls) == trace['model_calls'] == (2 if invalid_first else 1)
