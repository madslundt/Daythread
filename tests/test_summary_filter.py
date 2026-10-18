"""Summary request copies omit hesitation sounds without rewriting archive evidence."""

import copy
import json
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference
from omi_local.recaps import NO_CONTENT
from omi_local.settings import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


def source(text):
    return {"id": str(uuid4()), "started_at": "2026-10-18T10:00:00Z", "text": text}


async def summarize(tmp_path, sources):
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(json.loads(body["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": f"Notes [{sources[-1]['id']}]",
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        result, trace = await inference.summarize_traced(sources, "conversation notes")
    return result, trace, requests


@pytest.mark.anyio
async def test_fillers_are_removed_before_summary_requests_without_mutating_sources(tmp_path):
    sources = [source("Ummm, send øhhh, the invoice.\nEhm... Friday at 14:30, not Monday.")]
    original = copy.deepcopy(sources)
    _, trace, requests = await summarize(tmp_path, sources)
    evidence = requests[0]["evidence"]
    assert "send the invoice.\nFriday at 14:30, not Monday." in evidence
    assert sources == original
    assert trace["input_filtering"]["retained_characters"] < trace["input_filtering"][
        "original_characters"
    ]
    assert trace["input_filtering"]["removed_fillers"] == 3


@pytest.mark.anyio
async def test_only_fillers_and_non_speech_annotations_need_no_summary_call(tmp_path):
    result, trace, requests = await summarize(
        tmp_path, [source("ummm, øhhh...\n[Silence]\n[background noise]\nUh, ehm.")],
    )
    assert result == NO_CONTENT
    assert requests == []
    assert trace["model_calls"] == 0


@pytest.mark.anyio
async def test_omitted_whitespace_counts_as_zero_retained_characters(tmp_path):
    result, trace, requests = await summarize(tmp_path, [source(" \t\n")])
    assert result == NO_CONTENT
    assert requests == []
    assert trace["input_filtering"]["original_characters"] == 3
    assert trace["input_filtering"]["retained_characters"] == 0


@pytest.mark.anyio
async def test_short_answers_negation_names_quotes_and_ambiguous_words_survive(tmp_path):
    text = (
        'Yes. No. 42. Friday.\nI like it, you know. Well, er is a Danish verb.\n'
        'She said "ummm, øhhh". The label is ‘uh’.\n'
        'Use `ummm` and https://example.org/ummm; email um@example.org.\n'
        'Summer, number, Øhlsen, humming.\nHmm. Oh! Ah!'
    )
    _, _, requests = await summarize(tmp_path, [source(text)])
    assert text in requests[0]["evidence"]


@pytest.mark.anyio
async def test_empty_sources_are_omitted_but_meaningful_short_answers_keep_their_ids(tmp_path):
    empty, meaningful = source("øhhh... ummm"), source("No.")
    _, _, requests = await summarize(tmp_path, [empty, meaningful])
    assert empty["id"] not in requests[0]["evidence"]
    assert meaningful["id"] in requests[0]["evidence"]
    assert "No." in requests[0]["evidence"]


@pytest.mark.anyio
async def test_cleanup_happens_before_batching_to_avoid_unnecessary_reduction_calls(tmp_path):
    sources = [source("ummm, " * 4000 + "We agreed on Friday.")]
    _, trace, requests = await summarize(tmp_path, sources)
    assert len(requests) == 1
    assert trace["reduction_used"] is False
    assert "We agreed on Friday." in requests[0]["evidence"]


@pytest.mark.anyio
async def test_translation_and_chat_still_receive_original_hesitations(tmp_path):
    text = "ummm, øhhh... No, don't send it."
    requests = []

    def provider(request):
        requests.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Answer"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        await inference.translate(text, "da")
        await inference.chat("What did I say?", [source(text)])
    assert requests[0]["evidence"] == text
    assert json.loads(requests[1]["evidence"])[0]["text"] == text


@pytest.mark.anyio
async def test_multiline_quotes_and_code_keep_exact_annotations_and_whitespace(tmp_path):
    quoted = '"ummm,  uh\n[Silence]\n  øhhh"'
    code = '```\n[noise]\n  ummm  uh\n```'
    _, _, requests = await summarize(tmp_path, [source(f"Um, keep {quoted} and {code}")])
    assert quoted in requests[0]["evidence"]
    assert code in requests[0]["evidence"]
    assert "Um," not in requests[0]["evidence"]


@pytest.mark.anyio
async def test_symbols_can_be_meaningful_short_answers(tmp_path):
    _, _, requests = await summarize(tmp_path, [source("ummm, 👍")])
    assert "👍" in requests[0]["evidence"]


@pytest.mark.anyio
async def test_ambiguous_initials_and_ascii_measurement_units_are_preserved(tmp_path):
    _, _, requests = await summarize(tmp_path, [source("Ummm, UM measured 5 um on Friday.")])
    assert "UM measured 5 um on Friday." in requests[0]["evidence"]


@pytest.mark.anyio
async def test_large_chat_reduction_keeps_original_evidence(tmp_path):
    text = "ummm, " * 3000 + "No, don't send it."
    evidence = source(text)
    requests = []

    def provider(request):
        requests.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": f"Notes [{evidence['id']}]",
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        await inference.chat("What exactly did I say?", [evidence])
    assert len(requests) > 1
    assert "ummm," in requests[0]["evidence"]
    assert any("No, don't send it." in request["evidence"] for request in requests)


@pytest.mark.anyio
async def test_filler_only_recaps_and_rewind_skip_inference_and_keep_archive(tmp_path):
    from datetime import UTC, datetime, timedelta

    from omi_local.service import Memory
    from omi_local.store import Store

    at = datetime(2026, 10, 18, 10, tzinfo=UTC)
    original = "ummm, øhhh...\n[background noise]"
    store = Store(tmp_path)
    key = str(uuid4())
    store.ingest(key, "test", at, b"original audio", 30, source_kind="manual_upload")
    store.complete(key, original)

    def provider(request):
        pytest.fail("Filler-only recordings should never reach a summary provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
        memory = Memory(store, settings, Inference(settings, client))
        now = at + timedelta(hours=1)
        for _ in range(2):
            recap = await memory.recaps.generate(at.date(), now)
            assert recap["summary"] is None
            assert recap["no_recap_content"] is True
        rewind = await memory.window(at, now, "a rewind")
    assert rewind["summary"] is None
    assert rewind["no_recap_content"] is True
    assert store.get_audio(key)["text"] == original
    assert store.read_audio(key) == b"original audio"
