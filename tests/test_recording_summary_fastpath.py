"""Single selected whole-recording summaries use a complete-source request when proven safe."""

import json
import re
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from omi_local import inference as inference_module
from omi_local.inference import SUMMARY_SYSTEM, Inference, InferenceError
from omi_local.recaps import Recaps
from omi_local.settings import Settings
from omi_local.summary_filter import NO_CONTENT


@pytest.fixture
def anyio_backend():
    return "asyncio"


def full_recording(text: str, source_id: str | None = None) -> tuple[dict, dict]:
    source_id = source_id or str(uuid4())
    source_ids = [str(uuid4()) for _ in range(10)]
    split = text.index(" ")
    original = {
        "id": source_id,
        "recording_id": source_id,
        "reference_type": "recording_transcription",
        "result_version": "a" * 64,
        "input_fingerprint": "b" * 64,
        "pipeline_fingerprint": "c" * 64,
        "selection_version": 1,
        "source_ids": source_ids,
        "source_map": [{"source_id": item} for item in source_ids],
        "text": text,
        "speaker_evidence": [
            {
                "source_id": source_id, "text": text[:split], "start_offset": 0,
                "end_offset": split, "attribution": "attributed",
                "speaker_id": str(uuid4()), "speaker_label": "A",
            },
            {
                "source_id": source_id, "text": text[split:], "start_offset": split,
                "end_offset": len(text), "attribution": "unknown",
            },
        ],
    }
    prepared = {"id": source_id, "text": "old prepared note", "citation_source_ids": [source_id]}
    return prepared, original


def completion(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": text},
    }]})


def speech_blocks(evidence: str) -> list[dict]:
    match = re.search(r'(\{"speakers".*\})$', evidence, re.DOTALL)
    assert match
    return json.loads(match.group(1))["speech"]


def speech_owner(item):
    return item[0] if isinstance(item, list) else item.get("speaker", item.get("attribution"))


def speech_text(item):
    return item[1] if isinstance(item, list) else item["text"]


@pytest.mark.anyio
async def test_complete_typed_recording_uses_one_full_transcript_writer_when_context_is_verified(
    tmp_path,
):
    source_id = str(uuid4())
    canonical = "Beginning of the full recording. Final useful detail at the end."
    prepared, source = full_recording(canonical, source_id)
    requests, output = [], f"## Recorded plan\nA supported next step [{source_id}]"
    settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"])
        assert "Create a concise diary recap" in payload["task"]
        assert "language with code en" in payload["task"]
        speech = speech_blocks(payload["evidence"])
        assert "".join(speech_text(row) for row in speech) == canonical
        assert any(speech_owner(row) == "speaker_1" for row in speech)
        assert any(speech_owner(row) == "unknown" for row in speech)
        assert "old prepared note" not in payload["evidence"]
        assert body["max_tokens"] == 65536 and body["stream"] is True
        return completion(output)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_daily_traced(
            [prepared], "a concise diary recap", original_sources=[source],
        )

    assert result == output
    assert len(requests) == 1
    assert trace["complete_context"]["status"] == "used"
    assert trace["model_calls"] == 1 and trace["reduction_used"] is False
    assert trace["final_prompt_source_ids"] == [source_id]
    assert trace["coherence_review"]["skip_reason"] == "complete_context_single_pass"


@pytest.mark.anyio
async def test_conversation_reduction_uses_complete_recording_fastpath(tmp_path):
    source_id = str(uuid4())
    canonical = "The first part. The final part remains included."
    _, source = full_recording(canonical, source_id)
    calls = []
    settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)
    output = f"## Complete notes\nA supported note [{source_id}]"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        calls.append(request)
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        assert "\"notes from the complete source\"" not in payload["evidence"]
        assert "Complete notes" not in payload["evidence"]
        speech = speech_blocks(payload["evidence"])
        assert "".join(speech_text(row) for row in speech) == canonical
        return completion(output)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_traced([source], "notes from the complete source")

    assert result == output and len(calls) == 1
    assert trace["complete_context_one_pass"] is True
    assert trace["model_calls"] == 1 and trace["final_prompt_source_ids"] == [source_id]


