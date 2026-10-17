import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from omi_local.pocket_api import ENDPOINT, PocketAPI
from omi_local.vendor_imports import VendorError, VendorImportRequest

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
KEY = "pk_private_test_key"


def recording(key="pocket-recording"):
    return {
        "id": key,
        "recording_at": AT.isoformat(),
        "created_at": "2026-10-16T18:00:00Z",
        "duration": 300,
        "state": "completed",
        "transcript": {"segments": [
            {"start": 10, "end": 12, "text": "The kitchen arrives Friday",
             "speaker": "SPEAKER_00", "speakerName": "Ada"},
            {"start": 13, "end": 15, "text": "I will be home", "speaker": "SPEAKER_01"},
        ]},
        "summarizations": {"summary": "This summary must never be evidence"},
    }


def listing(items, *, page=1, more=False, total_pages=None):
    return {
        "success": True,
        "data": items,
        "pagination": {"page": page, "limit": len(items), "has_more": more,
                       "total_pages": total_pages if total_pages is not None else page},
    }


def fetch(handler, request=None, *, write=False):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = PocketAPI(
                SimpleNamespace(pocket_api_key=SecretStr(KEY)), client, object(),
            )
            # The dummy store has no connect(): validation must finish before any write.
            method = adapter.import_page if write else adapter.fetch_page
            return await method(VendorImportRequest(**(request or {})))
    return asyncio.run(run())


def test_documented_list_and_detail_contract_and_capture_provenance():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.url.host == "public.heypocketai.com"
        if len(calls) == 1:
            assert str(request.url).split("?")[0] == ENDPOINT
            assert dict(request.url.params) == {"page": "1", "limit": "25"}
            return httpx.Response(200, json=listing([recording() | {"transcript": None}]))
        assert request.url.path.endswith("/pocket-recording")
        assert dict(request.url.params) == {
            "include_transcript": "true", "include_summarizations": "false",
        }
        return httpx.Response(200, json={"success": True, "data": recording()})

    page = fetch(handler)
    assert len(calls) == 2
    source = page.recordings[0]
    assert source.source_id == "pocket-recording"
    assert source.started_at == AT
    assert source.ended_at == AT + timedelta(seconds=300)
    assert source.text == "Ada: The kitchen arrives Friday\nSpeaker SPEAKER_01: I will be home"
    assert source.segments[0].speaker_id == "SPEAKER_00"
    assert not page.has_more and page.next_offset == 1


def test_page_offset_round_trip_including_unaligned_offsets():
    def handler(request):
        assert dict(request.url.params) == {"page": "2", "limit": "2"}
        return httpx.Response(200, json=listing(
            [recording("two"), recording("three")], page=2, more=True, total_pages=3,
        ))

    page = fetch(handler, {"limit": 2, "offset": 3})
    assert [item.source_id for item in page.recordings] == ["three"]
    assert page.has_more and page.next_offset == 4


def test_short_remote_page_advances_to_following_remote_page():
    page = fetch(lambda _: httpx.Response(200, json=listing(
        [recording()], more=True, total_pages=2,
    )), {"limit": 2})
    assert page.has_more and page.next_offset == 2


def test_timezone_filters_cover_full_utc_days_and_apply_exact_bounds_locally():
    def handler(request):
        assert request.url.params["start_date"] == "2026-10-15"
        assert request.url.params["end_date"] == "2026-10-15"
        return httpx.Response(200, json=listing([
            recording("early") | {"recording_at": "2026-10-15T10:59:59Z"},
            recording("start") | {"recording_at": "2026-10-15T11:00:00Z"},
            recording("inside"),
            recording("end") | {"recording_at": "2026-10-15T13:00:00Z"},
        ]))

    page = fetch(handler, {
        "start_date": "2026-10-15T13:00:00+02:00",
        "end_date": "2026-10-15T15:00:00+02:00",
    })
    assert [item.source_id if item else None for item in page.recordings] == [
        None, "start", "inside", None,
    ]
    assert page.next_offset == 4


