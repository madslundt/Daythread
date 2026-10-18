"""Shared summary filtering, provider submissions and durable usage measurements."""

import copy
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference
from omi_local.inference_usage import summary_source_duration
from omi_local.service import Memory, public_source
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.summary_filter import VERSION, clean_summary_text
from omi_local.usage import UsageLedger


@pytest.fixture
def anyio_backend():
    return "asyncio"


def receipt(credits="5.125"):
    return {"X-Daythread-Credits-Used": credits, "X-Daythread-Billing": "credits",
            "X-Daythread-Operation-Id": str(uuid4())}


def response(key, *, credits="5.125", inputs=123, outputs=7):
    return httpx.Response(200, headers=receipt(credits), json={
        "choices": [{"message": {"content": f"Notes [{key}]"}}],
        "usage": {"prompt_tokens": inputs, "completion_tokens": outputs},
    })


@pytest.mark.anyio
async def test_summary_uses_shared_pipeline_measures_submissions_and_keeps_originals(tmp_path):
    store = Store(tmp_path)
    key = str(uuid4())
    at = datetime(2026, 10, 18, 10, tzinfo=UTC)
    original = "ummm, send øhhh, the invoice on Friday. Not Monday."
    store.ingest(key, "omi", at, b"original audio bytes", 30, source_kind="manual_upload")
    store.complete(key, original)
    sources = [public_source(store.get_audio(key))]
    before = copy.deepcopy(sources)
    submitted = []

    def provider(request):
        body = json.loads(request.content)
        submitted.append(sum(len(message["content"]) for message in body["messages"]))
        evidence = json.loads(body["messages"][1]["content"])["evidence"]
        assert "send the invoice on Friday. Not Monday." in evidence
        assert "ummm" not in evidence and "øhhh" not in evidence
        return response(key)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
        memory = Memory(store, settings, Inference(settings, client))
        await memory.inference.summarize(sources, "conversation notes")
    report = memory.usage.report()
    totals = report["totals"]
    cleaned, removed = clean_summary_text(original)
    assert totals["summary_original_characters"] == len(original)
    assert totals["summary_retained_characters"] == len(cleaned)
    assert totals["summary_removed_fillers"] == removed == 2
    assert totals["summary_submitted_characters"] == sum(submitted)
    assert totals["summary_submitted_characters"] > totals["summary_retained_characters"]
    assert totals["summary_requests"] == totals["summary_filter_runs"] == 1
    assert totals["summary_skipped_runs"] == 0
    assert totals["summary_source_minutes"] == 0.5
    assert totals["summary_unknown_duration_runs"] == 0
    assert totals["summary_input_tokens"] == 123
    assert totals["summary_output_tokens"] == 7
    assert totals["summary_unknown_token_requests"] == 0
    assert totals["summary_credits"] == 5.125
    assert report["credits_complete"]
    assert sources == before
    assert store.get_audio(key)["text"] == original
    assert store.read_audio(key) == b"original audio bytes"
    with store.connect() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM usage_events")]
    assert len({event["summary_run_id"] for event in events}) == 1
    assert next(e for e in events if e["kind"] == "summary_filter")["filter_version"] == VERSION
    assert all(not {"text", "evidence", "messages", "response"} & e.keys() for e in events)
    restarted = UsageLedger(Store(tmp_path)).report()
    assert restarted["totals"] == totals