@pytest.mark.anyio
async def test_conversation_preparation_skips_redundant_coherence_after_fastpath(
    tmp_path, monkeypatch,
):
    source_id = str(uuid4())
    _, source = full_recording("The whole transcript is present.", source_id)
    saved = []
    settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)
    output = f"## Complete notes\nUseful fact [{source_id}]"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        return completion(output)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        memory = SimpleNamespace(inference=inference, store=object())
        recaps = Recaps(memory)
        monkeypatch.setattr(recaps, "group_key", lambda _group: "group")
        monkeypatch.setattr(recaps, "group_summary", lambda _key: None)
        monkeypatch.setattr(recaps, "sources", lambda _group: [source])
        monkeypatch.setattr(recaps, "speaker_projection", lambda _group: {})
        monkeypatch.setattr(recaps, "save_group_summary",
                            lambda key, text, sources, guard: saved.append((key, text, sources)))

        async def forbidden_topics(_group):
            pytest.fail("One-pass full recording must not wait for topic maps")

        monkeypatch.setattr(recaps, "prepare_topics", forbidden_topics)
        group = SimpleNamespace(
            sources=[{"id": source_id}], basis="pause", hints={"markers": [], "meetings": []},
        )
        coherence, generation = [], []
        result = await recaps.prepare_group(
            group, lambda _db: None, coherence_trace=coherence, generation_trace=generation,
        )
    assert result == output and saved[0][1] == output
    assert coherence[0]["skip_reason"] == "complete_context_single_pass"
    assert generation[0]["complete_context_one_pass"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(("context", "advertised"), [(65536, True), (None, False)])
async def test_small_or_unknown_provider_context_preserves_existing_map_reduce(
    tmp_path, monkeypatch, context, advertised,
):
    prepared, source = full_recording("Beginning and end of the complete recording.")
    settings = Settings(data_dir=tmp_path, _env_file=None)
    calls = []

    def handler(request):
        assert request.method == "GET"
        rows = ([{"id": settings.llm_route("summary").model, "context_window": context}]
                if advertised else [])
        return httpx.Response(200, json={"data": rows})

    async def generic(inference, sources, purpose, *, original_sources=None):
        calls.append((sources, purpose, original_sources))
        return NO_CONTENT, {"model_calls": 0, "reduction_used": False}

    monkeypatch.setattr("omi_local.daily_synthesis.summarize_daily", generic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_daily_traced(
            [prepared], "a recap", original_sources=[source],
        )
    assert result == NO_CONTENT and len(calls) == 1
    assert trace["coherence_review"]["skip_reason"] == "no_content"
    assert trace["map_cache_hits"] >= 0


@pytest.mark.anyio
async def test_formatted_full_transcript_over_cap_falls_back_even_when_raw_text_fits(
    tmp_path, monkeypatch,
):
    source_id = str(uuid4())
    words = 7000
    text = "word " * words
    owners = [str(uuid4()), str(uuid4())]
    source = {
        "id": source_id, "recording_id": source_id,
        "reference_type": "recording_transcription", "result_version": "a" * 64,
        "input_fingerprint": "b" * 64, "pipeline_fingerprint": "c" * 64,
        "selection_version": 1, "source_ids": [str(uuid4()) for _ in range(10)],
        "text": text,
        "speaker_evidence": [{
            "source_id": source_id, "text": "word", "start_offset": i * 5,
            "end_offset": i * 5 + 4, "attribution": "attributed",
            "speaker_id": owners[i % 2],
            "speaker_label": "A" if i % 2 == 0 else "B",
        } for i in range(words)],
    }
    source["source_map"] = [{"source_id": item} for item in source["source_ids"]]
    prepared = {"id": source_id, "text": "notes", "citation_source_ids": [source_id]}
    settings = Settings(data_dir=tmp_path, _env_file=None)
    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        calls.append(request)
        return completion("unexpected")

    async def generic(_inference, _sources, _purpose, *, original_sources=None):
        return NO_CONTENT, {"model_calls": 0, "reduction_used": False}

    monkeypatch.setattr("omi_local.daily_synthesis.summarize_daily", generic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, _ = await inference.summarize_daily_traced(
            [prepared], "a recap", original_sources=[source],
        )
    assert len(text) < 64_000
    assert result == NO_CONTENT and calls == []


@pytest.mark.anyio
async def test_fastpath_requires_exactly_one_matching_typed_source(tmp_path, monkeypatch):
    source_id = str(uuid4())
    prepared, source = full_recording("Complete original transcript.", source_id)
    second = {"id": str(uuid4()), "text": "ordinary source"}
    settings = Settings(data_dir=tmp_path, _env_file=None)
    generic_calls = []

    def handler(request):
        pytest.fail(f"Fastpath should not probe or generate for multiple sources: {request.method}")

    async def generic(_inference, sources, _purpose, *, original_sources=None):
        generic_calls.append((sources, original_sources))
        return NO_CONTENT, {"model_calls": 0, "reduction_used": False}

    monkeypatch.setattr("omi_local.daily_synthesis.summarize_daily", generic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, _ = await inference.summarize_daily_traced(
            [prepared], "a recap", original_sources=[source, second],
        )
    assert result == NO_CONTENT and len(generic_calls) == 1


@pytest.mark.anyio
async def test_prepared_typed_notes_without_original_sources_cannot_use_original_fastpath(
    tmp_path, monkeypatch,
):
    _, source = full_recording("Original canonical transcript.")
    prepared = {
        **source, "text": "Prepared notes are not the original transcript.",
        "citation_source_ids": [source["id"]],
    }
    settings = Settings(data_dir=tmp_path, _env_file=None)
    generic_calls = []

    def handler(_request):
        pytest.fail("No original transcript was supplied; do not probe or call the model")

    async def generic(_inference, sources, _purpose, *, original_sources=None):
        generic_calls.append((sources, original_sources))
        return NO_CONTENT, {"model_calls": 0, "reduction_used": False}

    monkeypatch.setattr("omi_local.daily_synthesis.summarize_daily", generic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, _ = await inference.summarize_daily_traced([prepared], "a recap")
    assert result == NO_CONTENT and len(generic_calls) == 1


@pytest.mark.anyio
async def test_prepared_source_citation_metadata_disqualifies_complete_original_fastpath(
    tmp_path, monkeypatch,
):
    prepared, source = full_recording("Original canonical transcript.")
    source["citation_source_ids"] = [source["id"]]
    settings = Settings(data_dir=tmp_path, _env_file=None)
    generic_calls = []

    def handler(_request):
        pytest.fail("Prepared evidence cannot be presented as original speech")

    async def generic(_inference, sources, _purpose, *, original_sources=None):
        generic_calls.append((sources, original_sources))
        return NO_CONTENT, {"model_calls": 0, "reduction_used": False}

    monkeypatch.setattr("omi_local.daily_synthesis.summarize_daily", generic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, _ = await inference.summarize_daily_traced(
            [prepared], "a recap", original_sources=[source],
        )
    assert result == NO_CONTENT and len(generic_calls) == 1


@pytest.mark.anyio
async def test_noise_only_complete_recording_is_refused_before_generation(tmp_path):
    source_id = str(uuid4())
    _, source = full_recording("um uh", source_id)
    requests = []
    settings = Settings(data_dir=tmp_path, _env_file=None)

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        requests.append(request)
        return completion(f"## Topic\nInvented substance [{source_id}]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_complete_recording_traced(
            source, "a daily recap", require_subject_headings=True,
        )
    assert result == NO_CONTENT and requests == []
    assert trace["model_calls"] == 0 and trace["input_filtering"]["omitted_sources"] == 1
    assert trace["complete_context_one_pass"] is False


@pytest.mark.anyio
async def test_useful_recording_keeps_raw_text_in_anchored_prompt_after_filter_check(tmp_path):
    source_id = str(uuid4())
    text = "We agreed. Um, the plan is ready."
    _, source = full_recording(text, source_id)
    settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)
    output = f"## Plan\nThe plan is ready [{source_id}]"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        speech = speech_blocks(payload["evidence"])
        assert "".join(speech_text(row) for row in speech) == text
        assert any("Um," in speech_text(row) for row in speech)
        return completion(output)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_complete_recording_traced(
            source, "a daily recap", require_subject_headings=True,
        )
    assert result == output and trace["model_calls"] == 1


@pytest.mark.anyio
async def test_complete_recording_uses_reversible_short_citation_alias(tmp_path):
    source_id = str(uuid4())
    _, source = full_recording("The original text has a useful outcome.", source_id)
    settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)
    requests = []
    output = f"## Outcome\nThe outcome is supported [{source_id}]"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body["messages"][1]["content"])
        assert "[src_1]" in payload["task"]
        assert source_id not in payload["task"]
        assert source_id not in body["messages"][1]["content"]
        return completion(output.replace(source_id, "src_1"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_complete_recording_traced(
            source, "a daily recap", require_subject_headings=True,
        )
    assert result == output and trace["model_calls"] == 1
    body = requests[0]
    assert trace["complete_context"]["request_characters"] == (
        len(SUMMARY_SYSTEM) + len(body["messages"][1]["content"]) + 128
    )


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_output", [
    "## Topic\nNo citation",
    "## Topic\nAttributed fact [src_1]\n\nUncited factual paragraph.",
    "No heading here",
])
async def test_daily_fastpath_repairs_missing_citation_or_subject_heading(
    tmp_path, invalid_output,
):
    source_id = str(uuid4())
    _, source = full_recording("The daily topic has useful content.", source_id)
    settings = Settings(data_dir=tmp_path, summary_language="en", _env_file=None)
    requests = []
    output = f"## Daily topic\nThe useful content remains cited [{source_id}]"

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 262144,
            }]})
        body = json.loads(request.content)
        requests.append(body)
        task = json.loads(body["messages"][1]["content"])["task"]
        if len(requests) == 1:
            return completion(invalid_output)
        assert "previous draft" in task
        assert "[src_1]" in task
        return completion(output.replace(source_id, "src_1"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_complete_recording_traced(
            source, "a daily recap", require_subject_headings=True,
        )
    assert result == output and len(requests) == 2
    assert trace["model_calls"] == 2 and trace["complete_context_one_pass"] is True


def test_complete_context_validator_requires_citations_per_paragraph_and_accepts_lists():
    source_id = str(uuid4())
    invalid = (
        f"## Plan\nSupported fact [{source_id}]\n\n"
        "A second factual paragraph without a citation."
    )
    with pytest.raises(InferenceError):
        Inference._validate_complete_recording_output(invalid, source_id, True)

    valid_list = (
        f"## Plan\n- First supported fact [{source_id}]\n"
        f"- Second supported fact [{source_id}]"
    )
    assert Inference._validate_complete_recording_output(valid_list, source_id, True) == valid_list


@pytest.mark.anyio
async def test_context_fallback_filters_source_once_in_existing_reducer(tmp_path, monkeypatch):
    source_id = str(uuid4())
    _, source = full_recording("A meaningful original sentence.", source_id)
    settings = Settings(data_dir=tmp_path, _env_file=None)
    requests = []
    filter_calls = []
    original_filter = inference_module.filter_summary_sources

    def counting_filter(sources):
        filter_calls.append(len(sources))
        return original_filter(sources)

    monkeypatch.setattr("omi_local.inference.filter_summary_sources", counting_filter)

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": settings.llm_route("summary").model, "context_window": 65536,
            }]})
        requests.append(request)
        return completion(f"## Topic\nA useful fact [{source_id}]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, trace = await inference.summarize_traced([source], "conversation notes")
    assert "A useful fact" in result
    assert len(requests) == 1 and filter_calls == [1]
    assert "complete_context_one_pass" not in trace


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_field", ["canonical_text", "speaker_evidence", "speaker_evidence_member"],
)
async def test_excerpt_or_malformed_complete_variant_never_uses_single_pass(
    tmp_path, monkeypatch, invalid_field,
):
    prepared, source = full_recording("Complete original transcript.")
    if invalid_field == "canonical_text":
        source[invalid_field] = "A different full transcript"
    elif invalid_field == "speaker_evidence_member":
        source["speaker_evidence"] = [None]
    else:
        source[invalid_field] = None
    settings = Settings(data_dir=tmp_path, _env_file=None)
    generic_calls = []

    def handler(_request):
        pytest.fail("Invalid whole-recording evidence must not trigger a context probe")

    async def generic(_inference, sources, _purpose, *, original_sources=None):
        generic_calls.append((sources, original_sources))
        return NO_CONTENT, {"model_calls": 0, "reduction_used": False}

    monkeypatch.setattr("omi_local.daily_synthesis.summarize_daily", generic)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inference = Inference(settings, client)
        result, _ = await inference.summarize_daily_traced(
            [prepared], "a recap", original_sources=[source],
        )
    assert result == NO_CONTENT and len(generic_calls) == 1
