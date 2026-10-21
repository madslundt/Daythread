"""The compact day recap is generated only on request and remains a saved snapshot."""

import asyncio
from datetime import timedelta
from urllib.parse import quote

from fastapi.testclient import TestClient
from test_recaps import AT, FakeInference, insert
from test_recaps import client as client

from omi_local.api import create_app
from omi_local.day_overview import MAX_WORDS, DayOverview, linked_overview, reading_text
from omi_local.settings import Settings
from omi_local.worker import scheduled_recaps

URL = "/v1/recaps/2026-10-16/overview"
TITLE = "Launch plans & review"


class OverviewInference(FakeInference):
    async def summarize(self, sources, purpose):
        self.calls.append((sources, purpose))
        if purpose.startswith("a high-level day recap"):
            return f"The [{TITLE}](topic:{quote(TITLE, safe='')}) moved forward today."
        if purpose.startswith("a readable diary recap"):
            return "\n\n".join(
                f"## {TITLE}\n{source['text']} [{source['id']}]." for source in sources)
        return "\n".join(f"{source['text']} [{source['id']}]" for source in sources)


def prepare(client):
    memory = client.app.state.memory
    memory.inference = OverviewInference()
    key = insert(client, kind="manual_upload")
    return memory, key


def test_get_and_background_generation_never_create_the_requested_overview(client, monkeypatch):
    memory, _ = prepare(client)
    result = client.get(URL)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["can_generate"] and result.json()["summary"] is None
    assert not memory.inference.calls
    monkeypatch.setattr("omi_local.worker.utc_now", lambda: AT + timedelta(hours=1))
    asyncio.run(scheduled_recaps(memory))
    assert memory.inference.calls
    assert client.get(URL).json()["summary"] is None
    assert not any(purpose.startswith("a high-level day recap")
                   for _, purpose in memory.inference.calls)


def test_requested_overview_is_saved_and_repeated_requests_reuse_it(client):
    memory, _ = prepare(client)
    generated = client.post(URL).json()
    assert generated["summary"] and not generated["stale"]
    assert generated["reading_minutes"] == 1
    assert f"(topic:{quote(TITLE, safe='')})" in generated["summary"]
    assert generated["topics"] == [{"id": TITLE, "title": TITLE,
                                      "summary": "Share the notes tomorrow."}]
    calls = len(memory.inference.calls)
    assert client.get(URL).json() == generated
    assert DayOverview(memory).cached(AT.date()) == generated
    assert client.post(URL).json() == generated
    assert len(memory.inference.calls) == calls
    with memory.store.connect() as db:
        row = db.execute("SELECT content FROM day_overviews WHERE day='2026-10-16'").fetchone()
        assert generated["summary"] in row["content"]


def test_saved_overview_survives_backend_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as first:
        prepare(first)
        generated = first.post(URL).json()
        assert generated["summary"]
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(URL).json() == generated


def test_overview_is_profile_scoped_and_dates_are_validated(client):
    prepare(client)
    generated = client.post(URL).json()
    profile = client.post("/v1/profiles", json={"name": "Another archive"}).json()["id"]
    other = client.get(URL, headers={"X-Omi-Profile": profile})
    assert other.status_code == 200
    assert other.json()["summary"] is None and not other.json()["can_generate"]
    assert client.get(URL).json() == generated
    assert client.get("/v1/recaps/not-a-day/overview").status_code == 422
    assert client.post("/v1/recaps/not-a-day/overview").status_code == 422


def test_empty_or_non_speech_days_do_not_offer_or_generate_an_overview(client):
    memory = client.app.state.memory
    assert not client.get(URL).json()["can_generate"]
    assert client.post(URL).json()["summary"] is None
    insert(client, text="um uh [silence]", kind="manual_upload")
    # Only unambiguous filler is removed; a whole annotation line is non-speech.
    key = insert(client, AT - timedelta(days=1), "um uh\n[silence]", kind="manual_upload")
    other_url = "/v1/recaps/2026-10-15/overview"
    assert not client.get(other_url).json()["can_generate"]
    assert client.post(other_url).json()["summary"] is None
    assert not memory.inference.calls
    assert memory.store.get_audio(key)["text"] == "um uh\n[silence]"


def test_no_useful_daily_content_hides_generation_without_repeated_inference(client):
    memory, _ = prepare(client)

    class EmptyInference(FakeInference):
        async def summarize(self, sources, purpose):
            from omi_local.summary_filter import NO_CONTENT
            self.calls.append((sources, purpose))
            return NO_CONTENT

    memory.inference = EmptyInference()
    generated = client.post(URL).json()
    assert generated["summary"] is None and not generated["can_generate"]
    calls = len(memory.inference.calls)
    assert not client.get(URL).json()["can_generate"]
    client.post(URL)
    assert len(memory.inference.calls) == calls


def test_new_recordings_keep_saved_overview_until_explicit_regeneration(client):
    memory, _ = prepare(client)
    initial = client.post(URL).json()
    insert(client, AT + timedelta(minutes=10), "Book the service on Friday", kind="manual_upload")
    calls = len(memory.inference.calls)
    saved = client.get(URL).json()
    assert saved["summary"] == initial["summary"] and saved["stale"]
    assert saved["generated_at"] == initial["generated_at"] and saved["can_generate"]
    assert len(memory.inference.calls) == calls
    updated = client.post(URL).json()
    assert updated["summary"] and not updated["stale"]
    last_inputs = memory.inference.calls[-1][0]
    assert "Book the service on Friday" in last_inputs[0]["text"]
    assert "Share the notes tomorrow" in last_inputs[0]["text"]


