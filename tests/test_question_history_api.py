"""Question history can be opened by local day without a notification."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_question_history import END, saved_job

from omi_local.api import create_app
from omi_local.domain import stamp
from omi_local.settings import Settings


@pytest.fixture
def archive(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False,
                        background_processing_enabled=False,
                        timezone="Europe/Copenhagen", _env_file=None)
    with TestClient(create_app(settings)) as client:
        yield client, client.app.state.profiles.runtimes["default"].jobs


def test_direct_day_matches_notification_history_without_selection(archive):
    client, jobs = archive
    saved_job(jobs, end=END - timedelta(hours=1), questions=[{
        "question": "Earlier question?", "answer": "Earlier answer",
        "asked_at": stamp(END - timedelta(hours=1)),
        "time_precision": "request",
    }])
    selected = saved_job(jobs, questions=[{
        "question": f"Question {i}?", "answer": "4" if i == 3 else None,
        "asked_at": stamp(END), "time_precision": "request", "position": i,
    } for i in (1, 2, 3)])
    saved_job(jobs, end=END - timedelta(days=1), question="Other day?")
    notification = client.get(f"/v1/jobs/{selected['id']}/questions").json()
    response = client.get("/v1/questions", params={"day": "2026-10-31"})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["day"] == "2026-10-31" and page["timezone"] == "Europe/Copenhagen"
    assert page["selected_job_id"] is None and page["selected_question_ids"] == []
    assert page["questions"] == notification["questions"]
    assert [q["question"] for q in page["questions"]] == [
        "Question 3?", "Question 2?", "Question 1?", "Earlier question?",
    ]
    assert page["questions"][0]["answer"] == "4"
    assert page["questions"][-1]["answer"] == "Earlier answer"
    assert not page["processing"] and page["error"] is None


def test_empty_day_and_omitted_today_do_not_start_inference(archive, monkeypatch):
    client, jobs = archive
    monkeypatch.setattr("omi_local.question_history.utc_now",
                        lambda: datetime(2026, 10, 31, 23, 30, tzinfo=UTC))
    page = client.get("/v1/questions").json()
    assert page == {"day": "2026-11-01", "timezone": "Europe/Copenhagen",
                    "selected_job_id": None, "selected_question_ids": [], "questions": [],
                    "processing": False, "error": None}
    assert client.get("/v1/questions?day=2026-10-30&retry=true").json()["questions"] == []
    with jobs.store.connect() as db:
        assert not db.execute("SELECT * FROM question_history_requests").fetchall()


@pytest.mark.parametrize("day", ["2026-02-30", "2026-1-1", "0000-01-01", "20261031",
                                  "2026-10-31T00:00:00Z", "1793404800", ""])
def test_direct_day_rejects_invalid_or_non_iso_calendar_days(archive, day):
    client, _ = archive
    assert client.get("/v1/questions", params={"day": day}).status_code == 422


def test_day_route_is_profile_scoped_and_preserves_explicit_backfill_retry(archive):
    client, jobs = archive
    selected = saved_job(jobs)
    partial = client.get("/v1/questions?day=2026-10-31").json()
    assert partial["processing"] and partial["questions"][0]["question"] == "Latest question?"
    with jobs.store.connect() as db:
        db.execute("UPDATE question_history_requests SET state='failed',error='Failure'")
    assert not client.get("/v1/questions?day=2026-10-31").json()["processing"]
    assert client.get("/v1/questions?day=2026-10-31&retry=true").json()["processing"]
    other = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    response = client.get("/v1/questions?day=2026-10-31", headers={"X-Omi-Profile": other})
    assert response.status_code == 200 and response.json()["questions"] == []
    assert client.get(f"/v1/jobs/{selected['id']}/questions",
                      headers={"X-Omi-Profile": other}).status_code == 404
    source_id = selected["result"]["sources"][0]["id"]
    with jobs.store.connect() as db:
        db.execute("DELETE FROM audio WHERE id=?", (source_id,))
    assert client.get("/v1/questions?day=2026-10-31").json()["questions"] == []
