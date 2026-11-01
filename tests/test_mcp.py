"""Exercise the actual read-only bridge against archive HTTP routes and stdio framing."""

import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.mcp import MAX_MESSAGE_BYTES, Bridge, serve
from omi_local.settings import Settings


@pytest.fixture
def backend(tmp_path):
    with TestClient(create_app(Settings(
        data_dir=tmp_path, worker_enabled=False, _env_file=None,
    ))) as client:
        yield client


def rpc(method, params=None, key=1):
    return {"jsonrpc": "2.0", "id": key, "method": method, "params": params or {}}


def test_stdio_protocol_negotiation_notifications_and_read_only_catalog():
    with httpx.Client(base_url="http://localhost") as client:
        bridge = Bridge(client)
        assert bridge.handle(rpc("tools/list"))["error"]["code"] == -32002
        request = rpc("initialize", {"protocolVersion": "2025-11-25"})
        incoming = io.StringIO("\n".join([
            json.dumps(request),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(rpc("tools/list", key=2)),
            "invalid JSON", json.dumps(rpc("delete_memory", key=3)), "",
        ]))
        output = io.StringIO()
        serve(bridge, incoming, output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(replies) == 4
    assert replies[0]["result"]["protocolVersion"] == "2025-11-25"
    tools = replies[1]["result"]["tools"]
    assert len(tools) == 6
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    assert replies[2]["error"]["code"] == -32700
    assert replies[3]["error"]["code"] == -32601


def test_current_transcript_pages_profile_binding_and_deletion(backend):
    store = backend.app.state.memory.store
    key, at = str(uuid4()), datetime.now(UTC)
    with store.connect() as db:
        db.execute(
            "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text,"
            "audio_available,source_kind,segments) VALUES(?,?,?,?,?,?,'done',?,0,'omi_api',?)",
            (key, "omi-api", at.isoformat(), (at + timedelta(minutes=1)).isoformat(),
             "unused", "unused", "a" * 30 + "tail", '[{"text":"unbounded duplicate"}]'),
        )
    seen = []

    def route(request):
        seen.append(request)
        result = backend.request(
            request.method, str(request.url), content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(result.status_code, content=result.content)

    with httpx.Client(
        base_url="http://testserver", transport=httpx.MockTransport(route),
        headers={"X-Omi-Profile": "default"},
    ) as client:
        bridge = Bridge(client)
        bridge.handle(rpc("initialize"))
        first = bridge.handle(rpc("tools/call", {
            "name": "get_transcript", "arguments": {"source_id": key, "limit": 30},
        }))["result"]
        data = json.loads(first["content"][0]["text"])
        assert data["text"] == "a" * 30
        assert data["next_offset"] == 30 and data["truncated"]
        assert data["source_version"]
        assert "segments" not in data
        second = bridge.handle(rpc("tools/call", {
            "name": "get_transcript", "arguments": {"source_id": key, "offset": 30},
        }))["result"]
        assert json.loads(second["content"][0]["text"])["text"] == "tail"
        with store.connect() as db:
            db.execute("DELETE FROM audio WHERE id=?", (key,))
        removed = bridge.handle(rpc("tools/call", {
            "name": "get_transcript", "arguments": {"source_id": key},
        }))["result"]
        assert removed["isError"]
    assert all(item.headers["X-Omi-Profile"] == "default" for item in seen)
    assert all(item.method == "GET" for item in seen)


def test_invalid_arguments_never_make_network_calls():
    def unexpected(request):
        pytest.fail("Invalid arguments reached the backend")

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(unexpected)) as c:
        bridge = Bridge(c)
        bridge.handle(rpc("initialize"))
        for name, args in [
            ("get_transcript", {"source_id": "../settings"}),
            ("get_transcript", {"source_id": str(uuid4()), "profile": "other"}),
            ("search_recordings", {"question": "garden", "start_day": "2026-01-01"}),
            ("search_recordings", {"question": "garden", "limit": True}),
            ("get_daily_recap", {"day": "2026-02-30"}),
            ("delete_memory", {}),
        ]:
            response = bridge.handle(rpc("tools/call", {"name": name, "arguments": args}))
            assert response["error"]["code"] == -32602


def test_oversized_message_does_not_execute_its_tail():
    with httpx.Client(base_url="http://localhost") as client:
        bridge = Bridge(client)
        output = io.StringIO()
        serve(bridge, io.StringIO("x" * (MAX_MESSAGE_BYTES + 10) + "\n" +
                                json.dumps(rpc("ping")) + "\n"), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(replies) == 2
    assert replies[0]["error"]["code"] == -32600
    assert replies[1]["result"] == {}


def test_profile_scoping_and_cached_recap_never_generates(backend):
    other = backend.post("/v1/profiles", json={"name": "Other archive"}).json()["id"]
    seen = []

    def route(request):
        seen.append(request)
        result = backend.request(request.method, str(request.url), content=request.content,
                                 headers=dict(request.headers))
        return httpx.Response(result.status_code, content=result.content)

    with httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(route),
                      headers={"X-Omi-Profile": other}) as c:
        bridge = Bridge(c)
        bridge.handle(rpc("initialize"))
        result = bridge.handle(rpc("tools/call", {
            "name": "get_daily_recap", "arguments": {"day": "2026-11-01"},
        }))["result"]
        assert not result["isError"]
        assert json.loads(result["content"][0]["text"])["summary"] is None
        assert bridge.handle(rpc("tools/call", {
            "name": "search_people_projects", "arguments": {},
        }))["result"]["isError"] is False
    assert all(request.headers["X-Omi-Profile"] == other for request in seen)
    assert all(request.method == "GET" for request in seen)
    assert backend.get("/v1/chat/sessions", headers={"X-Omi-Profile": other}).json()["items"] == []
