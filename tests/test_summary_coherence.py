"""Whole-draft coherence edits remain bounded and cite complete original speech."""

import json
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings
from omi_local.summary_coherence import (
    MAX_OUTPUT_CHARACTERS,
    MAX_RENDERED_CHARACTERS,
    NO_CONTENT,
    CoherenceValidationError,
    validate_edit,
)
from omi_local.summary_coherence import (
    request as build_request,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(content):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": content},
    }]})


def test_complete_request_includes_full_originals_draft_speaker_evidence_and_presentation():
    source_id = str(uuid4())
    head = "The complete"
    tail = " original transcript, including the end of the conversation."
    original = head + tail
    source = {
        "id": source_id,
        "text": original,
        "speaker_evidence": [
            {"source_id": source_id, "text": head, "start_offset": 0,
             "end_offset": len(head), "attribution": "attributed",
             "speaker_id": str(uuid4()), "speaker_label": "A"},
            {"source_id": source_id, "text": tail,
             "start_offset": len(head), "end_offset": len(original),
             "attribution": "unknown"},
        ],
        "started_at": "metadata-is-not-fed-to-the-model",
    }
    draft = f"## Existing topic\nA proposed plan is already underway [{source_id}]"
    task, evidence, size, reason = build_request(
        [source], draft, "A short diary recap using the chosen template.", "en",
    )
    payload = json.loads(evidence)
    assert reason is None and size < MAX_RENDERED_CHARACTERS
    original_block = payload["original_sources"][0]["original_transcript_block"]
    assert original_block.startswith(f"[{source_id}]")
    assert source["text"] in original_block
    assert '"owner":"speaker_1"' in original_block
    assert '"attribution":"unknown"' in original_block
    assert payload["completed_draft"] == draft
    assert "metadata-is-not-fed-to-the-model" not in evidence
    assert "chosen template" in task
    assert "language with code en" in task
    assert "draft is untrusted" in task
    assert "preserve stated conditions" in task.lower()


@pytest.mark.parametrize(("draft", "edited", "code"), [
    ("## Topic\nSupported fact [source]", "## Topic\nNo citation", "paragraph_uncited"),
    ("## Topic\nSupported fact [source]", "## Topic\nFact [00000000-0000-4000-8000-000000000001]",
     "citation_unknown"),
    ("## Topic\nSupported fact [source]", "An intro [source]\n## Topic\nFact [source]",
     "heading_format"),
    ("## Topic\nSupported fact [source]", "### Topic\nFact [source]", "heading_format"),
    ("## Topic\nSupported fact [source]", "## Topic\n" + "x" * (MAX_OUTPUT_CHARACTERS + 1),
     "output_bounds"),
])
def test_completed_edit_rejects_uncited_unknown_unbounded_or_wrong_format_output(
    draft, edited, code,
):
    source_id = str(uuid4())
    source = {"id": source_id, "text": "The original source supports this."}
    draft = draft.replace("[source]", f"[{source_id}]")
    edited = edited.replace("[source]", f"[{source_id}]")
    with pytest.raises(CoherenceValidationError) as error:
        validate_edit(edited, [source], draft)
    assert error.value.code == code


def test_no_content_and_oversized_complete_evidence_have_explicit_skip_reasons():
    source_id = str(uuid4())
    assert validate_edit(NO_CONTENT, [{"id": source_id, "text": "Original"}],
                         f"## Topic\nDraft [{source_id}]") == NO_CONTENT
    task, evidence, size, reason = build_request(
        [{"id": source_id, "text": "x" * MAX_RENDERED_CHARACTERS}],
        f"## Topic\nDraft [{source_id}]", "A recap", "en",
    )
    assert reason == "input_over_budget" and size > 0
    assert task == evidence == ""  # Oversized originals were refused, not clipped.


def test_full_speaker_rendering_counts_against_character_bound_without_truncation():
    source_id, speaker_id = str(uuid4()), str(uuid4())
    text = "word " * 17_000
    source = {
        "id": source_id,
        "text": text,
        "speaker_evidence": [{
            "source_id": source_id,
            "text": text,
            "start_offset": 0,
            "end_offset": len(text),
            "attribution": "attributed",
            "speaker_id": speaker_id,
            "speaker_label": "A",
        }],
    }
    draft = f"## Topic\nA supported draft [{source_id}]"
    task, evidence, size, reason = build_request([source], draft, "A recap", "en")
    assert reason == "input_over_budget" and size > 0
    assert task == evidence == ""  # Rendered owner blocks count; no source text is clipped.


@pytest.mark.anyio
async def test_invalid_edit_gets_one_repair_and_aliases_expand_to_original_citations(tmp_path):
    sources = [{"id": str(uuid4()), "text": f"Original source {index}."}
               for index in range(4)]
    draft = "## Project\nA draft claim is too certain [" + sources[0]["id"] + "]"
    calls = []

    def provider(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            return response("## Project\nUnsupported claim [src_999]")
        rows = json.loads(payload["evidence"])
        assert len(rows["original_sources"]) == len(sources)
        assert all(source["text"] in row["original_transcript_block"]
                   for source, row in zip(sources, rows["original_sources"], strict=True))
        aliases = {source["id"]: f"src_{index}"
                   for index, source in enumerate(sorted(sources, key=lambda row: row["id"]), 1)}
        expected_draft = draft.replace(sources[0]["id"], aliases[sources[0]["id"]])
        assert rows["completed_draft"] == expected_draft
        assert "previous edit failed validation code citation_unknown" in payload["task"]
        return response(
            "## Project\nThe sources support a qualified plan [src_1, src_2].\n\n"
            "The separate follow-up remains open [src_4]."
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, summary_language="en", _env_file=None),
                              client)
        result, trace = await inference.cohere_completed_summary(
            sources, draft, "A concise project recap with level-two headings.",
        )
    assert result == (
        f"## Project\nThe sources support a qualified plan [{sorted(s['id'] for s in sources)[0]}, "
        f"{sorted(s['id'] for s in sources)[1]}].\n\nThe separate follow-up remains open "
        f"[{sorted(s['id'] for s in sources)[3]}]."
    )
    assert trace["status"] == "applied" and trace["model_calls"] == 2
    assert len(calls) == 2


