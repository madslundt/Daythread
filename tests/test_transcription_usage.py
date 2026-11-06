import io
import json
import wave
from datetime import UTC, datetime
from email.parser import BytesParser
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
@pytest.mark.parametrize("seconds,end", [(0.02, 1.0), (1.68, 29.98)])
async def test_invalid_provider_timestamps_do_not_keep_recordings_retrying(
    tmp_path, seconds, end,
):
    store = Store(tmp_path)
    key = str(uuid4())
    original = wav(seconds)
    store.ingest(key, "omi", datetime.now(UTC), original, seconds)
    settings = Settings(
        data_dir=tmp_path, _env_file=None, stt_filter_speech=False,
        stt_enhancement_mode="off",
    )
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={
            "text": "Vi mødes i morgen.",
            "segments": [{"start": 0, "end": end, "text": "Vi mødes i morgen."}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(settings, client)
        memory = SimpleNamespace(store=store, settings=settings, inference=inference)
        assert await process_one(memory)
        assert not await process_one(memory)

    row = store.get_audio(key)
    assert row["state"] == "done"
    assert row["text"] == "Vi mødes i morgen."
    assert json.loads(row["segments"]) == []
    details = json.loads(row["transcription_details"])
    assert details["chunks"][0]["timestamp_status"] == "discarded_invalid"
    assert store.read_audio(key) == original
    assert len(requests) == 1


@pytest.mark.parametrize("segments", [
    None, {}, [None], [{}],
    [{"start": -1, "end": 1, "text": "Hej"}],
    [{"start": 1, "end": 1, "text": "Hej"}],
    [{"start": 10.1, "end": 10.2, "text": "Hej"}],
    [{"start": 0, "end": float("nan"), "text": "Hej"}],
    [{"start": 0, "end": float("inf"), "text": "Hej"}],
    [{"start": 0, "end": 1, "text": "Hej"},
     {"start": 1, "end": 30, "text": "igen"}],
])
def test_unusable_timing_keeps_text_without_publishing_partial_alignment(segments):
    result = Inference._transcription_result({"text": "Hej igen", "segments": segments}, wav())
    assert result == {"text": "Hej igen", "segments": [],
                      "timestamp_status": "discarded_invalid"}


def test_small_timestamp_rounding_overrun_still_produces_valid_alignment():
    result = Inference._transcription_result({
        "text": "Hej", "segments": [{"start": 9, "end": 10.2, "text": "Hej"}],
    }, wav())
    assert result["segments"] == [{"start": 9, "end": 10, "text": "Hej"}]


def test_explicit_character_anchors_trim_and_retain_only_exact_partial_spans():
    text = "  Møde møde ukendt  "
    result = Inference._transcription_result({
        "text": text,
        "segments": [
            {"start": .1, "end": .3, "start_offset": 2, "end_offset": 6,
             "text": "Møde", "kind": "word"},
            {"start": .4, "end": .6, "start_offset": 7, "end_offset": 11,
             "text": "møde", "kind": "word"},
            {"start": .7, "end": .8, "start_offset": 12, "end_offset": 18,
             "text": "wrong", "kind": "word"},
        ],
    }, wav())
    assert result["text"] == "Møde møde ukendt"
    assert result["segments"] == [
        {"start": .1, "end": .3, "text": "Møde", "start_offset": 0, "end_offset": 4,
         "kind": "word"},
        {"start": .4, "end": .6, "text": "møde", "start_offset": 5, "end_offset": 9,
         "kind": "word"},
    ]
    assert result["timestamp_status"] == "partial"


@pytest.mark.parametrize("segment", [
    {"start": .1, "end": .3, "start_offset": True, "end_offset": 4, "text": "Hej"},
    {"start": float("nan"), "end": .3, "start_offset": 0, "end_offset": 3, "text": "Hej"},
    {"start": .1, "end": .3, "start_offset": 0, "end_offset": 2, "text": "Hej"},
    {"start": .1, "end": .3, "start_offset": 8, "end_offset": 11, "text": "Hej"},
])
def test_invalid_explicit_anchors_never_fall_back_to_segment_only_timing(segment):
    result = Inference._transcription_result({"text": "Hej igen", "segments": [segment]}, wav())
    assert result["text"] == "Hej igen"
    assert result["segments"] == []
    assert result["timestamp_status"] == "discarded_invalid"


@pytest.mark.anyio
async def test_worker_rebases_explicit_anchors_after_filtered_gaps_and_newline_join(
    tmp_path, monkeypatch,
):
    from omi_local import transcription

    audio_a, audio_b = wav(2), wav(1)
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda _data: activity())
    monkeypatch.setattr(transcription, "filter_transcription_audio", lambda *_args, **_kwargs: {
        "spans": [(0.5, audio_a), (3.5, audio_b)], "status": "filtered",
        "version": 1, "original_seconds": 5, "retained_seconds": 3,
        "detected_speech_seconds": 3,
    })
    store = Store(tmp_path)
    key = str(uuid4())
    original = wav(5)
    store.ingest(key, "omi", datetime.now(UTC), original, 5)
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_filter_speech=True,
                        stt_segment_timestamps=True, stt_enhancement_mode="off")
    replies = [
        {"text": "  navn navn  ", "segments": [
            {"start": .1, "end": .3, "start_offset": 2, "end_offset": 6,
             "text": "navn", "kind": "word"},
            {"start": .4, "end": .6, "start_offset": 7, "end_offset": 11,
             "text": "navn", "kind": "word"},
        ]},
        {"text": " navn", "segments": [
            {"start": .2, "end": .5, "start_offset": 1, "end_offset": 5,
             "text": "navn", "kind": "word"},
        ]},
    ]

    def provider(_request):
        return httpx.Response(200, json=replies.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(settings, client)
        memory = SimpleNamespace(store=store, settings=settings, inference=inference)
        assert await process_one(memory)
    row = store.get_audio(key)
    assert row["state"] == "done"
    assert row["text"] == "navn navn\nnavn"
    segments = json.loads(row["segments"])
    assert [(s["start"], s["start_offset"], s["end_offset"], s["text"])
            for s in segments] == [
        (.6, 0, 4, "navn"), (.9, 5, 9, "navn"), (3.7, 10, 14, "navn"),
    ]
    assert all(row["text"][s["start_offset"]:s["end_offset"]] == s["text"] for s in segments)
    assert store.read_audio(key) == original


@pytest.mark.parametrize("text", [None, 123, "x" * 20001])
def test_invalid_transcript_text_is_still_rejected(text):
    with pytest.raises(InferenceError, match="Invalid STT response"):
        Inference._transcription_result({"text": text, "segments": []}, wav())


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
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enhancement_mode="off",
                        stt_filter_speech=True, stt_chunk_seconds=3,
                        stt_boundary_search_seconds=0)
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
    assert [s["start"] for s in segments] == pytest.approx([.1, 6.1])
    assert [s["end"] for s in segments] == pytest.approx([.5, 6.5])
    details = json.loads(row["transcription_details"])
    assert details["filtering"]["retained_seconds"] == 6
    totals = ledger.report()["totals"]
    assert totals["captured_minutes"] == pytest.approx(10 / 60)
    assert totals["retained_minutes"] == pytest.approx(6 / 60)
    assert totals["submitted_minutes"] == pytest.approx(6 / 60)
    assert totals["transcription_credits"] == 2.5
    assert totals["summary_credits"] == 5
    assert totals["unknown_credit_attempts"] == 0
    with store.connect() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM usage_events")]
    assert all(e["recording_id"] == recording_id for e in events if e["kind"] == "stt")
    assert all("recording_id" not in e for e in events if e["kind"] == "llm")


