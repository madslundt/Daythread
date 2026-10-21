"""Weekly recaps reuse the compact recap's evidence and translation contracts."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_recaps import AT, FakeInference, insert
from test_recaps import client as client

from omi_local.api import create_app
from omi_local.day_overview import reading_text
from omi_local.inference import InferenceError
from omi_local.settings import Settings
from omi_local.weekly_overview import (
    MAX_WORDS,
    WeekOverview,
    linked_weekly_overview,
    restore_links,
)

URL = "/v1/weekly-reviews/2026-10-12/overview"
MONDAY = AT - timedelta(days=4)
TITLE = "Project plans & review"


class WeeklyInference(FakeInference):
    async def summarize(self, sources, purpose):
        self.calls.append((sources, purpose))
        if purpose.startswith("a high-level weekly recap"):
            target = quote(f"2026-10-12:{TITLE}", safe="")
            return f"The [{TITLE}](topic:{target}) was proposed, with a final update on Sunday."
        if purpose.startswith("a readable diary recap"):
            return "\n\n".join(
                f"## {TITLE}\n{source['text']} [{source['id']}]" for source in sources)
        return "\n".join(f"{source['text']} [{source['id']}]" for source in sources)


def prepare(client):
    memory = client.app.state.memory
    memory.inference = WeeklyInference()
    first = insert(client, MONDAY, "A launch was proposed for next month.", kind="manual_upload")
    last = insert(client, MONDAY + timedelta(days=6), "A Sunday update was recorded.",
                  kind="manual_upload")
    return memory, first, last


def test_read_is_inference_free_and_generation_uses_the_entire_selected_week(client):
    memory, first, last = prepare(client)
    before = insert(client, MONDAY - timedelta(days=1), "Previous week only", kind="manual_upload")
    after = insert(client, MONDAY + timedelta(days=7), "Next week only", kind="manual_upload")
    read = client.get(URL)
    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"
    assert read.json()["can_generate"] and read.json()["summary"] is None
    assert not memory.inference.calls
    generated = client.post(URL).json()
    assert generated["summary"] and generated["reading_minutes"] == 1
    assert not generated["can_generate"] and not generated["stale"]
    assert generated["accuracy_review"]["checked_source_ids"] == [first, last]
    writers = [(sources, purpose) for sources, purpose in memory.inference.calls
               if purpose.startswith("a high-level weekly recap")]
    assert {source["id"] for source in writers[0][0]} == {first, last}
    assert all(source["id"] not in {before, after} for source in writers[0][0])
    assert "WHOLE" in writers[0][1] and "proposals and options" in writers[0][1]
    assert [topic["id"] for topic in generated["topics"]] == [
        f"2026-10-12:{TITLE}", f"2026-10-18:{TITLE}"]
    assert all("[" not in topic["summary"] for topic in generated["topics"])
    count = len(memory.inference.calls)
    assert client.get(URL).json() == generated
    assert client.post(URL).json() == generated
    assert len(memory.inference.calls) == count


def test_saved_recap_does_not_start_the_existing_detailed_review(client):
    prepare(client)
    assert client.post(URL).json()["summary"]
    detail = client.get("/v1/weekly-reviews/2026-10-12").json()
    assert detail["version"] is None and detail["job"] is None


def test_late_ready_recordings_offer_update_but_queued_recordings_do_not(client):
    memory, _, _ = prepare(client)
    initial = client.post(URL).json()
    pending = str(uuid4())
    memory.store.ingest(pending, "test", MONDAY + timedelta(days=2), b"new audio", 30,
                        source_kind="manual_upload")
    waiting = client.get(URL).json()
    assert waiting["summary"] == initial["summary"] and not waiting["can_generate"]
    memory.store.complete(pending, "A Wednesday decision was made.")
    current = client.get(URL).json()
    assert current["summary"] == initial["summary"] and current["can_generate"]
    assert current["stale"] and current["output_id"] == initial["output_id"]
    updated = client.post(URL).json()
    assert not updated["can_generate"] and not updated["stale"]
    assert updated["output_id"] != initial["output_id"]
    assert pending in updated["accuracy_review"]["checked_source_ids"]


def test_settings_and_policy_changes_do_not_offer_generation_without_new_sources(
    client, monkeypatch,
):
    memory, _, _ = prepare(client)
    initial = client.post(URL).json()
    client.patch("/v1/settings", json={"recap_language": "da", "summary_language": "fr"})
    monkeypatch.setattr("omi_local.weekly_overview.VERSION", "a-new-policy")
    current = client.get(URL).json()
    assert current["stale"] and not current["can_generate"]
    assert current["summary"] == initial["summary"]
    count = len(memory.inference.calls)
    client.patch("/v1/settings", json={"llm_enabled": False})
    assert client.post(URL).json() == current
    assert len(memory.inference.calls) == count


def test_empty_and_non_speech_week_has_no_generation_action(client):
    memory = client.app.state.memory
    assert not client.get(URL).json()["can_generate"]
    assert client.post(URL).json()["summary"] is None
    insert(client, MONDAY, "um uh\n[silence]", kind="manual_upload")
    assert not client.get(URL).json()["can_generate"]
    assert client.post(URL).json()["summary"] is None
    assert not memory.inference.calls


def test_prompt_cleanup_does_not_replace_original_publication_or_review_evidence(client):
    memory = client.app.state.memory
    raw = "um, A launch was proposed for next month.\n[silence]\nWe will test it tomorrow."
    key = insert(client, MONDAY, raw, kind="manual_upload")
    reviewed = []

    class OriginalReview(WeeklyInference):
        async def review_summary(self, sources, draft, **kwargs):
            reviewed.extend(sources)
            return await super().review_summary(sources, draft, **kwargs)

    memory.inference = OriginalReview()
    generated = client.post(URL)
    assert generated.status_code == 200
    assert generated.json()["summary"] and not generated.json()["can_generate"]
    assert reviewed[0]["id"] == key and reviewed[0]["text"] == raw
    assert memory.store.get_audio(key)["text"] == raw
    snapshot = WeekOverview(memory).snapshot(date(2026, 10, 12), AT + timedelta(days=7))
    assert snapshot["sources"][0]["text"] == raw
    path = f"/v1/outputs/{generated.json()['output_id']}/translation"
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("day", ["not-a-date", "2026-10-13", "9999-12-27", "2099-10-12"])
def test_invalid_non_monday_or_future_weeks_are_rejected(client, day):
    assert client.get(f"/v1/weekly-reviews/{day}/overview").status_code == 422
    assert client.post(f"/v1/weekly-reviews/{day}/overview").status_code == 422


def test_week_bounds_follow_dst_and_current_week_is_partial(client, monkeypatch):
    memory = client.app.state.memory
    memory.settings.timezone = "Europe/Copenhagen"
    clock = datetime(2026, 10, 28, 12, tzinfo=UTC)
    monkeypatch.setattr("omi_local.weekly_overview.utc_now", lambda: clock)
    completed = client.get("/v1/weekly-reviews/2026-10-19/overview").json()
    assert datetime.fromisoformat(completed["start"]) == datetime(2026, 10, 18, 22, tzinfo=UTC)
    assert datetime.fromisoformat(completed["end"]) == datetime(2026, 10, 25, 23, tzinfo=UTC)
    assert not completed["partial"]
    current = client.get("/v1/weekly-reviews/2026-10-26/overview").json()
    assert current["partial"] and datetime.fromisoformat(current["cutoff"]) == clock


def test_completed_calendar_week_keeps_its_saved_cutoff_without_saying_in_progress(
    client, monkeypatch,
):
    memory, _, _ = prepare(client)
    midweek = MONDAY + timedelta(days=6, hours=1)
    monkeypatch.setattr("omi_local.weekly_overview.utc_now", lambda: midweek)
    initial = client.post(URL).json()
    assert initial["partial"] and initial["saved_partial"]
    prompt = next(purpose for _, purpose in memory.inference.calls
                  if purpose.startswith("a high-level weekly recap"))
    assert "Calendar timezone: UTC" in prompt
    assert initial["start"] in prompt and initial["end"] in prompt
    assert initial["cutoff"] in prompt and "partial week: True" in prompt
    assert "Convert source UTC timestamps" in prompt and "do not describe the week as" in prompt
    monkeypatch.setattr("omi_local.weekly_overview.utc_now", lambda: MONDAY + timedelta(days=8))
    current = client.get(URL).json()
    assert not current["partial"] and current["saved_partial"]
    assert current["cutoff"] == initial["cutoff"]
    assert current["summary"] == initial["summary"]
    assert not current["can_generate"]
    count = len(memory.inference.calls)
    assert client.post(URL).json() == current
    assert len(memory.inference.calls) == count


def test_repeated_topic_titles_keep_distinct_targets_through_accuracy_review():
    topics = [{"id": "2026-10-12:Launch", "title": "Launch"},
              {"id": "2026-10-18:Launch", "title": "Launch"}]
    original = ("Monday's [Launch](topic:2026-10-12%3ALaunch) was planned. "
                "Sunday's [Launch](topic:2026-10-18%3ALaunch) was released.")
    reviewed = "Monday's Launch was proposed. Sunday's Launch was released."
    restored = restore_links(original, reviewed, topics)
    assert restored == original.replace("was planned", "was proposed")
    assert restore_links(original, "A different subject was discussed.", topics) == (
        "A different subject was discussed.")


def test_plain_unique_titles_get_inline_links_without_an_index_or_changed_words():
    topics = [{"id": "2026-10-12:Project planning", "title": "Project planning"},
              {"id": "2026-10-13:Launch", "title": "Launch"},
              {"id": "2026-10-18:launch", "title": "launch"}]
    draft = "Project planning continued. Launch was discussed, alongside project planning."
    linked = linked_weekly_overview(draft, topics)
    target = "topic:2026-10-12%3AProject%20planning"
    assert linked == (f"[Project planning]({target}) continued. Launch was discussed, "
                      f"alongside [project planning]({target}).")
    assert reading_text(linked) == draft
    assert linked_weekly_overview(linked, topics) == linked
    assert "\n" not in linked and "topic:2026-10-13" not in linked
    assert restore_links(draft, draft, topics) == linked


def test_plain_fallback_preserves_explicit_day_links_and_skips_ambiguous_mentions():
    topics = [{"id": "2026-10-12:Launch", "title": "Launch"},
              {"id": "2026-10-18:Launch", "title": "Launch"},
              {"id": "2026-10-18:Planning", "title": "Planning"}]
    draft = ("Monday's [Launch](topic:2026-10-12%3ALaunch) was planned. "
             "Sunday's [Launch](topic:2026-10-18%3ALaunch) was released. "
             "Launch and Planning were discussed.")
    expected = draft.replace("and Planning", "and [Planning](topic:2026-10-18%3APlanning)")
    assert linked_weekly_overview(draft, topics) == expected
    assert restore_links(draft, reading_text(draft), topics) == expected


def test_generation_links_plain_unique_topic_mentions_before_and_after_review(client):
    memory = client.app.state.memory
    insert(client, MONDAY, "A launch was proposed for next month.", kind="manual_upload")

    class PlainWeeklyInference(WeeklyInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level weekly recap"):
                return f"{TITLE} was proposed."
            return await super().summarize(sources, purpose)

        async def review_summary(self, sources, draft, **kwargs):
            assert draft == f"{TITLE} was proposed."
            return await super().review_summary(sources, draft, **kwargs)

    memory.inference = PlainWeeklyInference()
    generated = client.post(URL).json()
    target = quote(f"2026-10-12:{TITLE}", safe="")
    assert generated["summary"] == f"[{TITLE}](topic:{target}) was proposed."
    assert client.get(URL).json()["summary"] == generated["summary"]
    assert reading_text(generated["summary"]) == f"{TITLE} was proposed."


def test_accuracy_review_uses_local_weekday_for_late_utc_recording(client):
    memory = client.app.state.memory
    memory.settings.timezone = "Europe/Copenhagen"
    source_at = datetime(2026, 10, 11, 22, 30, tzinfo=UTC)
    key = insert(client, source_at, "A Monday launch was proposed.", kind="manual_upload")
    reviewed = []

    class LocalReview(WeeklyInference):
        async def review_summary(self, sources, draft, **kwargs):
            reviewed.extend(sources)
            return await super().review_summary(sources, draft, **kwargs)

    memory.inference = LocalReview()
    result = client.post(URL)
    assert result.status_code == 200
    assert reviewed[0]["id"] == key
    assert reviewed[0]["started_at"] == "2026-10-12T00:30:00+02:00"
    assert reviewed[0]["text"] == "A Monday launch was proposed."
    assert datetime.fromisoformat(memory.store.get_audio(key)["started_at"]) == source_at


def test_translation_preserves_exact_targets_and_source_lifecycle_invalidates(client):
    memory, first, _ = prepare(client)
    client.patch("/v1/settings", json={"translation_languages": ["da"]})
    generated = client.post(URL).json()
    path = f"/v1/outputs/{generated['output_id']}/translation"
    calls = []

    async def translate(text, language):
        calls.append(text)
        return "Projektplaner" if text == TITLE else "En plan blev drøftet."

    memory.inference.translate = translate
    assert client.get(path).json()["translation"] is None and not calls
    translated = client.post(path).json()["translation"]["text"]
    assert f"[Projektplaner](topic:{quote('2026-10-12:'+TITLE, safe='')})" in translated
    assert all("topic:" not in text for text in calls)
    count = len(calls)
    assert client.post(path).json()["translation"]["text"] == translated
    assert len(calls) == count
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    assert client.get(URL, headers={"X-Omi-Profile": profile}).json()["summary"] is None
    assert client.get(path, headers={"X-Omi-Profile": profile}).status_code == 404
    assert client.get(URL).json() == generated
    client.patch(f"/v1/transcripts/{first}", json={"text": "Corrected original."})
    assert client.get(URL).json()["summary"] is None
    assert client.get(path).status_code == 404


def test_replacement_and_deletion_remove_old_translations(client):
    memory, _, last = prepare(client)
    initial = client.post(URL).json()
    insert(client, MONDAY + timedelta(days=1), "A second day's update", kind="manual_upload")
    updated = client.post(URL).json()
    assert updated["output_id"] != initial["output_id"]
    assert client.get(f"/v1/outputs/{initial['output_id']}/translation").status_code == 404
    memory.store.delete_audio(last)
    assert client.get(URL).json()["summary"] is None
    assert client.get(f"/v1/outputs/{updated['output_id']}/translation").status_code == 404


def test_transcript_expiry_purges_saved_translation_and_recap(client):
    memory, first, _ = prepare(client)
    client.patch("/v1/settings", json={"translation_languages": ["da"]})
    initial = client.post(URL).json()
    path = f"/v1/outputs/{initial['output_id']}/translation"

    async def translate(text, language):
        return "En plan blev drøftet."

    memory.inference.translate = translate
    assert client.post(path).status_code == 200
    with memory.store.connect() as db:
        db.execute("UPDATE audio SET state='expired',text=NULL WHERE id=?", (first,))
    assert client.get(URL).json()["summary"] is None
    assert client.get(path).status_code == 404
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM summary_translations").fetchone()[0] == 0


def test_saved_weekly_recap_survives_restart(tmp_path):
    settings = Settings(data_dir=tmp_path, timezone="UTC", worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as first:
        prepare(first)
        generated = first.post(URL).json()
        assert generated["summary"]
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(URL).json() == generated


def test_oversized_recap_is_never_published_and_retry_uses_the_draft(client):
    memory, _, _ = prepare(client)

    class Oversized(WeeklyInference):
        drafts = 0

        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level weekly recap"):
                self.drafts += 1
                if self.drafts == 2:
                    assert sources[0]["text"] == "word " * MAX_WORDS + "extra"
                    assert "Rewrite the supplied" in purpose
                return "word " * MAX_WORDS + "extra"
            return await super().summarize(sources, purpose)

    memory.inference = Oversized()
    assert client.post(URL).status_code == 502
    assert memory.inference.drafts == 2
    assert client.get(URL).json()["summary"] is None


@pytest.mark.parametrize("mutation", ["transcript", "language", "model", "delete"])
def test_mutations_during_generation_cannot_publish_stale_evidence(client, mutation):
    memory, first, _ = prepare(client)

    class Mutating(WeeklyInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level weekly recap"):
                if mutation == "transcript":
                    memory.store.correct_transcript(first, "The launch was cancelled.")
                elif mutation == "language":
                    memory.settings.recap_language = "da"
                elif mutation == "model":
                    memory.settings.llm_model = "changed-model"
                else:
                    memory.store.delete_audio(first)
            return await super().summarize(sources, purpose)

    memory.inference = Mutating()
    assert client.post(URL).status_code == 409
    assert client.get(URL).json()["summary"] is None


def test_concurrent_requests_reuse_one_saved_recap(client):
    memory, _, _ = prepare(client)

    async def run():
        return await asyncio.gather(*(WeekOverview(memory).generate(date(2026, 10, 12))
                                      for _ in range(2)))

    first, second = asyncio.run(run())
    assert first == second
    assert sum(purpose.startswith("a high-level weekly recap")
               for _, purpose in memory.inference.calls) == 1


def test_failed_update_keeps_the_prior_saved_recap(client):
    memory, _, _ = prepare(client)
    initial = client.post(URL).json()
    insert(client, MONDAY + timedelta(days=1), "A new proposal.", kind="manual_upload")

    async def fail(*args, **kwargs):
        raise InferenceError("Local model unavailable")

    memory.inference.summarize_day_overview = fail
    assert client.post(URL).status_code == 502
    current = client.get(URL).json()
    assert current["summary"] == initial["summary"] and current["can_generate"]
    assert current["output_id"] == initial["output_id"]


def test_long_original_tail_reaches_writer_and_accuracy_review(client):
    memory, first, _ = prepare(client)
    long_text = "A detailed planning discussion. " * 3500 + "A late cancellation was reported."
    memory.store.correct_transcript(first, long_text)
    generated = client.post(URL).json()
    writer_sources = next(sources for sources, purpose in memory.inference.calls
                          if purpose.startswith("a high-level weekly recap"))
    assert next(source["text"] for source in writer_sources if source["id"] == first) == long_text
    assert first in generated["accuracy_review"]["checked_source_ids"]


def test_new_independent_evidence_during_generation_leaves_saved_recap_updatable(client):
    memory, _, _ = prepare(client)

    class LateArrival(WeeklyInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level weekly recap"):
                insert(client, MONDAY + timedelta(days=2), "A new Wednesday update.",
                       kind="manual_upload")
            return await super().summarize(sources, purpose)

    memory.inference = LateArrival()
    generated = client.post(URL).json()
    assert generated["summary"] and generated["stale"] and generated["can_generate"]
