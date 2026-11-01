"""Ring disclosure requires the original deterministic copy identity, not imported claims."""

import asyncio
import hashlib
import io
import json
import wave
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.inference import Inference
from omi_local.service import public_source
from omi_local.settings import Settings

SOURCE_ID = "omi-ring:pendant:99:10"
SOURCE_UUID = "fb58c342-6eba-52aa-a447-4807587c8606"
DISCLOSURE = {"provenance_kind": "omi_ring", "timestamp_precision_seconds": 1,
              "device_copy_retained": True}


def row(**changes):
    return {"id": SOURCE_UUID, "device_id": "pendant", "source_kind": "manual_upload",
            "path": "/unused.wav",
            "source_id": SOURCE_ID, "state": "done", "text": "Review the launch on Friday.",
            "started_at": "2026-10-16T12:00:00+00:00", "ended_at": "2026-10-16T12:00:01+00:00",
            "source_metadata": "{}", **changes}


def stable_id(source_id):
    digest = bytearray(hashlib.sha256(source_id.encode()).digest()[:16])
    digest[6] = (digest[6] & 15) | 80
    digest[8] = (digest[8] & 63) | 128
    return str(UUID(bytes=bytes(digest)))


def test_verified_copy_identity_derives_disclosure_without_rewriting_metadata_or_timestamp_basis():
    original = row(source_metadata=json.dumps({"vendor_id": "omi", "part_index": 0}))
    result = public_source(original)
    assert result["source_metadata"] == {"vendor_id": "omi", "part_index": 0, **DISCLOSURE}
    assert result["timestamp_basis"] == "capture"
    assert original["source_metadata"] == '{"vendor_id": "omi", "part_index": 0}'
    assert public_source(row(id=SOURCE_UUID.upper()))["source_metadata"] == DISCLOSURE


@pytest.mark.parametrize("changes", [
    {"id": str(uuid4())}, {"id": "invalid"}, {"device_id": "another-pendant"},
    {"source_kind": "omi_api"}, {"source_kind": ""},
    {"source_id": "omi-ring:pendant:99"}, {"source_id": "other:pendant:99:10"},
    {"source_id": "omi-ring::99:10"}, {"source_id": None},
])
def test_prefix_or_imported_metadata_cannot_spoof_a_ring_copy(changes):
    result = public_source(row(source_metadata=json.dumps({"other": "preserved", **DISCLOSURE}),
                               **changes))
    assert result["source_metadata"] == {"other": "preserved"}


@pytest.mark.parametrize("ring,sequence", [
    ("0", "10"), ("-1", "10"), ("+99", "10"), ("099", "10"),
    ("99", "-1"), ("99", "010"), ("99", "10.0"), ("９９", "10"),
    (str(2**64), "10"), ("99", str(2**64)),
])
def test_malformed_unsigned_values_remain_generic_even_with_a_matching_hash(ring, sequence):
    source_id = f"omi-ring:pendant:{ring}:{sequence}"
    assert public_source(row(id=stable_id(source_id), source_id=source_id))["source_metadata"] == {}


def test_uint64_edges_and_colon_separated_device_identity_are_exact():
    source_id = f"omi-ring:aa:bb:{2**64 - 1}:0"
    assert public_source(row(id=stable_id(source_id), device_id="aa:bb", source_id=source_id))[
        "source_metadata"] == DISCLOSURE


def test_uploaded_copy_retries_and_preparation_keep_original_wire_contract(tmp_path):
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        stream.writeframes(b"\x01\x02" * 16000)
    with TestClient(create_app(Settings(
        data_dir=tmp_path, worker_enabled=False, background_processing_enabled=False,
        _env_file=None,
    ))) as client:
        params = {"device_id": "pendant", "started_at": "2026-10-16T12:00:00Z",
                  "source_kind": "manual_upload", "source_id": SOURCE_ID}
        for _ in range(2):
            response = client.put(f"/v1/audio/{SOURCE_UUID}", params=params, content=out.getvalue())
            assert response.status_code == 200, response.text
        store = client.app.state.memory.store
        store.complete(SOURCE_UUID, "Review the launch on Friday.")
        assert json.loads(store.get_audio(SOURCE_UUID)["source_metadata"]) == {}
        source = client.get(f"/v1/transcripts/{SOURCE_UUID}").json()
        assert source["source_metadata"] == DISCLOSURE and source["timestamp_basis"] == "capture"
        prep = client.post("/v1/conversation-prep/search", json={
            "kind": "topic", "subject": "Review",
        })
        assert prep.status_code == 200, prep.text
        assert prep.json()["sources"][0]["source_metadata"] == DISCLOSURE


def test_ring_identifiers_and_device_identity_are_not_added_to_model_prompts(tmp_path):
    sent = []

    def provider(request):
        sent.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": f"Review Friday. [{SOURCE_UUID}]"},
        }]})

    async def ask():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await Inference(Settings(data_dir=tmp_path, _env_file=None), client).chat(
                "When is the review?", [public_source(row())],
            )
    assert SOURCE_UUID in asyncio.run(ask())
    assert sent and all(SOURCE_ID not in payload and "pendant" not in payload for payload in sent)
