"""Translated recap display must preserve canonical structure and evidence identity."""

import asyncio
from uuid import uuid4

import pytest
from test_summary_translation import DANISH
from test_summary_translation import summary as summary

from omi_local.inference import InferenceError
from omi_local.summary_translation_structure import rebuild


def replace_summary(s, text):
    with s.memory.store.connect() as db:
        db.execute("UPDATE summary_outputs SET text=? WHERE id=?", (text, s.id))


def test_translation_cannot_silently_flatten_original_recap_topics(summary):
    s = summary
    text = (f"## Budget\n\nReview budget [{s.source['id']}]\n\n"
            f"## Travel\n\nBook train [{s.source['id']}]")
    with s.memory.store.connect() as db:
        db.execute("UPDATE summary_outputs SET text=? WHERE id=?", (text, s.id))

    async def flattened(text, language):
        return DANISH

    s.memory.inference.translate = flattened
    result = s.client.post(s.path)
    assert result.status_code == 200
    translated = result.json()["translation"]
    assert translated["text"].count("## ") == 2
    assert translated["text"].count(s.source["id"]) == 2
    assert translated["structure"]["version"] == 1


def test_original_offsets_order_bold_labels_and_source_evidence_survive_translation(summary):
    s = summary
    source = s.source["id"]
    original = (f"## Budget 🧾\n\nA budget update [{source}]\n\n"
                f"**Travel**\n\n- **Train** — Book the train [{source}]\n"
                f"  Keep the receipt [{source}]\n\n> Keep this quotation [{source}]")
    replace_summary(s, original)
    terms = {"Budget 🧾": "Økonomi 🧾", "A budget update": "En budgetopdatering",
             "Travel": "Rejse", "Train": "Tog", "Book the train": "Bestil toget",
             "Keep the receipt": "Gem kvitteringen", "Keep this quotation": "Bevar dette citat"}
    active = peak = 0

    async def localized(text, language):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001 if text == "Book the train" else 0.005)
        active -= 1
        return terms[text]

    s.memory.inference.translate = localized
    response = s.client.post(s.path)
    assert response.status_code == 200, response.text
    value = response.json()["translation"]
    mapping = value["structure"]
    assert mapping["summary_version"] == value["summary_version"]
    assert [entry["index"] for entry in mapping["blocks"]] == list(range(6))
    assert [entry["kind"] for entry in mapping["blocks"]] == [
        "heading", "text", "heading", "item", "text", "text",
    ]
    for entry in mapping["blocks"]:
        assert original[entry["start_offset"]:entry["end_offset"]] == entry["original_text"]
    assert rebuild(original, mapping["blocks"]) == value["text"]
    assert "- **Tog** — Bestil toget" in value["text"]
    assert "\n  Gem kvitteringen" in value["text"]
    assert "\n\n> Bevar dette citat" in value["text"]
    assert value["text"].count(source) == 4
    assert peak == 3
    assert s.memory.store.summary_translation_source(s.id)["text"] == original
    assert s.memory.store.get_audio(source)["text"] == s.source["text"]
    assert s.client.get(s.path).json()["translation"] == value


@pytest.mark.parametrize("bad", ["## Injected\nBody", "- New task", "New\n\nsection",
                                  f"Made up [{uuid4()}]", "Invented https://example.com"])
def test_injected_structure_or_evidence_is_rejected_without_partial_cache(summary, bad):
    s = summary

    async def injected(text, language):
        return "Budget" if text == "Budget" else bad

    s.memory.inference.translate = injected
    response = s.client.post(s.path)
    assert response.status_code == 502
    assert s.client.get(s.path).json()["translation"] is None
    with s.memory.store.connect() as db:
        assert not db.execute("SELECT 1 FROM summary_translation_structures").fetchone()
        assert not db.execute("SELECT 1 FROM summary_translations").fetchone()


def test_legacy_translation_projects_only_matching_structure_and_evidence(summary):
    s = summary
    source = s.memory.store.summary_translation_source(s.id)
    translated = f"## Økonomi\n\n{DANISH} [{s.source['id']}]"
    s.memory.store.save_summary_translation(source, "da", translated, ["da"])
    result = s.client.get(s.path).json()
    assert result["regeneration_required"] is False
    assert result["translation"]["text"] == translated
    assert result["translation"]["structure"]["blocks"][0]["original_text"] == "## Budget"
    assert not s.calls


