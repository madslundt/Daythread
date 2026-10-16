"""Broad recall must select bounded records before loading transcript bodies."""

import tracemalloc
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omi_local.domain import stamp
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["archive", "day", "range"])
async def test_broad_search_does_not_materialize_the_whole_archive(tmp_path, scope):
    config = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    store = Store(tmp_path)
    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    keys = []
    with store.connect() as db:
        for index in range(40):
            key = str(uuid4())
            keys.append(key)
            start = at + timedelta(minutes=index)
            db.execute(
                "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text) "
                "VALUES(?,?,?,?,?,?,'done',?)",
                (
                    key,
                    "fixture",
                    stamp(start),
                    stamp(start + timedelta(seconds=30)),
                    "unused",
                    "unused",
                    "fictional transcript " * 5000,
                ),
            )
    memory = Memory(store, config, None)
    tracemalloc.start()
    try:
        kwargs = (
            {"day": at.date()}
            if scope == "day"
            else {"start_day": at.date(), "end_day": at.date()}
            if scope == "range"
            else {}
        )
        result = await memory.search("Summarize all diaries", 1, **kwargs)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # The archive holds 4 MB of transcript text; selecting one should stay below 1 MB.
    assert peak < 1_000_000
    assert [row["id"] for row in result["sources"]] == keys[-1:]
    assert result["retrieval_details"]["recordings_sampled"]


@pytest.mark.anyio
async def test_followups_load_only_recent_history_while_session_reads_remain_complete(tmp_path):
    config = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    store = Store(tmp_path)
    key = uuid4()
    store.ingest(key, "omi", datetime(2026, 10, 15, 12, tzinfo=UTC), b"audio", 30)
    store.complete(str(key), "Kitchen discussion")
    session = store.ensure_session(None, "Kitchen")
    for index in range(8):
        store.save_exchange(session["id"], f"Kitchen question {index}", f"Answer {index}", [])

    class Answer:
        async def chat(self, question, sources, history):
            assert len(history) == 6
            assert history[0]["content"] == "Kitchen question 5"
            assert history[-1]["content"] == "Answer 7"
            return "Recorded answer"

    result = await Memory(store, config, Answer()).chat("Kitchen", 12, session["id"])
    assert result["answer"] == "Recorded answer"
    assert len(store.chat_session(session["id"])["messages"]) == 18
