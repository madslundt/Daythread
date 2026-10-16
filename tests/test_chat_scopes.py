import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.inference import Inference
from omi_local.retrieval import date_scope
from omi_local.settings import Settings
from omi_local.store import Store

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


class ScopeInference:
    settings = None

    async def summarize(self, sources, purpose):
        return f"Project insight [{sources[0]['id']}]"

    async def chat(self, question, sources, history=None):
        return f"Recorded answer [{sources[0]['id']}]"


@pytest.fixture
def config(tmp_path):
    return Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as client:
        client.app.state.memory.inference = ScopeInference()
        yield client


def insert(client, at=AT, text="Kitchen discussion", duration=1):
    key = uuid4()
    store = client.app.state.memory.store
    store.ingest(key, "omi", at, b"test audio", duration)
    store.complete(str(key), text)
    return str(key)


def source_ids(response):
    assert response.status_code == 200, response.text
    return {source["id"] for source in response.json()["sources"]}


def meeting(client):
    key = str(uuid4())
    for event_id, at in ((key, AT), (str(uuid4()), AT + timedelta(minutes=5))):
        response = client.post(
            "/v1/events",
            json={
                "event_id": event_id, "device_id": "omi", "occurred_at": stamp(at),
                "action": "meeting_toggle",
            },
        )
        assert response.status_code == 200
    return key


def test_date_scope_survives_followups_and_restart(client, config):
    selected = insert(client)
    insert(client, AT - timedelta(days=1))
    first = client.post("/v1/chat", json={"question": "kitchen", "day": "2026-10-15"})
    assert source_ids(first) == {selected}
    session_id = first.json()["session_id"]
    for question in ("What did they decide?", "Kitchen on 2026-10-14"):
        followup = client.post("/v1/chat", json={"question": question, "session_id": session_id})
        assert source_ids(followup) == {selected}
        assert followup.json()["day"] == "2026-10-15"
    with TestClient(create_app(config)) as restarted:
        restarted.app.state.memory.inference = ScopeInference()
        detail = restarted.get(f"/v1/chat/sessions/{session_id}").json()
        assert detail["day"] == "2026-10-15" and detail["meeting_id"] is None
        assert restarted.get("/v1/chat/sessions").json()["items"][0]["day"] == "2026-10-15"
        response = restarted.post(
            "/v1/chat", json={"question": "kitchen", "session_id": session_id}
        )
        assert source_ids(response) == {selected}


def test_meeting_scope_uses_padded_chunk_overlap_for_search_and_followup(client):
    insert(client, AT - timedelta(seconds=61))  # Ends exactly at the padded start.
    before = insert(client, AT - timedelta(seconds=60))
    during = insert(client, AT + timedelta(minutes=1))
    after = insert(client, AT + timedelta(minutes=5, seconds=59))
    insert(client, AT + timedelta(minutes=6))  # Starts exactly at the padded end.
    key = meeting(client)
    first = client.post("/v1/chat", json={"question": "kitchen", "meeting_id": key})
    assert source_ids(first) == {before, during, after}
    assert first.json()["meeting_id"] == key and first.json()["day"] is None
    session_id = first.json()["session_id"]
    followup = client.post(
        "/v1/chat", json={"question": "What did they decide?", "session_id": session_id}
    )
    assert source_ids(followup) == {before, during, after}
    assert followup.json()["scope"] == {
        "start": stamp(AT - timedelta(minutes=1)),
        "end": stamp(AT + timedelta(minutes=6)),
    }
    detail = client.get(f"/v1/chat/sessions/{session_id}").json()
    assert detail["meeting_id"] == key