@pytest.mark.anyio
@pytest.mark.parametrize("original", ["ummm, øhhh...\n[background noise]", " \n  "])
async def test_omitted_summary_records_filtering_without_a_provider_request(tmp_path, original):
    ledger = UsageLedger(Store(tmp_path))
    sources = [{"id": str(uuid4()), "started_at": "2026-10-18T10:00:00Z",
                "ended_at": "2026-10-18T10:01:00Z", "text": original}]

    def unexpected(request):
        pytest.fail("Omitted summary input must not make a provider request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        inference.usage_recorder = ledger.record
        await inference.summarize(sources, "a recap")
    totals = ledger.report()["totals"]
    assert totals["summary_original_characters"] == len(original)
    assert totals["summary_retained_characters"] == 0
    assert totals["summary_filter_runs"] == totals["summary_skipped_runs"] == 1
    assert totals["summary_requests"] == totals["summary_submitted_characters"] == 0
    assert totals["summary_credits"] == 0
    assert totals["summary_source_minutes"] == 1  # Considered, although no text was submitted.
    assert totals["summary_unknown_duration_runs"] == 0
    assert sources[0]["text"] == original


@pytest.mark.anyio
async def test_summary_reduction_measures_all_real_requests_once_per_filter_run(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    key = str(uuid4())
    original = "We agreed on Friday. " * 1000
    sources = [{"id": key, "started_at": "2026-10-18T10:00:00Z",
                "ended_at": "2026-10-18T10:30:00Z", "text": original}]
    submitted = []

    def provider(request):
        body = json.loads(request.content)
        submitted.append(sum(len(message["content"]) for message in body["messages"]))
        return response(key, credits="5", inputs=100, outputs=10)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        inference.usage_recorder = ledger.record
        await inference.summarize(sources, "a recap")
    totals = ledger.report()["totals"]
    assert len(submitted) > 1
    assert totals["summary_filter_runs"] == 1
    assert totals["summary_source_minutes"] == 30  # Not multiplied by reduction requests.
    assert totals["summary_original_characters"] == len(original)
    assert totals["summary_retained_characters"] == len(original)
    assert totals["summary_requests"] == len(submitted)
    assert totals["summary_submitted_characters"] == sum(submitted)
    assert totals["summary_input_tokens"] == 100 * len(submitted)
    assert totals["summary_output_tokens"] == 10 * len(submitted)
    assert totals["summary_credits"] == 5 * len(submitted)


@pytest.mark.anyio
async def test_summary_retry_counts_attempts_and_leaves_timeout_charge_unknown(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    key = str(uuid4())
    sources = [{"id": key, "started_at": "2026-10-18T10:00:00Z", "text": "ummm, No."}]
    calls = []

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("private diagnostic", request=request)
        return response(key)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        inference.usage_recorder = ledger.record
        with pytest.raises(httpx.ReadTimeout):
            await inference.summarize(sources, "a recap")
        await inference.summarize(sources, "a recap")
    report = ledger.report()
    totals = report["totals"]
    assert totals["summary_requests"] == totals["summary_filter_runs"] == 2
    assert totals["summary_unknown_token_requests"] == totals["unknown_credit_attempts"] == 1
    assert totals["summary_unknown_character_requests"] == 0
    assert totals["summary_input_tokens"] == 123
    assert totals["summary_credits"] == 5.125
    assert not report["credits_complete"]
    assert sources[0]["text"] == "ummm, No."


@pytest.mark.anyio
async def test_translation_preserves_text_and_has_separate_usage_category(tmp_path):
    ledger = UsageLedger(Store(tmp_path))
    original = "ummm, øhhh... No, don't send it."

    def provider(request):
        body = json.loads(request.content)
        assert json.loads(body["messages"][1]["content"])["evidence"] == original
        return response(str(uuid4()), credits="5")

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        inference.usage_recorder = ledger.record
        await inference.translate(original, "da")
    totals = ledger.report()["totals"]
    assert totals["summary_requests"] == totals["summary_filter_runs"] == 0
    assert totals["summary_credits"] == 0
    assert totals["other_credits"] == 5


def test_summary_source_duration_unions_overlaps_and_keeps_cached_note_scope():
    sources = [
        {"started_at": "2026-10-18T10:00:00Z", "ended_at": "2026-10-18T10:05:00Z",
         "text": "Conversation notes covering all recordings"},
        {"started_at": "2026-10-18T12:04:00+02:00", "ended_at": "2026-10-18T12:10:00+02:00",
         "text": ""},
        {"started_at": "2026-10-18T10:20:00Z", "ended_at": "2026-10-18T10:21:00Z",
         "text": ""},
    ]
    before = copy.deepcopy(sources)
    assert summary_source_duration(sources) == {
        "source_seconds": 660, "source_duration_complete": True,
    }
    assert sources == before


@pytest.mark.parametrize("bad", [
    {}, {"started_at": None, "ended_at": 123},
    {"started_at": "2026-10-18T10:00:00", "ended_at": "2026-10-18T10:01:00"},
    {"started_at": "2026-10-18T10:02:00Z", "ended_at": "2026-10-18T10:01:00Z"},
    {"started_at": "invalid", "ended_at": "invalid"},
])
def test_summary_duration_missing_bounds_do_not_estimate_from_text(bad):
    known = {"started_at": "2026-10-18T10:00:00Z", "ended_at": "2026-10-18T10:02:00Z"}
    assert summary_source_duration([known, {**bad, "text": "a" * 10000}]) == {
        "source_seconds": 120, "source_duration_complete": False,
    }
