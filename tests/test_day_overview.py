"""The compact day recap is generated only on request and remains a saved snapshot."""

import asyncio
import json
from datetime import timedelta
from urllib.parse import quote

from fastapi.testclient import TestClient
from test_recaps import AT, FakeInference, insert
from test_recaps import client as client

from omi_local.api import create_app
from omi_local.day_overview import (
    MAX_WORDS,
    DayOverview,
    linked_overview,
    reading_text,
    topic_details,
)
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
    input_text = "\n".join(source["text"] for source in last_inputs)
    assert "Book the service on Friday" in input_text
    assert "Share the notes tomorrow" in input_text


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
                words = MAX_WORDS + 1 if self.attempts == 1 else MAX_WORDS
                return "word " * words
            return await super().summarize(sources, purpose)

    memory.inference = ShortenedInference()
    result = client.post(URL)
    assert result.status_code == 200
    assert len(reading_text(result.json()["summary"]).split()) == MAX_WORDS
    assert result.json()["reading_minutes"] == 5
    assert memory.inference.attempts == 2


def test_oversized_overview_retry_rewrites_the_actual_draft(client):
    memory, _ = prepare(client)
    draft = f"The [{TITLE}](topic:{quote(TITLE, safe='')}) progressed. " + "context " * MAX_WORDS

    class RewriteInference(OverviewInference):
        attempts = 0

        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level day recap"):
                self.attempts += 1
                if self.attempts == 1:
                    return draft
                assert sources[0]["text"] == draft.strip()
                assert "Rewrite the supplied draft" in purpose
                return f"The [{TITLE}](topic:{quote(TITLE, safe='')}) progressed."
            return await super().summarize(sources, purpose)

    memory.inference = RewriteInference()
    result = client.post(URL)
    assert result.status_code == 200
    assert result.json()["summary"] == draft.split(" context")[0]
    assert memory.inference.attempts == 2


def test_unknown_links_are_removed_and_plain_topic_mentions_are_linked():
    topics = [{"id": TITLE, "title": TITLE, "summary": "Full detail"}]
    text = linked_overview(
        f"{TITLE} progressed. [Invented](topic:Unknown) and [external](https://bad.example).",
        topics)
    assert f"[{TITLE}](topic:{quote(TITLE, safe='')})" in text
    assert "topic:Unknown" not in text and "https:" not in text
    assert reading_text(text) == f"{TITLE} progressed. Invented and external."


def test_paraphrased_topics_do_not_append_a_topic_index():
    topics = [{"id": TITLE, "title": TITLE, "summary": "Launch details"},
              {"id": "Hiring activities", "title": "Hiring activities", "summary": "Hiring"}]
    text = linked_overview("Launch plans progressed, and hiring remains open.", topics)
    assert text == "Launch plans progressed, and hiring remains open."
    assert linked_overview(text, topics) == text
    assert linked_overview("", topics) == ""
    assert linked_overview("[11111111-1111-4111-8111-111111111111]", topics) == ""


def test_existing_overview_footer_is_removed_and_registered_without_model_calls(client):
    memory, _ = prepare(client)
    initial = client.post(URL).json()
    with memory.store.connect() as db:
        content = dict(initial)
        content.pop("output_id")
        content["summary"] += f"\n\n[{TITLE}](topic:{quote(TITLE, safe='')})"
        db.execute("DELETE FROM output_evidence WHERE id=?", (initial["output_id"],))
        db.execute("UPDATE day_overviews SET content=?", (json.dumps(content),))
    calls = len(memory.inference.calls)
    migrated = client.get(URL).json()
    assert migrated["summary"] == initial["summary"]
    assert migrated["output_id"] and migrated["output_id"] != initial["output_id"]
    assert client.get(URL).json() == migrated
    saved = memory.store.summary_translation_source(migrated["output_id"])
    assert saved["text"] == initial["summary"]
    assert len(memory.inference.calls) == calls
    assert not migrated["can_generate"]


def test_preferences_and_policy_changes_do_not_offer_generation_without_new_recordings(
    client, monkeypatch,
):
    memory, _ = prepare(client)
    initial = client.post(URL).json()
    assert not initial["can_generate"]
    assert client.patch("/v1/settings", json={"recap_language": "da", "summary_language": "fr"}
                        ).status_code == 200
    monkeypatch.setattr("omi_local.day_overview.VERSION", "a-new-policy")
    current = client.get(URL).json()
    assert current["summary"] == initial["summary"]
    assert current["stale"] and not current["can_generate"]
    calls = len(memory.inference.calls)
    assert client.post(URL).json() == current
    assert len(memory.inference.calls) == calls


def test_pending_recording_does_not_offer_generation_until_transcribed(client):
    from uuid import uuid4

    memory, _ = prepare(client)
    initial = client.post(URL).json()
    key = str(uuid4())
    memory.store.ingest(key, "test", AT + timedelta(minutes=10), b"new audio", 30,
                        source_kind="manual_upload")
    current = client.get(URL).json()
    assert current["summary"] == initial["summary"]
    assert not current["can_generate"]
    memory.store.complete(key, "We proposed a follow-up tomorrow")
    assert client.get(URL).json()["can_generate"]


