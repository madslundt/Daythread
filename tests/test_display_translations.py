"""Canonical display translations never accept client prose or change evidence identity."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from test_decisions import content, create, event
from test_inbox import listing, proposal, run, source
from test_summary_translation import DANISH
from test_summary_translation import summary as summary

from omi_local.domain import utc_now
from omi_local.inbox_domain import Mutation
from omi_local.inbox_store import InboxStore
from omi_local.inference import InferenceError
from omi_local.store import Store

PATH = "/v1/display-translations"


def canonical(s):
    source, store = s.source, s.memory.store
    run(store, [proposal(source["id"], source["text"], title="Review the budget")])
    inbox = listing(store)["items"][0]
    decision = create(s.client, source, "Use the budget plan")
    section = {"title": "Budget planning", "start_offset": 0,
               "end_offset": len(source["text"]), "chapter_id": "test-chapter"}
    with store.connect() as db:
        db.execute("UPDATE audio SET topic_details=? WHERE id=?", (
            json.dumps({"title": "Budget", "sections": [section]}), source["id"],
        ))
    refs = [{"kind": "inbox", "id": inbox["id"], "version": inbox["version"]},
            {"kind": "decision", "id": decision["id"], "version": decision["version"]},
            {"kind": "source_topic", "id": source["id"],
             "source_version": source["source_version"],
             "transcript_version": source["transcript_version"],
             **{k: section[k] for k in ("start_offset", "end_offset", "chapter_id")}}]
    return refs


def batch(s, refs, language="da", **kwargs):
    return s.client.post(PATH, json={"language": language, "items": refs}, **kwargs)


def counts(store):
    with store.connect() as db:
        return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "display_translations", "display_translation_sources",
        ))


def test_display_batch_api_translates_canonical_labels(summary):
    s = summary
    refs = canonical(s)
    before = [s.client.get(f"/v1/inbox/{refs[0]['id']}").json(),
              s.client.get(f"/v1/decisions/{refs[1]['id']}").json(),
              s.memory.store.get_audio(s.source["id"])]
    response = batch(s, refs)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["language"] == "da"
    assert [r["original_title"] for r in result["items"]] == [
        "Review the budget", "Use the budget plan", "Budget planning",
    ]
    for ref, translated in zip(refs, result["items"], strict=True):
        assert {key: translated[key] for key in ref} == ref
        assert translated["title"] == DANISH and len(translated["content_key"]) == 64
        assert not {"evidence", "quote", "source_text", "owner", "user_note"} & translated.keys()
    assert s.calls == [(text, "da") for text in (
        "Review the budget", "Use the budget plan", "Budget planning",
    )]
    assert batch(s, refs).json() == result
    assert len(s.calls) == 3 and counts(s.memory.store) == (3, 3)
    assert s.client.get(f"/v1/inbox/{refs[0]['id']}").json() == before[0]
    assert s.client.get(f"/v1/decisions/{refs[1]['id']}").json() == before[1]
    assert s.memory.store.get_audio(s.source["id"]) == before[2]


def test_persistent_cache_and_duplicates_reuse_current_label_translation(summary):
    from omi_local.display_translation_store import DisplayTranslationStore

    s = summary
    refs = canonical(s)
    result = batch(s, refs + refs).json()
    assert result["items"][:3] == result["items"][3:]
    assert len(s.calls) == 3
    s.memory.store = Store(s.memory.store.root)
    loaded, cached = DisplayTranslationStore(s.memory).load(refs, "da")
    assert len(loaded) == 3 and len(cached) == 3
    assert batch(s, refs).json()["items"] == result["items"][:3]
    assert len(s.calls) == 3


def test_user_override_and_decision_edit_invalidate_old_versions(summary):
    s = summary
    refs = canonical(s)
    original = batch(s, refs).json()
    item = InboxStore(s.memory.store).mutate(refs[0]["id"], Mutation.model_validate({
        "operation_id": str(uuid4()), "expected_version": refs[0]["version"],
        "changes": {"title": "My revised task", "user_note": "Never send this note to the model"},
    }))
    assert batch(s, [refs[0]]).status_code == 409
    refs[0]["version"] = item["version"]
    changed = batch(s, [refs[0]])
    assert changed.status_code == 200, changed.text
    assert changed.json()["items"][0]["original_title"] == "My revised task"
    assert changed.json()["items"][0]["content_key"] != original["items"][0]["content_key"]
    assert all("Never send" not in text for text, _ in s.calls)
    decision = s.client.get(f"/v1/decisions/{refs[1]['id']}").json()
    edited = event(
        s.client, decision, "edit", content=content(s.source, "Choose another plan"),
    ).json()
    assert batch(s, [refs[1]]).status_code == 409
    refs[1]["version"] = edited["version"]
    assert batch(s, [refs[1]]).json()["items"][0]["original_title"] == "Choose another plan"


def test_topic_label_regeneration_changes_content_key_without_changing_evidence(summary):
    s = summary
    refs = canonical(s)
    first = batch(s, [refs[2]]).json()["items"][0]
    store = s.memory.store
    with store.connect() as db:
        details = json.loads(db.execute("SELECT topic_details FROM audio WHERE id=?",
                                        (s.source["id"],)).fetchone()[0])
        details["sections"][0]["title"] = "Revised subject"
        db.execute("UPDATE audio SET topic_details=? WHERE id=?",
                   (json.dumps(details), s.source["id"]))
    assert counts(store) == (0, 0)
    changed = batch(s, [refs[2]]).json()["items"][0]
    assert changed["original_title"] == "Revised subject"
    assert changed["content_key"] != first["content_key"]
    assert {k: changed[k] for k in refs[2]} == refs[2]


@pytest.mark.parametrize("mutation", ["correct", "delete", "expire"])
def test_source_lifecycle_purges_dependent_display_cache(summary, mutation):
    s = summary
    refs = canonical(s)
    assert batch(s, refs).status_code == 200
    store = s.memory.store
    if mutation == "correct":
        store.correct_transcript(s.source["id"], "Corrected evidence")
    elif mutation == "delete":
        store.delete_audio(s.source["id"])
    else:
        store.apply_retention(0, 1, now=utc_now() + timedelta(days=2))
    assert counts(store) == (0, 0)
    assert batch(s, refs).status_code in {404, 409}


@pytest.mark.parametrize("kind", ["inbox", "decision"])
def test_canonical_record_removal_cleans_cached_display(summary, kind):
    s = summary
    refs = canonical(s)
    ref = next(ref for ref in refs if ref["kind"] == kind)
    assert batch(s, [ref]).status_code == 200
    table = "inbox_items" if kind == "inbox" else "decisions"
    with s.memory.store.connect() as db:
        db.execute(f"DELETE FROM {table} WHERE id=?", (ref["id"],))
    assert counts(s.memory.store) == (0, 0)
    assert batch(s, [ref]).status_code == 404


@pytest.mark.parametrize("failure", [
    InferenceError("provider failed"), httpx.ConnectError("offline"), TimeoutError(), "",
    "## Injected\n\nNew topic",
])
def test_failed_batch_is_atomic_and_preserves_originals(summary, failure):
    s = summary
    refs = canonical(s)

    async def fail(text, language):
        if text == "Review the budget":
            return "Translate one label successfully"
        if isinstance(failure, Exception):
            raise failure
        return failure

    s.memory.inference.translate = fail
    response = batch(s, refs)
    assert response.status_code == 502, response.text
    assert response.json()["detail"] == "Display translation failed. Original text is available."
    assert counts(s.memory.store) == (0, 0)
    assert s.memory.store.get_audio(s.source["id"])["text"] == s.source["text"]


@pytest.mark.parametrize("mutation", ["inbox", "decision", "topic", "language"])
def test_canonical_changes_during_inference_reject_whole_batch(summary, mutation):
    s = summary
    refs = canonical(s)
    done = False

    async def change(text, language):
        nonlocal done
        if not done:
            done = True
            with s.memory.store.connect() as db:
                if mutation == "inbox":
                    db.execute("UPDATE inbox_items SET version=version+1 WHERE id=?",
                               (refs[0]["id"],))
                elif mutation == "decision":
                    db.execute("UPDATE decisions SET version=version+1 WHERE id=?",
                               (refs[1]["id"],))
                elif mutation == "topic":
                    db.execute("UPDATE audio SET topic_details='{}' WHERE id=?", (refs[2]["id"],))
                else:
                    s.memory.settings.translation_languages = []
                    s.memory.store.save_settings({"translation_languages": []})
        return DANISH

    s.memory.inference.translate = change
    assert batch(s, refs).status_code == 409
    assert counts(s.memory.store) == (0, 0)


@pytest.mark.parametrize("bad", ["empty", "too_many", "prose", "version", "offsets", "kind"])
def test_invalid_references_never_reach_inference(summary, bad):
    s = summary
    refs = canonical(s)
    if bad == "empty":
        refs = []
    elif bad == "too_many":
        refs = refs * 17
    elif bad == "prose":
        refs[0]["text"] = "Untrusted caller prose"
    elif bad == "version":
        refs[0]["version"] = True
    elif bad == "offsets":
        refs[2]["end_offset"] = 0
    else:
        refs[0]["kind"] = "source_text"
    assert batch(s, refs).status_code == 422
    assert not s.calls


@pytest.mark.parametrize("language", ["xx", "fr", ""])
def test_disallowed_targets_do_not_translate(summary, language):
    s = summary
    assert batch(s, canonical(s), language).status_code == 422
    assert not s.calls


def test_stale_and_unavailable_batch_members_fail_before_any_model_work(summary):
    s = summary
    refs = canonical(s)
    refs[1]["id"] = str(uuid4())
    assert batch(s, refs).status_code == 404
    assert not s.calls and counts(s.memory.store) == (0, 0)
    refs = canonical(s)
    refs[2]["source_version"] = "old"
    assert batch(s, refs).status_code == 409
    refs = canonical(s)
    refs[2]["chapter_id"] = "other-chapter"
    assert batch(s, refs).status_code == 409
    assert not s.calls


def test_profile_boundaries_and_model_identity_isolate_display_translations(summary):
    s = summary
    refs = canonical(s)
    first = batch(s, refs).json()
    profile = s.client.post("/v1/profiles", json={"name": "Other"}).json()
    assert batch(s, refs, headers={"X-Omi-Profile": profile["id"]}).status_code == 404
    assert len(s.calls) == 3
    s.memory.settings.llm_model = "another-model"
    changed = batch(s, refs).json()
    assert changed["items"][0]["content_key"] != first["items"][0]["content_key"]
    assert len(s.calls) == 6


def test_batch_inference_is_globally_bounded_and_has_one_timeout(summary):
    s = summary
    refs = canonical(s)
    # This label creates three prose units in addition to the other two records, so
    # per-record semaphores would exceed the required global concurrency bound.
    with s.memory.store.connect() as db:
        details = json.loads(db.execute("SELECT topic_details FROM audio WHERE id=?",
                                        (s.source["id"],)).fetchone()[0])
        details["sections"][0]["title"] = "Review **budget** before confirming"
        db.execute("UPDATE audio SET topic_details=? WHERE id=?",
                   (json.dumps(details), s.source["id"]))
    active = peak = 0

    async def translate(text, language):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            return DANISH
        finally:
            active -= 1

    s.memory.inference.translate = translate
    assert batch(s, refs).status_code == 200
    assert peak == 3
    with s.memory.store.connect() as db:
        db.execute("DELETE FROM display_translations")
    s.memory.settings.inference_timeout_seconds = 0.001
    assert batch(s, refs).status_code == 502
    assert active == 0 and counts(s.memory.store) == (0, 0)


def test_context_sources_invalidate_labels_for_all_dependent_record_types(summary):
    s = summary
    refs = canonical(s)
    store = s.memory.store
    context = source(store, "Context that informed the canonical records")
    with store.connect() as db:
        job_id = db.execute("SELECT job_id FROM inbox_occurrences WHERE item_id=?",
                            (refs[0]["id"],)).fetchone()[0]
        revision = db.execute("SELECT revision FROM inbox_sources WHERE source_id=?",
                              (context,)).fetchone()[0]
        db.execute("INSERT INTO inbox_dependencies VALUES(?,?,?)", (job_id, context, revision))
        db.execute("INSERT INTO decision_dependencies VALUES(?,?,?)", (
            refs[1]["id"], context, store.get_audio(context)["source_version"],
        ))
        details = json.loads(db.execute("SELECT topic_details FROM audio WHERE id=?",
                                        (refs[2]["id"],)).fetchone()[0])
        details["context"] = "test-context"
        db.execute("UPDATE audio SET topic_details=? WHERE id=?",
                   (json.dumps(details), refs[2]["id"]))
        db.executemany("INSERT INTO recording_topic_sources VALUES(?,?)",
                       [("test-context", context), ("test-context", refs[2]["id"])])
    assert batch(s, refs).status_code == 200
    assert counts(store) == (3, 6)
    store.correct_transcript(context, "Context corrected")
    assert counts(store) == (0, 0)
    assert batch(s, refs).status_code == 409


def test_bounded_cache_and_disabled_inference_with_current_cached_labels(summary, monkeypatch):
    s = summary
    refs = canonical(s)
    assert batch(s, refs).status_code == 200
    s.memory.settings.llm_enabled = False
    assert batch(s, refs).status_code == 200
    assert len(s.calls) == 3
    s.memory.settings.llm_model = "uncached-model"
    assert batch(s, refs).status_code == 409
    assert len(s.calls) == 3
    s.memory.settings.llm_enabled = True
    monkeypatch.setattr("omi_local.display_translation_store.MAX_CACHE", 2)
    assert batch(s, refs, "DA").json()["language"] == "da"
    assert counts(s.memory.store) == (2, 2)


def test_optional_chapter_id_and_inline_links_preserve_canonical_display_shape(summary):
    s = summary
    refs = canonical(s)
    ref = refs[2]
    ref.pop("chapter_id")
    with s.memory.store.connect() as db:
        details = json.loads(db.execute("SELECT topic_details FROM audio WHERE id=?",
                                        (s.source["id"],)).fetchone()[0])
        details["sections"][0]["title"] = "Open [portal](https://example.test) before confirming"
        db.execute("UPDATE audio SET topic_details=? WHERE id=?", (json.dumps(details), ref["id"]))
    terms = {"Open": "Åbn", "portal": "portalen", "before confirming": "før bekræftelse"}

    async def translate(text, language):
        return terms[text]

    s.memory.inference.translate = translate
    value = batch(s, [ref]).json()["items"][0]
    assert value["title"] == "Åbn [portalen](https://example.test) før bekræftelse"
    assert "chapter_id" not in value
    assert value["original_title"] == "Open [portal](https://example.test) before confirming"