@pytest.mark.anyio
@pytest.mark.parametrize("digital_silence", [False, True])
async def test_default_digital_silence_and_opt_in_non_speech_skip_provider(
    tmp_path, monkeypatch, digital_silence,
):
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda data: activity(False))
    store = Store(tmp_path)
    ledger = UsageLedger(store)
    original = wav(sample=b"\0\0" if digital_silence else b"\x08\0")
    key = str(uuid4())
    store.ingest(key, "omi", datetime.now(UTC), original, 10)
    filtering = {} if digital_silence else {"stt_filter_speech": True}
    settings = Settings(data_dir=tmp_path, _env_file=None, **filtering)

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
@pytest.mark.parametrize("detected_speech", [False, True])
async def test_default_processing_keeps_full_nonzero_audio_despite_vad(
    tmp_path, monkeypatch, detected_speech,
):
    monkeypatch.setattr("omi_local.worker.analyze_voice", lambda _: activity(detected_speech))
    store = Store(tmp_path)
    original = wav()
    key = str(uuid4())
    store.ingest(key, "omi", datetime.now(UTC), original, 10)
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_max_gain_db=0,
                        stt_enhancement_mode="off")
    requests = []

    def provider(request):
        message = BytesParser().parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
        )
        submitted_audio = next(
            part.get_payload(decode=True) for part in message.get_payload()
            if part.get_param("name", header="content-disposition") == "file"
        )
        requests.append(submitted_audio)
        return httpx.Response(200, json={"text": "Quiet words are retained."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        inference = Inference(settings, client)
        memory = SimpleNamespace(store=store, settings=settings, inference=inference)
        assert await process_one(memory)
    assert requests == [original]
    row = store.get_audio(key)
    assert row["text"] == "Quiet words are retained."
    assert row["transcription_outcome"] == "text"
    assert store.read_audio(key) == original
    filtering = json.loads(row["transcription_details"])["filtering"]
    assert filtering["status"] == "disabled"
    assert filtering["original_seconds"] == filtering["retained_seconds"] == 10


@pytest.mark.anyio
async def test_failed_detection_retains_full_audio_and_unreported_credits_stay_unknown(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, stt_enhancement_mode="off",
                        stt_filter_speech=True)
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
