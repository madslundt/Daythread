import io
import json
import wave
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.usage import UsageLedger
from omi_local.worker import process_one


@pytest.fixture
def anyio_backend():
    return "asyncio"


def wav(seconds=10, sample=b"\x08\0"):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(sample * round(seconds * 16000))
    return output.getvalue()


def activity(speech=True):
    return {
        "status": "observed", "method": "webrtcvad",
        "speech": [[1, 2], [7, 8]] if speech else [],
        "non_speech": [[0, 1], [2, 7], [8, 10]] if speech else [[0, 10]],
    }


def billing(credits):
    return {"X-Daythread-Credits-Used": str(credits), "X-Daythread-Billing": "credits",
            "X-Daythread-Operation-Id": str(uuid4())}


@pytest.mark.anyio
async def test_worker_filters_requests_preserves_original_timing_and_measures_charges(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda data: activity())
    store = Store(tmp_path)
    ledger = UsageLedger(store)
    original = wav()
    recording_id = str(uuid4())
    store.ingest(recording_id, "omi", datetime.now(UTC), original, 10)
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enhancement_mode="off")
    requests = []

    def provider(request):
        requests.append(request)
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(200, headers=billing("1.25"), json={
                "text": "A short phrase", "segments": [{"start": .1, "end": .5, "text": "phrase"}],
            })
        return httpx.Response(200, headers=billing(5), json={
            "choices": [{"message": {"content": "Summary"}}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        await process_one(SimpleNamespace(store=store, settings=settings, inference=inference))
        await inference.for_task("summary").generate("Summarize", "Evidence")
    assert len(requests) == 3
    assert store.read_audio(recording_id) == original
    row = store.get_audio(recording_id)
    assert row["state"] == "done"
    segments = json.loads(row["segments"])
    assert [s["start"] for s in segments] == pytest.approx([.8, 6.8])
    assert [s["end"] for s in segments] == pytest.approx([1.2, 7.2])
    details = json.loads(row["transcription_details"])
    assert details["filtering"]["retained_seconds"] == pytest.approx(3.2)
    totals = ledger.report()["totals"]
    assert totals["captured_minutes"] == pytest.approx(10 / 60)
    assert totals["retained_minutes"] == pytest.approx(3.2 / 60)
    assert totals["submitted_minutes"] == pytest.approx(3.2 / 60)
    assert totals["transcription_credits"] == 2.5
    assert totals["summary_credits"] == 5
    assert totals["unknown_credit_attempts"] == 0
    with store.connect() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM usage_events")]
    assert all(e["recording_id"] == recording_id for e in events if e["kind"] == "stt")
    assert all("recording_id" not in e for e in events if e["kind"] == "llm")


@pytest.mark.anyio
@pytest.mark.parametrize("digital_silence", [False, True])
async def test_non_speech_skips_provider_but_keeps_recording_and_usage(
    tmp_path, monkeypatch, digital_silence,
):
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda data: activity(False))
    store = Store(tmp_path)
    ledger = UsageLedger(store)
    original = wav(sample=b"\0\0" if digital_silence else b"\x08\0")
    key = str(uuid4())
    store.ingest(key, "omi", datetime.now(UTC), original, 10)
    settings = Settings(data_dir=tmp_path, _env_file=None)

    def unexpected(request):
        pytest.fail("Non-speech must not reach a transcription or enhancement provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        await process_one(SimpleNamespace(store=store, settings=settings, inference=inference))
    assert store.read_audio(key) == original
    assert store.get_audio(key)["transcription_outcome"] == "silence_gate"
    report = ledger.report()
    assert report["totals"]["captured_minutes"] == pytest.approx(10 / 60)
    assert report["totals"]["retained_minutes"] == report["totals"]["submitted_minutes"] == 0
    assert report["totals"]["transcription_credits"] == 0
    assert report["totals"]["filtered_percent"] == pytest.approx(100)
    assert report["credits_complete"]


@pytest.mark.anyio
async def test_failed_detection_retains_full_audio_and_unreported_credits_stay_unknown(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enhancement_mode="off")
    ledger = UsageLedger(Store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"text": "Speech"}),
    )) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        result = await inference.transcribe_result(wav(), voice_activity={"status": "unknown"})
    assert result["processing"]["filtering"]["status"] == "unavailable"
    report = ledger.report()
    assert report["totals"]["submitted_minutes"] == pytest.approx(10 / 60)
    assert report["totals"]["unknown_credit_attempts"] == 1
    assert not report["credits_complete"]


@pytest.mark.anyio
async def test_timeout_attempt_survives_restart_without_inventing_a_free_request(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    ledger = UsageLedger(Store(tmp_path))

    def timeout(request):
        raise httpx.ReadTimeout("private provider information", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        with pytest.raises(httpx.ReadTimeout):
            await inference.generate("Task", "Evidence")
    report = UsageLedger(Store(tmp_path)).report()
    assert report["totals"]["unknown_credit_attempts"] == 1
    assert report["totals"]["summary_credits"] == 0
    assert not report["credits_complete"]


@pytest.mark.anyio
async def test_malformed_output_still_records_already_settled_credits(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    ledger = UsageLedger(Store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers=billing("5.125"), json={}),
    )) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        with pytest.raises(InferenceError):
            await inference.generate("Task", "Evidence")
    report = ledger.report()
    assert report["credits_complete"]
    assert report["totals"]["summary_credits"] == 5.125


@pytest.mark.anyio
async def test_streaming_json_gateway_records_actual_chat_charge(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    ledger = UsageLedger(Store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers=billing(5), json={"choices": [
            {"message": {"content": "Answer"}, "finish_reason": "stop"},
        ]},
    ))) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        deltas = []

        async def on_delta(text):
            deltas.append(text)

        assert await inference.generate_stream("Task", "Evidence", on_delta) == "Answer"
    assert deltas == ["Answer"]
    report = ledger.report()
    assert report["totals"]["chat_credits"] == 5
    assert report["totals"]["chat_requests"] == 1
    assert report["totals"]["chat_unknown_token_requests"] == 1
    assert report["totals"]["other_credits"] == 0
    assert report["totals"]["summary_credits"] == 0


@pytest.mark.anyio
async def test_usage_persistence_failure_prevents_provider_call(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    calls = []

    def cannot_record(event):
        raise OSError("Usage archive unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: calls.append(request),
    )) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = cannot_record
        with pytest.raises(OSError):
            await inference.generate("Task", "Evidence")
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("reported", ["1E+1000000", "NaN", "Infinity", "-1", "0.0001"])
async def test_invalid_billing_headers_do_not_retry_successful_paid_work(tmp_path, reported):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    ledger = UsageLedger(Store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers=billing(reported), json={"choices": [{"message": {"content": "Summary"}}]},
    ))) as client:
        inference = Inference(settings, client)
        inference.usage_recorder = ledger.record
        assert await inference.generate("Task", "Evidence") == "Summary"
    assert ledger.report()["totals"]["unknown_credit_attempts"] == 1
