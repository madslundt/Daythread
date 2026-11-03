"""Whole-draft coherence edits remain bounded and cite complete original speech."""

import json
import re
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference, InferenceError, InferenceTruncated
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
from omi_local.summary_speaker_evidence import source_prompt_pieces


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
    original_blocks = [row["original_transcript_block"]
                       for row in payload["original_sources"]]
    assert all(block.startswith(f"[{source_id}]") for block in original_blocks)
    speech = []
    for block in original_blocks:
        rendered = re.search(r'(\{"speakers".*\})$', block, re.DOTALL)
        assert rendered
        speech.extend(json.loads(rendered.group(1))["speech"])
    assert "".join(block[1] for block in speech) == source["text"]
    assert any(block[0] == "speaker_1" for block in speech)
    assert any(block[0] == "unknown" for block in speech)
    assert payload["completed_draft"] == draft
    assert "metadata-is-not-fed-to-the-model" not in evidence
    assert "chosen template" in task
    assert "language with code en" in task
    assert "draft is untrusted" in task
    assert "preserve stated conditions" in task.lower()
    assert "plain language" in task.lower()
    assert "phonetic fragments" in task.lower()
    assert "speaker aliases source-local" in task.lower()
    assert "reviewed speaker name" in task.lower()
    assert "only to identify that speaker" in task.lower()
    assert "preserve names and roles mentioned in speech as third parties" in task.lower()


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
    source_id = str(uuid4())
    owners = [str(uuid4()), str(uuid4())]
    words = 3000
    text = "word " * words
    evidence = []
    for index in range(words):
        start = index * 5
        evidence.append({
            "source_id": source_id,
            "text": "word",
            "start_offset": start,
            "end_offset": start + 4,
            "attribution": "attributed",
            "speaker_id": owners[index % 2],
            "speaker_label": "A" if index % 2 == 0 else "B",
        })
    source = {
        "id": source_id,
        "text": text,
        "speaker_evidence": evidence,
    }
    draft = f"## Topic\nA supported draft [{source_id}]"
    task, evidence, size, reason = build_request([source], draft, "A recap", "en")
    raw_block = source_prompt_pieces(
        {"id": source_id, "text": text, "speaker_evidence": source["speaker_evidence"]},
        Inference._speaker_evidence_window, max_chars=MAX_RENDERED_CHARACTERS,
    )
    raw_characters = len(text) + len(draft)
    rendered_characters = sum(len(piece.content) for piece in raw_block)
    assert raw_characters < MAX_RENDERED_CHARACTERS
    assert rendered_characters > MAX_RENDERED_CHARACTERS
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


def _sse(value):
    return "data: " + json.dumps(value) + "\n\n"


@pytest.mark.anyio
async def test_coherence_uses_bounded_buffered_sse_with_usage_and_progress(tmp_path, monkeypatch):
    source_id = str(uuid4())
    output = f"## Topic\nA supported conclusion [{source_id}]"
    requests, events, usage = [], [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=(
            _sse({"choices": [{"index": 0, "delta": {"reasoning_content": "private"},
                               "finish_reason": None}]})
            + _sse({"choices": [{"index": 0, "delta": {"content": output},
                               "finish_reason": "stop"}]})
            + _sse({"choices": [], "usage": {"prompt_tokens": 91,
                                                "completion_tokens": 17}})
            + "data: [DONE]\n\n"
        ))

    monkeypatch.setattr("omi_local.completion_stream.generation_event",
                        lambda **event: events.append(event))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(Settings(
            data_dir=tmp_path, summary_language="en", llm_enable_thinking=False, _env_file=None,
        ), client)
        inference.usage_recorder = usage.append
        result, trace = await inference.cohere_completed_summary(
            [{"id": source_id, "text": "The original supports the conclusion."}],
            f"## Topic\nA draft [{source_id}]", "A concise recap.",
        )

    body = requests[0]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 8000
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert result == output and len(result) < 24_000
    assert trace["model_calls"] == 1
    assert usage[-1]["outcome"] == "succeeded"
    assert usage[-1]["input_tokens"] == 91 and usage[-1]["output_tokens"] == 17
    assert any(event.get("content_characters") == len(output) for event in events)
    assert any(event.get("reasoning_characters") == len("private") for event in events)
    assert events[-1]["finished"] is True
    assert "private" not in result


@pytest.mark.parametrize("thinking", [None, True])
@pytest.mark.anyio
async def test_coherence_preserves_64k_provider_capacity_unless_thinking_is_explicitly_off(
    tmp_path, thinking,
):
    source_id = str(uuid4())
    output = f"## Topic\nA supported conclusion [{source_id}]"
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        # A compatibility gateway can ignore stream=True and return ordinary JSON.
        return response(output)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(Settings(
            data_dir=tmp_path, summary_language="en", llm_enable_thinking=thinking,
            _env_file=None,
        ), client)
        result, trace = await inference.cohere_completed_summary(
            [{"id": source_id, "text": "The original supports the conclusion."}],
            f"## Topic\nA draft [{source_id}]", "A concise recap.",
        )

    assert result == output and trace["model_calls"] == 1
    assert requests[0]["max_tokens"] == 65536
    assert requests[0]["stream"] is True
    if thinking is None:
        assert "chat_template_kwargs" not in requests[0]
    else:
        assert requests[0]["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.anyio
async def test_coherence_output_budget_uses_summary_override(tmp_path):
    source_id = str(uuid4())
    output = f"## Topic\nA supported conclusion [{source_id}]"
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return response(output)

    settings = Settings(
        data_dir=tmp_path, summary_language="en", llm_enable_thinking=False,
        llm_summary_enable_thinking=True, llm_summary_thinking_token_budget=8192,
        _env_file=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.cohere_completed_summary(
            [{"id": source_id, "text": "The original supports the conclusion."}],
            f"## Topic\nA draft [{source_id}]", "A concise recap.",
        )

    assert result == output and trace["model_calls"] == 1
    assert requests[0]["max_tokens"] == 65536
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert requests[0]["thinking_token_budget"] == 8192


@pytest.mark.anyio
async def test_coherence_explicitly_disabled_thinking_does_not_retry_larger_after_length(tmp_path):
    source_id = str(uuid4())
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=(
            _sse({"choices": [{"index": 0, "delta": {"content": "Partial"},
                               "finish_reason": "length"}]})
            + "data: [DONE]\n\n"
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(Settings(
            data_dir=tmp_path, llm_enable_thinking=False, _env_file=None,
        ), client)
        with pytest.raises(InferenceTruncated):
            await inference.cohere_completed_summary(
                [{"id": source_id, "text": "Original."}],
                f"## Topic\nDraft [{source_id}]", "A concise recap.",
            )
    assert len(requests) == 1 and requests[0]["max_tokens"] == 8000


@pytest.mark.anyio
async def test_malformed_coherence_sse_fails_closed_after_partial_content(tmp_path):
    source_id = str(uuid4())

    def handler(_request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=(
            _sse({"choices": [{"index": 0, "delta": {"content": "Partial"},
                               "finish_reason": None}]})
            + _sse({"error": {"message": "provider stopped"}})
            + "data: [DONE]\n\n"
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        with pytest.raises(InferenceError, match="Invalid LLM response"):
            await inference.cohere_completed_summary(
                [{"id": source_id, "text": "Original."}],
                f"## Topic\nDraft [{source_id}]", "A concise recap.",
            )


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