def test_failed_regeneration_keeps_the_last_saved_overview_readable(client):
    memory, _ = prepare(client)
    initial = client.post(URL).json()
    insert(client, AT + timedelta(minutes=10), "Book the service", kind="manual_upload")

    class FailedOverview(OverviewInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level day recap"):
                from omi_local.inference import InferenceError
                raise InferenceError("Temporary inference failure")
            return await super().summarize(sources, purpose)

    memory.inference = FailedOverview()
    assert client.post(URL).status_code == 502
    saved = client.get(URL).json()
    assert saved["summary"] == initial["summary"] and saved["stale"]
    assert saved["topics"] == initial["topics"]
    assert saved["generated_at"] == initial["generated_at"]


def test_concurrent_generation_requests_share_one_saved_result(client):
    memory, _ = prepare(client)

    class SlowOverview(OverviewInference):
        async def summarize(self, sources, purpose):
            await asyncio.sleep(0)
            return await super().summarize(sources, purpose)

    memory.inference = SlowOverview()

    async def requests():
        return await asyncio.gather(DayOverview(memory).generate(AT.date()),
                                    DayOverview(memory).generate(AT.date()))

    first, second = asyncio.run(requests())
    assert first == second and first["summary"]
    assert sum(purpose.startswith("a high-level day recap")
               for _, purpose in memory.inference.calls) == 1


def test_correction_or_deletion_removes_saved_overview_and_topic_details(client):
    memory, key = prepare(client)
    assert client.post(URL).json()["summary"]
    assert client.patch(f"/v1/transcripts/{key}", json={"text": "Revised plans"}).status_code == 200
    current = client.get(URL).json()
    assert current["summary"] is None and current["topics"] == []
    assert client.post(URL).json()["summary"]
    assert client.delete(f"/v1/audio/{key}").status_code == 200
    current = client.get(URL).json()
    assert current["summary"] is None and not current["can_generate"]
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM day_overviews").fetchone()[0] == 0


def test_unrelated_source_changes_do_not_remove_a_saved_overview(client):
    _, key = prepare(client)
    initial = client.post(URL).json()
    unrelated = insert(client, AT - timedelta(days=1), "Different day", kind="manual_upload")
    client.patch(f"/v1/transcripts/{unrelated}", json={"text": "An unrelated correction"})
    assert client.get(URL).json()["summary"] == initial["summary"]
    assert client.get(f"/v1/transcripts/{key}").status_code == 200


def test_transcript_retention_invalidates_saved_overview(client):
    memory, _ = prepare(client)
    assert client.post(URL).json()["summary"]
    memory.store.apply_retention(0, 1, now=AT + timedelta(days=3))
    current = client.get(URL).json()
    assert current["summary"] is None and current["topics"] == []


def test_evidence_changed_during_overview_cannot_be_saved(client):
    memory, key = prepare(client)

    class CorrectedInference(OverviewInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level day recap"):
                memory.store.correct_transcript(key, "Actually the launch was cancelled")
            return await super().summarize(sources, purpose)

    memory.inference = CorrectedInference()
    assert client.post(URL).status_code == 409
    assert client.get(URL).json()["summary"] is None


def test_five_minute_limit_retries_and_never_saves_an_oversized_overview(client):
    memory, _ = prepare(client)

    class VerboseInference(OverviewInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level day recap"):
                self.calls.append((sources, purpose))
                return "word " * (MAX_WORDS + 1)
            return await super().summarize(sources, purpose)

    memory.inference = VerboseInference()
    assert client.post(URL).status_code == 502
    assert client.get(URL).json()["summary"] is None
    assert sum(purpose.startswith("a high-level day recap")
               for _, purpose in memory.inference.calls) == 2


def test_retry_accepts_a_complete_overview_within_five_minutes(client):
    memory, _ = prepare(client)

    class ShortenedInference(OverviewInference):
        attempts = 0

        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level day recap"):
                self.attempts += 1
                return "word " * (MAX_WORDS + 1 if self.attempts == 1 else MAX_WORDS)
            return await super().summarize(sources, purpose)

    memory.inference = ShortenedInference()
    result = client.post(URL)
    assert result.status_code == 200
    assert len(reading_text(result.json()["summary"]).split()) == MAX_WORDS
    assert result.json()["reading_minutes"] == 5
    assert memory.inference.attempts == 2


def test_unknown_links_are_removed_and_plain_topic_mentions_are_linked():
    topics = [{"id": TITLE, "title": TITLE, "summary": "Full detail"}]
    text = linked_overview(
        f"{TITLE} progressed. [Invented](topic:Unknown) and [external](https://bad.example).",
        topics)
    assert f"[{TITLE}](topic:{quote(TITLE, safe='')})" in text
    assert "topic:Unknown" not in text and "https:" not in text
    assert reading_text(text) == f"{TITLE} progressed. Invented and external."