@pytest.mark.parametrize("changes", [
    {"recording_at": None},
    {"duration": None},
    {"duration": 0},
    {"transcript": None},
    {"transcript": {"segments": []}},
    {"transcript": {"segments": [{"start": 0, "end": 1, "text": "  "}]}},
])
def test_unfinished_or_empty_sources_are_skipped(changes):
    calls = []

    def handler(request):
        calls.append(request)
        item = recording() | changes
        payload = listing([item]) if len(calls) == 1 else {"success": True, "data": item}
        return httpx.Response(200, json=payload)

    assert fetch(handler).recordings == [None]


def test_original_text_fallback_and_chronological_segments():
    item = recording() | {"transcript": {"segments": [
        {"start": 5, "end": 6, "originalText": "second"},
        {"start": 1, "end": 2, "text": "edited first", "originalText": "first"},
    ]}}
    source = fetch(lambda _: httpx.Response(200, json=listing([item]))).recordings[0]
    assert source.text == "edited first\nsecond"


def test_recording_ids_are_escaped_as_single_detail_path_components():
    calls = []
    key = "untrusted/segment?query#fragment"

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=listing([recording(key) | {"transcript": None}]))
        assert b"untrusted%2Fsegment%3Fquery%23fragment?" in request.url.raw_path
        assert set(request.url.params) == {"include_transcript", "include_summarizations"}
        return httpx.Response(200, json={"success": True, "data": recording(key)})

    assert fetch(handler).recordings[0].source_id == key


@pytest.mark.parametrize("changes", [
    {"recording_at": "2026-10-15T12:00:00"},
    {"recording_at": "2099-01-01T00:00:00Z"},
    {"duration": -1},
    {"duration": 7 * 86400 + 1},
    {"id": ""},
    {"transcript": {"segments": [{"start": 0, "end": 400, "text": "too long"}]}},
    {"transcript": {"segments": [{"start": 2, "end": 1, "text": "reversed"}]}},
    {"transcript": {"segments": [{"start": 0, "end": 1, "text": 123}]}},
])
def test_invalid_full_page_is_rejected_before_any_archive_write(changes):
    payload = listing([recording("valid-first"), recording("invalid") | changes])
    with pytest.raises(VendorError, match="nothing was imported"):
        fetch(lambda _: httpx.Response(200, json=payload), write=True)


@pytest.mark.parametrize("payload", [
    [], {"success": False, "error": KEY, "data": []},
    {"success": True, "data": {}},
    listing([]) | {"pagination": {}},
    listing([recording()], more=True, total_pages=1),
    listing([]) | {"pagination": {"page": 2, "total_pages": 2, "has_more": False}},
])
def test_invalid_envelopes_or_nonadvancing_pages_never_write_or_leak(payload):
    with pytest.raises(VendorError, match="nothing was imported") as exc:
        fetch(lambda _: httpx.Response(200, json=payload), write=True)
    assert KEY not in str(exc.value)


def test_detail_id_must_match_list_entry():
    def handler(request):
        payload = (listing([recording() | {"transcript": None}])
                   if request.url.path.endswith("/recordings")
                   else {"success": True, "data": recording("different-id")})
        return httpx.Response(200, json=payload)

    with pytest.raises(VendorError, match="nothing was imported"):
        fetch(handler, write=True)


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_remote_failures_are_secret_safe_and_redirects_are_not_followed(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=KEY, headers={"Location": "https://other.example"})

    with pytest.raises(VendorError) as exc:
        fetch(handler, write=True)
    assert len(calls) == 1 and KEY not in str(exc.value)
    assert exc.value.status == (503 if status == 429 else 502)


def test_network_errors_and_invalid_json_do_not_expose_diagnostics():
    def handler(request):
        raise httpx.ConnectError(KEY, request=request)

    with pytest.raises(VendorError, match="Pocket API is unavailable") as exc:
        fetch(handler, write=True)
    assert KEY not in str(exc.value)
    with pytest.raises(VendorError, match="nothing was imported"):
        fetch(lambda _: httpx.Response(200, text=KEY), write=True)


def test_cursor_rejected_instead_of_silently_ignored():
    with pytest.raises(VendorError, match="nothing was imported"):
        fetch(
            lambda _: pytest.fail("No network request expected"),
            {"cursor": "opaque"}, write=True,
        )