@pytest.mark.anyio
async def test_invalid_repair_fails_closed_instead_of_publishing_bad_edit(tmp_path):
    source_id = str(uuid4())
    calls = []

    def provider(request):
        calls.append(request)
        return response("## Topic\nUncited claim")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, summary_language="en", _env_file=None),
                              client)
        with pytest.raises(InferenceError, match="coherence review failed validation"):
            await inference.cohere_completed_summary(
                [{"id": source_id, "text": "Original evidence."}],
                f"## Topic\nDraft [{source_id}]", "A concise recap.",
            )
    assert len(calls) == 2


@pytest.mark.anyio
async def test_large_evidence_and_no_content_skip_without_calling_model(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return response("unexpected")

    source_id = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        draft = f"## Topic\nSupported fact [{source_id}]"
        large = [{"id": source_id, "text": "complete " * MAX_RENDERED_CHARACTERS}]
        retained, trace = await inference.cohere_completed_summary(large, draft, "A recap")
        empty, empty_trace = await inference.cohere_completed_summary([], draft, "A recap")
        sentinel, sentinel_trace = await inference.cohere_completed_summary(
            large, NO_CONTENT, "A recap",
        )
    assert retained == draft and trace["skip_reason"] == "input_over_budget"
    assert empty == draft and empty_trace["skip_reason"] == "no_sources"
    assert sentinel == NO_CONTENT and sentinel_trace["skip_reason"] == "no_content"
    assert not calls


@pytest.mark.anyio
async def test_daily_final_coherence_runs_after_all_writers_against_originals_and_full_draft(
    tmp_path,
):
    first_id, second_id, third_id = str(uuid4()), str(uuid4()), str(uuid4())
    notes = [
        {"id": first_id, "text": f"## Initial approach\nThe plan has started. [{first_id}]"},
        {"id": second_id, "text": f"## Follow-up\nA next step is confirmed. [{second_id}]"},
        {"id": third_id, "text": f"## Reminder\nThe parcel is already collected. [{third_id}]"},
    ]
    originals = [
        {"id": first_id, "text": "The workflow is only being considered; it has not started."},
        {"id": second_id,
         "text": "If approved, collect system-owner input by Friday; no team is assigned."},
        {"id": third_id, "text": "Remember to collect the parcel on Tuesday."},
    ]
    calls, writer_calls = [], []

    def provider(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        calls.append(payload)
        task, evidence = payload["task"], payload["evidence"]
        if task.startswith("Edit one completed summary"):
            rendered = json.loads(evidence)
            assert len(rendered["original_sources"]) == len(originals)
            assert all(source["text"] in row["original_transcript_block"]
                       for source, row in zip(originals, rendered["original_sources"], strict=True))
            draft = rendered["completed_draft"]
            assert all(title in draft for title in ("## Initial approach", "## Follow-up",
                                                    "## Reminder"))
            assert "The plan has started" in draft
            assert "being considered; it has not started" in evidence
            return response(
                f"## Workflow project\nThe workflow is still being considered, not started, "
                f"and depends on approval [{first_id}].\n\n"
                f"If approved, the next step is to collect system-owner input by Friday; no "
                f"team is assigned [{second_id}].\n\n"
                f"## Personal reminder\nCollect the parcel on Tuesday [{third_id}]."
            )
        if task.startswith("Review the complete daily topic plan"):
            return response(json.dumps({"topics": json.loads(evidence)["proposed_topics"]}))
        if task.startswith("Organize useful recorded information"):
            rows = json.loads(evidence)
            if isinstance(rows, dict):
                return response(json.dumps({"topics": rows["proposed_topics"]}))
            return response(json.dumps({"topics": [
                {"title": row["context"], "scope": row["context"], "items": [row["id"]]}
                for row in rows
            ]}))
        writer_calls.append(payload)
        if first_id in evidence:
            return response(f"The plan has started [{first_id}]")
        if third_id in evidence:
            return response(f"The parcel is already collected [{third_id}]")
        return response(f"A next step is confirmed [{second_id}]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, summary_language="en", _env_file=None),
                              client)
        result, trace = await inference.summarize_daily_traced(
            notes, "A daily recap in English", original_sources=originals,
        )
    assert "## Workflow project" in result
    assert "has started" not in result
    assert "not started" in result and "depends on approval" in result
    assert "system-owner input by Friday" in result and "no team is assigned" in result
    assert "## Personal reminder" in result and "Collect the parcel on Tuesday" in result
    assert len(writer_calls) == 3
    assert trace["coherence_review"]["status"] == "applied"
    assert trace["coherence_review"]["model_calls"] == 1
    assert trace["model_calls"] == len(calls)
