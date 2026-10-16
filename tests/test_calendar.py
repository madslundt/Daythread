from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as session:
        yield session


def save_summary(client, day, summary="Saved summary"):
    client.app.state.memory.store.save_diary(
        day, "fingerprint", {"day": day, "summary": summary, "sources": []}
    )


def test_calendar_returns_only_sorted_month_metadata_without_inference(client, monkeypatch):
    for day in ("2026-10-31", "2026-11-01", "2026-09-30", "2026-10-01"):
        save_summary(client, day)
    save_summary(client, "2026-10-01", "Replacement summary")

    def unexpected_call(*args, **kwargs):
        pytest.fail("Calendar should only query saved day metadata")

    monkeypatch.setattr(client.app.state.memory.store, "diaries", unexpected_call)
    monkeypatch.setattr(client.app.state.memory.inference, "summarize", unexpected_call)
    assert client.get("/v1/calendar?month=2026-10").json() == {
        "month": "2026-10",
        "days": [
            {"day": "2026-10-01", "has_summary": True},
            {"day": "2026-10-31", "has_summary": True},
        ],
    }


@pytest.mark.parametrize("month", ["0001-01", "2024-02", "2026-02", "2026-12", "9999-12"])
def test_calendar_accepts_valid_months_including_year_boundaries(client, month):
    assert client.get("/v1/calendar", params={"month": month}).json() == {
        "month": month,
        "days": [],
    }


@pytest.mark.parametrize(
    "month",
    [
        "",
        "2026-1",
        "26-10",
        "2026-00",
        "2026-13",
        "0000-01",
        "10000-01",
        "2026-10-01",
        "2026-10 ",
        " 2026-10",
        "２０２６-１０",
        "2026-10\n",
    ],
)
def test_calendar_rejects_invalid_months(client, month):
    assert client.get("/v1/calendar", params={"month": month}).status_code == 422


def test_calendar_requires_month(client):
    assert client.get("/v1/calendar").status_code == 422


@pytest.mark.parametrize("day", ["0001-01-01", "2024-02-29", "9999-12-31"])
def test_calendar_includes_last_supported_day_and_leap_day(client, day):
    save_summary(client, day)
    assert client.get("/v1/calendar", params={"month": day[:7]}).json()["days"] == [
        {"day": day, "has_summary": True}
    ]


def test_calendar_ignores_missing_or_empty_summaries(client):
    for day, summary in (("2026-10-01", None), ("2026-10-02", ""), ("2026-10-03", "  ")):
        save_summary(client, day, summary)
    assert client.get("/v1/calendar?month=2026-10").json()["days"] == []


@pytest.mark.parametrize("change", ["timezone", "correction", "deletion", "retention"])
def test_calendar_follows_existing_summary_invalidation(client, change):
    from uuid import uuid4

    store = client.app.state.memory.store
    key = str(uuid4())
    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    store.ingest(key, "omi", at, b"audio", 1)
    store.complete(key, "Transcript")
    save_summary(client, "2026-10-15")
    assert client.get("/v1/calendar?month=2026-10").json()["days"]
    if change == "timezone":
        assert (
            client.patch("/v1/settings", json={"timezone": "Europe/Copenhagen"}).status_code == 200
        )
    elif change == "correction":
        assert client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected"}).status_code == 200
    elif change == "deletion":
        assert client.delete(f"/v1/audio/{key}").status_code == 200
    else:
        store.apply_retention(1, 1, at + timedelta(days=2))
    assert client.get("/v1/calendar?month=2026-10").json()["days"] == []
    if change in ("timezone", "correction"):
        assert store.diary_versions("2026-10-15")  # Historical versions are excluded.