def test_overview_translation_preserves_topic_targets_and_invalidates_on_replacement(client):
    memory, key = prepare(client)
    client.patch("/v1/settings", json={"translation_languages": ["da"]})
    generated = client.post(URL).json()
    path = f"/v1/outputs/{generated['output_id']}/translation"
    translations = []

    async def translate(text, language):
        translations.append((text, language))
        return "Dagens planer" if text == TITLE else "Forslaget blev drøftet i dag."

    memory.inference.translate = translate
    assert client.get(path).json()["translation"] is None
    assert not translations
    translated = client.post(path).json()["translation"]["text"]
    assert f"[Dagens planer](topic:{quote(TITLE, safe='')})" in translated
    assert all("topic:" not in text for text, _ in translations)
    assert client.get(URL).json() == generated
    calls = len(translations)
    assert client.post(path).json()["translation"]["text"] == translated
    assert len(translations) == calls
    profile = client.post("/v1/profiles", json={"name": "Other"}).json()["id"]
    assert client.get(path, headers={"X-Omi-Profile": profile}).status_code == 404
    insert(client, AT + timedelta(minutes=10), "A second proposal", kind="manual_upload")
    replaced = client.post(URL).json()
    assert replaced["output_id"] != generated["output_id"]
    assert client.get(path).status_code == 404
    next_path = f"/v1/outputs/{replaced['output_id']}/translation"
    assert client.post(next_path).status_code == 200
    client.patch(f"/v1/transcripts/{key}", json={"text": "Corrected evidence"})
    assert client.get(next_path).status_code == 404
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM summary_translations").fetchone()[0] == 0
    fresh = client.post(URL).json()
    final_path = f"/v1/outputs/{fresh['output_id']}/translation"
    assert client.post(final_path).status_code == 200
    client.delete(f"/v1/audio/{key}")
    assert client.get(final_path).status_code == 404


def test_topic_navigation_keeps_each_subjects_original_evidence_separate():
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    markdown = (f"## Ownership\nA handoff is planned. [{first}] [{first}]\n\n"
                f"## Quality review\nAn independent review was suggested. [{second}]")
    navigation = topic_details(markdown, include_source_ids=True)
    assert navigation[0]["source_ids"] == [first]
    assert navigation[1]["source_ids"] == [second]
    assert all("source_ids" not in topic for topic in topic_details(markdown))
    assert navigation[1]["summary"] == "An independent review was suggested."


def test_overview_uses_original_action_states_instead_of_overstated_topic_notes(client):
    memory = client.app.state.memory
    insert(client, text="The team is looking into how to deploy directly in production. "
           "The risk assessment was completed.", kind="manual_upload")

    class StatusInference(OverviewInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a readable diary recap"):
                return ("## Catalog\nThe team is deploying directly to production. "
                        f"[{sources[0]['id']}]")
            if purpose.startswith("a high-level day recap"):
                self.calls.append((sources, purpose))
                text = " ".join(source["text"] for source in sources)
                if "looking into how to deploy" in text:
                    return ("The team is considering direct production deployment. "
                            "The risk assessment was reported completed.")
                return "The team is deploying directly to production."
            return await super().summarize(sources, purpose)

    memory.inference = StatusInference()
    result = client.post(URL)
    assert result.status_code == 200
    summary = result.json()["summary"]
    assert "considering direct production deployment" in summary
    assert "risk assessment was reported completed" in summary
    assert "team is deploying" not in summary


def test_accuracy_review_runs_before_saving_and_gets_original_transcripts(client):
    memory, key = prepare(client)

    class ReviewedInference(OverviewInference):
        async def review_summary(self, sources, draft, **kwargs):
            assert sources[0]["id"] == key
            assert sources[0]["text"] == "Share the notes tomorrow"
            return "Sharing the notes is planned for tomorrow.", {"correction_count": 1}

    memory.inference = ReviewedInference()
    generated = client.post(URL).json()
    assert generated["summary"].startswith("Sharing the notes is planned for tomorrow.")
    assert generated["accuracy_review"]["correction_count"] == 1
    assert client.get(URL).json() == generated


def test_encoded_navigation_does_not_break_exact_claim_corrections(client):
    memory, _ = prepare(client)

    class PlainReviewInference(OverviewInference):
        async def review_summary(self, sources, draft, **kwargs):
            assert draft == f"The {TITLE} moved forward today."
            return draft.replace("moved forward today", "was proposed today"), {
                "correction_count": 1}

    memory.inference = PlainReviewInference()
    generated = client.post(URL).json()
    assert generated["summary"] == (
        f"The [{TITLE}](topic:{quote(TITLE, safe='')}) was proposed today.")


def test_recap_language_can_translate_link_labels_without_changing_topic_targets(client):
    memory, _ = prepare(client)

    class DanishOverview(OverviewInference):
        async def summarize(self, sources, purpose):
            if purpose.startswith("a high-level day recap"):
                return f"Dagens [lanceringsplaner](topic:{quote(TITLE, safe='')}) blev drøftet."
            return await super().summarize(sources, purpose)

        async def review_summary(self, sources, draft, **kwargs):
            assert draft == "Dagens lanceringsplaner blev drøftet."
            return draft, {"correction_count": 0}

    memory.inference = DanishOverview()
    generated = client.post(URL).json()
    assert generated["summary"] == (
        f"Dagens [lanceringsplaner](topic:{quote(TITLE, safe='')}) blev drøftet.")