def test_scope_validation_and_conflicts_do_not_create_invalid_sessions(client):
    insert(client)
    for payload, status in (
        ({"day": "2026-02-30"}, 422),
        ({"day": "2020-01-01"}, 404),
        ({"meeting_id": str(uuid4())}, 404),
        ({"meeting_id": "invalid"}, 422),
        ({"day": "2026-10-15", "meeting_id": str(uuid4())}, 422),
    ):
        response = client.post("/v1/chat", json={"question": "kitchen", **payload})
        assert response.status_code == status
    assert client.get("/v1/chat/sessions").json()["items"] == []
    first = client.post("/v1/chat", json={"question": "kitchen", "day": "2026-10-15"}).json()
    for scope in ({"day": "2026-10-14"}, {"meeting_id": str(uuid4())}, {"day": None}):
        response = client.post(
            "/v1/chat", json={"question": "kitchen", "session_id": first["session_id"], **scope}
        )
        assert response.status_code == 409
    detail = client.get(f"/v1/chat/sessions/{first['session_id']}").json()
    assert len(detail["messages"]) == 2


def test_all_diaries_retrieves_recaps_and_transcripts_across_days(client):
    first = insert(client)
    second = insert(client, AT - timedelta(days=1))
    for day in ("2026-10-14", "2026-10-15"):
        assert client.post(f"/v1/recaps/{day}").status_code == 200
    broad = client.post("/v1/chat", json={"question": "Summarize all diaries"})
    assert source_ids(broad) == {first, second}
    assert broad.json()["day"] is None and broad.json()["meeting_id"] is None
    assert broad.json()["scope"] is None
    # The recap contains this topic even though neither raw transcript does.
    query = client.post("/v1/chat", json={"question": "Project insight"})
    assert source_ids(query) == {first, second}
    assert {source["diary_summaries"][0]["day"] for source in query.json()["sources"]} == {
        "2026-10-14", "2026-10-15",
    }
    scoped = client.post(
        "/v1/chat", json={"question": "Project insight", "day": "2026-10-15"}
    )
    assert source_ids(scoped) == {first}
    transcript_topic = client.post(
        "/v1/chat", json={"question": "Kitchen discussion", "day": "2026-10-15"}
    )
    assert transcript_topic.json()["sources"][0]["diary_summaries"][0]["summary"].startswith(
        "Project insight"
    )
    key = meeting(client)
    excluded = client.post("/v1/chat", json={"question": "Project insight", "meeting_id": key})
    assert source_ids(excluded) == set()  # A daily recap cannot supply other meeting context.



@pytest.mark.parametrize(
    "question",
    ["Summarize all diaries", "What happened across all days?", "Opsummer alle dagbøger",
     "Hvad skete der på tværs af alle dage?"],
)
def test_broad_archive_followup_includes_other_days_without_recaps(client, question):
    insert(client)
    insert(client, AT - timedelta(days=1), "Garden planning")
    first = client.post("/v1/chat", json={"question": "kitchen"}).json()
    assert len(first["sources"]) == 1
    followup = client.post(
        "/v1/chat", json={"question": question, "session_id": first["session_id"]}
    )
    assert len(source_ids(followup)) == 2


def test_archive_recall_clears_an_inferred_prior_date(client, monkeypatch):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT)
    today = insert(client)
    yesterday = insert(client, AT - timedelta(days=1))
    first = client.post("/v1/chat", json={"question": "kitchen yesterday"})
    assert source_ids(first) == {yesterday}
    followup = client.post(
        "/v1/chat",
        json={"question": "Summarize all diaries", "session_id": first.json()["session_id"]},
    )
    assert source_ids(followup) == {today, yesterday}
    assert followup.json()["scope"] is None


@pytest.mark.parametrize("kind", ["day", "meeting"])
def test_archive_recall_preserves_saved_scope(client, kind):
    selected = insert(client)
    insert(client, AT - timedelta(days=1))
    scope = {"day": "2026-10-15"} if kind == "day" else {"meeting_id": meeting(client)}
    first = client.post("/v1/chat", json={"question": "kitchen", **scope}).json()
    followup = client.post(
        "/v1/chat",
        json={"question": "Summarize all diaries", "session_id": first["session_id"]},
    )
    assert source_ids(followup) == {selected}
    assert followup.json()["scope"] is not None