@pytest.mark.parametrize("bad", ["flattened", "missing_citation", "ambiguous_order"])
def test_incompatible_legacy_translation_requires_regeneration_and_can_be_replaced(summary, bad):
    s = summary
    if bad == "ambiguous_order":
        replace_summary(s, s.text + f"\n\n## Travel\n\nBook train [{s.source['id']}]")
        translated = (f"## Rejse\n\nBestil tog [{s.source['id']}]\n\n"
                      f"## Økonomi\n\n{DANISH} [{s.source['id']}]")
    elif bad == "missing_citation":
        translated = f"## Økonomi\n\n{DANISH}"
    else:
        translated = DANISH
    source = s.memory.store.summary_translation_source(s.id)
    s.memory.store.save_summary_translation(source, "da", translated, ["da"])
    result = s.client.get(s.path).json()
    assert result["translation"] is None and result["regeneration_required"] is True
    assert result["regeneration_reason"] == (
        "Saved translation cannot preserve this recap's topics. Translate again."
    )
    assert not s.calls
    fixed = s.client.post(s.path)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["translation"]["structure"]["version"] == 1
    assert fixed.json()["regeneration_required"] is False
    assert s.client.get(s.path).json() == fixed.json()


@pytest.mark.parametrize("mutation", ["correct", "delete", "expire", "remove_output"])
def test_structure_metadata_obeys_same_privacy_cleanup_as_translation(summary, mutation):
    s = summary
    assert s.client.post(s.path).status_code == 200
    if mutation == "correct":
        s.memory.store.correct_transcript(s.source["id"], "Corrected")
    elif mutation == "delete":
        s.memory.store.delete_audio(s.source["id"])
    elif mutation == "expire":
        from datetime import timedelta

        from omi_local.domain import utc_now

        s.memory.store.apply_retention(0, 1, now=utc_now() + timedelta(days=2))
    else:
        with s.memory.store.connect() as db:
            db.execute("DELETE FROM output_evidence WHERE id=?", (s.id,))
    with s.memory.store.connect() as db:
        assert not db.execute("SELECT 1 FROM summary_translation_structures").fetchone()


def test_damaged_saved_mapping_never_projects_guessed_topics(summary):
    s = summary
    assert s.client.post(s.path).status_code == 200
    with s.memory.store.connect() as db:
        db.execute("UPDATE summary_translation_structures SET structure='not JSON'")
    result = s.client.get(s.path)
    assert result.status_code == 200
    assert result.json()["regeneration_required"] is True
    assert result.json()["translation"] is None


def test_inline_links_citations_and_emphasis_keep_original_positions(summary):
    s = summary
    source = s.source["id"]
    original = (f"## Access\n\nOpen [portal](https://example.test) before **confirming** "
                f"[{source}] with the team; keep `ACCESS_ID`.")
    replace_summary(s, original)
    terms = {"Access": "Adgang", "Open": "Åbn", "portal": "portalen", "before": "før",
             "confirming": "bekræftelse", "with the team; keep": "med holdet; behold", ".": "."}

    async def localized(text, language):
        return terms[text]

    s.memory.inference.translate = localized
    response = s.client.post(s.path)
    assert response.status_code == 200, response.text
    value = response.json()["translation"]
    assert value["text"] == (
        f"## Adgang\n\nÅbn [portalen](https://example.test) før **bekræftelse** "
        f"[{source}] med holdet; behold `ACCESS_ID`."
    )
    assert rebuild(original, value["structure"]["blocks"]) == value["text"]
    assert s.client.get(s.path).json()["translation"] == value


def test_failure_cancels_other_inflight_display_units_before_returning(summary):
    s = summary
    started = asyncio.Event()
    cancelled = False

    async def translate(text, language):
        nonlocal cancelled
        if text == "Budget":
            await started.wait()
            raise InferenceError("provider failed")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    s.memory.inference.translate = translate
    assert s.client.post(s.path).status_code == 502
    assert cancelled is True
    assert s.client.get(s.path).json()["translation"] is None


def test_one_overall_timeout_cancels_all_display_units_without_publication(summary):
    s = summary
    active = 0

    async def translate(text, language):
        nonlocal active
        active += 1
        try:
            await asyncio.sleep(10)
        finally:
            active -= 1

    s.memory.settings.inference_timeout_seconds = 0.02
    s.memory.inference.translate = translate
    assert s.client.post(s.path).status_code == 502
    assert active == 0
    assert s.client.get(s.path).json()["translation"] is None
