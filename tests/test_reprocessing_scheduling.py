"""Automatic recaps leave redo-owned days to their durable archive job."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_extended import audio_data

from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.worker import RecapBatch, RecapBatcher, scheduled_recaps

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, timezone="UTC", _env_file=None)
    result = Memory(Store(tmp_path), settings, SimpleNamespace())
    result.recap = AsyncMock(return_value={})
    monkeypatch.setattr("omi_local.worker.utc_now", lambda: NOW)
    return result


def recording(memory, day, *, time="12:00:00", duration=1):
    key = str(uuid4())
    memory.store.ingest(key, "phone", datetime.fromisoformat(f"{day}T{time}+00:00"),
                        audio_data(), duration, source_kind="manual_upload")
    memory.store.complete(key, "We agreed to send the delivery plan on Friday.")
    return key


def reserve(memory, days, *, state="summarizing", start=None, end=None):
    key = str(uuid4())
    with memory.store.connect() as db:
        db.execute(
            "INSERT INTO reprocessing_jobs(id,kind,start_day,end_day,state,days,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (key, "summaries", start or days[0], end or days[-1], state,
             json.dumps(days), NOW.isoformat()),
        )
    return key


@pytest.mark.parametrize("state", ["queued", "transcribing", "summarizing"])
def test_automatic_recaps_do_not_enter_redo_owned_days(memory, state):
    recording(memory, "2026-10-15")
    reserve(memory, ["2026-10-15"], state=state)

    asyncio.run(scheduled_recaps(memory))

    memory.recap.assert_not_awaited()


def test_exact_snapshot_days_leave_unrelated_days_automatic(memory):
    for day in ("2026-10-15", "2026-10-16", "2026-10-17"):
        recording(memory, day)
    reserve(memory, ["2026-10-15", "2026-10-17"])

    asyncio.run(scheduled_recaps(memory))

    memory.recap.assert_awaited_once_with(date(2026, 10, 16))


def test_cross_midnight_snapshot_reserves_dates_outside_requested_bounds(memory):
    recording(memory, "2026-10-15", time="23:59:59", duration=2)
    reserve(memory, ["2026-10-15", "2026-10-16"], start="2026-10-15", end="2026-10-15")

    asyncio.run(scheduled_recaps(memory))

    memory.recap.assert_not_awaited()


@pytest.mark.parametrize("terminal_state", ["completed", "failed"])
def test_terminal_jobs_release_days_for_future_automatic_updates(memory, terminal_state):
    recording(memory, "2026-10-15")
    key = reserve(memory, ["2026-10-15"])
    asyncio.run(scheduled_recaps(memory))
    memory.recap.assert_not_awaited()
    with memory.store.connect() as db:
        db.execute("UPDATE reprocessing_jobs SET state=? WHERE id=?", (terminal_state, key))

    asyncio.run(scheduled_recaps(memory))

    memory.recap.assert_awaited_once_with(date(2026, 10, 15))


def test_reserved_day_discards_pending_automatic_batch(memory):
    recording(memory, "2026-10-15")
    reserve(memory, ["2026-10-15"])
    batcher = RecapBatcher()
    before = NOW - timedelta(minutes=1)
    batcher.pending["2026-10-15"] = RecapBatch("previous-evidence", before, before)

    asyncio.run(scheduled_recaps(memory, batcher))

    assert "2026-10-15" not in batcher.pending
    memory.recap.assert_not_awaited()


def test_job_started_during_scheduler_pass_reserves_later_days(memory):
    for day in ("2026-10-15", "2026-10-16"):
        recording(memory, day)

    async def recap(day):
        reserve(memory, ["2026-10-16"])
        return {}

    memory.recap.side_effect = recap

    asyncio.run(scheduled_recaps(memory))

    memory.recap.assert_awaited_once_with(date(2026, 10, 15))