@pytest.mark.parametrize("kind", ["day", "meeting"])
def test_broad_scoped_prompts_use_selected_evidence(client, kind):
    selected = insert(client)
    insert(client, AT - timedelta(days=1))
    scope = {"day": "2026-10-15"} if kind == "day" else {"meeting_id": meeting(client)}
    response = client.post("/v1/chat", json={"question": f"Summarize this {kind}", **scope})
    assert source_ids(response) == {selected}

def test_existing_database_sessions_migrate_as_all_days(config):
    key = str(uuid4())
    with sqlite3.connect(config.data_dir / "omi.sqlite3") as db:
        db.execute(
            "CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute("INSERT INTO chat_sessions VALUES(?,?,?,?)", (key, "Old chat", "a", "a"))
    store = Store(config.data_dir)
    assert store.chat_session(key)["day"] is None
    assert store.chat_session(key)["meeting_id"] is None
    assert store.chat_session(key)["start_day"] is None
    assert store.chat_session(key)["end_day"] is None
    assert store.ensure_session(None, "New chat")["day"] is None


@pytest.mark.anyio
async def test_inference_includes_recap_text_as_recorded_evidence(config):
    key = str(uuid4())

    def handler(request):
        content = json.loads(request.content)["messages"][1]["content"]
        evidence = json.loads(json.loads(content)["evidence"])
        assert evidence[0]["diary_summaries"][0]["summary"] == "Project insight"
        return httpx.Response(200, json={"choices": [{"message": {"content": f"Answer [{key}]"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await Inference(config, http).chat(
            "What happened?",
            [{"id": key, "started_at": stamp(AT), "text": "Kitchen discussion",
              "diary_summaries": [{"day": "2026-10-15", "summary": "Project insight"}]}],
        )
        assert key in result


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "scope",
    [
        {"start_day": "2026-10-14"},
        {"end_day": "2026-10-15"},
        {"start_day": "2026-10-14", "end_day": None},
        {"start_day": "2026-02-30", "end_day": "2026-10-15"},
        {"start_day": "2026-10-16", "end_day": "2026-10-15"},
        {"start_day": "2026-10-14", "end_day": "2026-10-15", "day": "2026-10-14"},
        {"start_day": "2026-10-14", "end_day": "2026-10-15", "meeting_id": str(uuid4())},
    ],
)
def test_date_range_rejects_missing_reversed_invalid_and_mixed_scopes(client, scope):
    response = client.post("/v1/chat", json={"question": "kitchen", **scope})
    assert response.status_code == 422
    assert client.get("/v1/chat/sessions").json()["items"] == []


def test_inclusive_date_range_uses_local_dst_boundaries_and_survives_restart(client, config):
    assert client.patch("/v1/settings", json={"timezone": "Europe/Copenhagen"}).status_code == 200
    start = datetime(2026, 10, 23, 22, tzinfo=UTC)
    end = datetime(2026, 10, 26, 23, tzinfo=UTC)
    insert(client, start - timedelta(seconds=1))
    first = insert(client, start)
    last = insert(client, end - timedelta(seconds=1))
    insert(client, end)
    request = {"question": "kitchen on 2020-01-01",
               "start_day": "2026-10-24", "end_day": "2026-10-26"}
    response = client.post("/v1/chat", json=request)
    assert source_ids(response) == {first, last}
    assert response.json()["scope"] == {"start": stamp(start), "end": stamp(end)}
    assert end - start == timedelta(hours=73)
    session_id = response.json()["session_id"]
    with TestClient(create_app(config)) as restarted:
        restarted.app.state.memory.inference = ScopeInference()
        detail = restarted.get(f"/v1/chat/sessions/{session_id}").json()
        listed = restarted.get("/v1/chat/sessions").json()["items"][0]
        for session in (detail, listed):
            assert session["start_day"] == "2026-10-24"
            assert session["end_day"] == "2026-10-26"
            assert session["day"] is None and session["meeting_id"] is None
        followup = restarted.post(
            "/v1/chat", json={"question": "Summarize all diaries", "session_id": session_id}
        )
        assert source_ids(followup) == {first, last}
        assert followup.json()["start_day"] == "2026-10-24"
        assert followup.json()["end_day"] == "2026-10-26"
        for scope in (
            {"start_day": "2026-10-24", "end_day": "2026-10-27"},
            {"start_day": None, "end_day": None},
            {"day": "2026-10-24"},
            {"meeting_id": str(uuid4())},
        ):
            conflict = restarted.post(
                "/v1/chat", json={"question": "kitchen", "session_id": session_id, **scope}
            )
            assert conflict.status_code == 409
        repeated = restarted.post("/v1/chat", json={**request, "session_id": session_id})
        assert source_ids(repeated) == {first, last}


def test_date_range_includes_only_relevant_recaps_and_accepts_empty_same_day(client):
    first = insert(client, AT - timedelta(days=1))
    last = insert(client)
    insert(client, AT - timedelta(days=2))
    for day in ("2026-10-13", "2026-10-14", "2026-10-15"):
        assert client.post(f"/v1/recaps/{day}").status_code == 200
    result = client.post(
        "/v1/chat",
        json={"question": "Project insight", "start_day": "2026-10-14", "end_day": "2026-10-15"},
    )
    assert source_ids(result) == {first, last}
    assert {source["diary_summaries"][0]["day"] for source in result.json()["sources"]} == {
        "2026-10-14", "2026-10-15",
    }
    empty = client.post(
        "/v1/chat",
        json={"question": "Summarize", "start_day": "2020-01-01", "end_day": "2020-01-01"},
    )
    assert source_ids(empty) == set()
    assert "No matching recorded evidence" in empty.json()["answer"]
    assert empty.json()["start_day"] == empty.json()["end_day"] == "2020-01-01"


@pytest.mark.parametrize(
    "question,start,end",
    [
        ("this week", "2026-10-12", "2026-10-18"),
        ("last week", "2026-10-05", "2026-10-11"),
        ("this month", "2026-10-01", "2026-10-31"),
        ("last month", "2026-09-01", "2026-09-30"),
        ("this year", "2026-01-01", "2026-12-31"),
        ("last year", "2025-01-01", "2025-12-31"),
        ("denne uge", "2026-10-12", "2026-10-18"),
        ("sidste uge", "2026-10-05", "2026-10-11"),
        ("denne måned", "2026-10-01", "2026-10-31"),
        ("sidste måned", "2026-09-01", "2026-09-30"),
        ("dette år", "2026-01-01", "2026-12-31"),
        ("sidste år", "2025-01-01", "2025-12-31"),
    ],
)
def test_natural_calendar_presets_cover_full_periods(client, monkeypatch, question, start, end):
    monkeypatch.setattr("omi_local.service.utc_now", lambda: AT)
    assert date_scope(question, AT.date()) == (date.fromisoformat(start), date.fromisoformat(end))
    # A broad calendar request retrieves the end of its entire period, even after today's date.
    final_day = datetime.fromisoformat(end).replace(hour=12, tzinfo=UTC)
    key = insert(client, final_day)
    response = client.post("/v1/chat", json={"question": f"Summarize {question}"})
    assert source_ids(response) == {key}
    assert response.json()["scope"] == {
        "start": f"{start}T00:00:00.000000+00:00",
        "end": stamp(final_day.replace(hour=0) + timedelta(days=1)),
    }


def test_calendar_month_presets_cross_year_and_leap_month_boundaries():
    assert date_scope("last month", date(2026, 1, 2)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert date_scope("this month", date(2024, 2, 3)) == (date(2024, 2, 1), date(2024, 2, 29))
    assert date_scope("last month", date(2024, 3, 2)) == (date(2024, 2, 1), date(2024, 2, 29))
