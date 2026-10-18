"""Summary translations share transcript settings without replacing original output evidence."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.completeness import revision
from omi_local.domain import utc_now
from omi_local.inference import InferenceError
from omi_local.profiles import DEFAULT_PROFILE
from omi_local.settings import Settings
from omi_local.store import Store

ENGLISH = "We should review the budget before the meeting tomorrow."
DANISH = ("Vi skal sende forslaget til kunden i morgen. "
          "Det er vigtigt, at alle oplysninger er korrekte.")


@pytest.fixture
def summary(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, translation_languages=["da"],
                        _env_file=None)
    with TestClient(create_app(settings)) as client:
        memory = client.app.state.memory
        source_id, output_id = str(uuid4()), str(uuid4())
        memory.store.ingest(source_id, "test", utc_now(), b"test", 30)
        memory.store.complete(source_id, ENGLISH)
        source = memory.store.get_audio(source_id)
        evidence = {"selected": [{"id": source_id, "revision": revision(source),
                                  "source_version": source["source_version"],
                                  "transcript_version": source["transcript_version"]}]}
        text = f"## Budget\n\n{ENGLISH} [{source_id}]"
        memory.store.save_evidence(output_id, evidence, [source], summary=text)
        calls = []

        async def translate(text, language):
            calls.append((text, language))
            return f"## Budget\n\n{DANISH}"

        memory.inference.translate = translate
        yield SimpleNamespace(client=client, memory=memory, source=source, id=output_id,
                              evidence=evidence, text=text, calls=calls,
                              path=f"/v1/outputs/{output_id}/translation")


def test_explicit_translation_is_cached_separately_and_preserves_original(summary):
    s = summary
    result = s.client.get(s.path).json()
    assert result["available_languages"] == ["da"]
    assert result["translation"] is None
    assert result["already_in_target_language"] is False
    assert not s.calls
    translated = s.client.post(s.path).json()
    assert translated["translation"]["summary_version"] == result["summary_version"]
    assert s.calls == [(s.text, "da")]
    assert s.client.post(s.path).json() == translated
    assert s.client.get(s.path).json() == translated
    assert len(s.calls) == 1
    reopened = Store(s.memory.store.root)
    source = reopened.summary_translation_source(s.id)
    assert source["text"] == s.text
    assert reopened.summary_translation(source, "DA") == translated["translation"]
    assert s.memory.store.get_audio(s.source["id"])["text"] == ENGLISH
    assert s.memory.store.evidence(s.id) == s.evidence | {"changed_source_ids": []}


def test_settings_languages_control_targets_independently_of_summary_language(summary):
    s = summary
    s.client.post(s.path)
    s.client.patch("/v1/settings", json={"translation_languages": ["en", "da"],
                                        "summary_language": "fr"})
    assert s.client.get(s.path).json()["already_in_target_language"] is True
    assert s.client.post(s.path).json()["translation"] is None
    assert s.client.get(s.path, params={"language": "DA"}).json()["translation"]
    assert s.client.post(s.path, params={"language": "xx"}).status_code == 422
    assert s.client.post(s.path, params={"language": "fr"}).status_code == 422
    s.client.patch("/v1/settings", json={"translation_languages": []})
    assert s.client.get(s.path).json()["available_languages"] == []
    assert s.client.post(s.path, params={"language": "fr"}).status_code == 422
    assert s.client.post(s.path).status_code == 409
    assert len(s.calls) == 1


@pytest.mark.parametrize("share,recommended", [(0.89, "da"), (0.9, None), (0.91, None)])
def test_mixed_summary_recommends_enabled_dominant_only_below_90_percent(
    summary, monkeypatch, share, recommended,
):
    s = summary
    s.client.patch("/v1/settings", json={"translation_languages": ["en", "da"]})
    analysis = {"dominant_language": "da", "dominant_language_share": share,
                "language_shares": {"da": share, "en": 1 - share}}
    monkeypatch.setattr("omi_local.summary_translation.analyze_transcript_languages",
                        lambda text: analysis)
    result = s.client.get(s.path).json()
    assert result["language_analysis"] == analysis
    assert result["recommended_language"] == recommended
    assert result["language"] == (recommended or "en")
    assert ("da" in result["redundant_languages"]) is (share >= 0.9)
    assert not s.calls
    # An explicit enabled choice wins over the recommendation.
    assert s.client.get(s.path, params={"language": "en"}).json()["language"] == "en"


def test_mixed_summary_analysis_ignores_citations_and_respects_enabled_targets(summary):
    s = summary
    text = f"## Planer\n\n{DANISH * 3}\n\n{ENGLISH} [{s.source['id']}]"
    output_id = str(uuid4())
    path = f"/v1/outputs/{output_id}/translation"
    s.memory.store.save_evidence(output_id, s.evidence, [s.source], summary=text)
    result = s.client.get(path).json()
    assert result["language_analysis"]["dominant_language"] == "da"
    assert result["language_analysis"]["dominant_language_share"] < 0.9
    assert result["recommended_language"] == "da"
    assert not s.calls
    translated = s.client.post(path).json()
    assert translated["recommended_language"] == "da"
    assert translated["translation"]["language"] == "da"
    assert s.memory.store.summary_translation_source(output_id)["text"] == text
    s.client.patch("/v1/settings", json={"translation_languages": ["en"]})
    result = s.client.get(path).json()
    assert result["recommended_language"] is None
    assert result["available_languages"] == ["en"]


@pytest.mark.parametrize("mutation", ["correct", "delete", "expire"])
def test_changed_source_purges_translations_and_rejects_publication(summary, mutation):
    s = summary

    async def translate(text, language):
        if mutation == "correct":
            s.memory.store.correct_transcript(s.source["id"], "Corrected evidence")
        elif mutation == "delete":
            s.memory.store.delete_audio(s.source["id"])
        else:
            s.memory.store.apply_retention(0, 1, now=utc_now() + timedelta(days=2))
        return DANISH

    s.memory.inference.translate = translate
    assert s.client.post(s.path).status_code in {404, 409}
    with s.memory.store.connect() as db:
        assert not db.execute("SELECT * FROM summary_translations").fetchall()
        assert not db.execute("SELECT * FROM summary_outputs").fetchall()


@pytest.mark.parametrize("mutation", ["correct", "delete", "expire", "remove_output"])
def test_invalidated_output_cleans_existing_cached_translation(summary, mutation):
    s = summary
    assert s.client.post(s.path).status_code == 200
    if mutation == "correct":
        s.memory.store.correct_transcript(s.source["id"], "Corrected evidence")
    elif mutation == "delete":
        s.memory.store.delete_audio(s.source["id"])
    elif mutation == "expire":
        s.memory.store.apply_retention(0, 1, now=utc_now() + timedelta(days=2))
    else:
        with s.memory.store.connect() as db:
            db.execute("DELETE FROM output_evidence WHERE id=?", (s.id,))
    assert s.client.get(s.path).status_code in {404, 409}
    with s.memory.store.connect() as db:
        assert not db.execute("SELECT * FROM summary_translations").fetchall()


def test_removed_language_during_inference_rejects_translation(summary):
    s = summary

    async def translate(text, language):
        s.memory.settings.translation_languages = []
        s.memory.store.save_settings({"translation_languages": []})
        return DANISH

    s.memory.inference.translate = translate
    response = s.client.post(s.path)
    assert response.status_code == 409
    with s.memory.store.connect() as db:
        assert db.execute("SELECT language FROM summary_translations").fetchone() is None


@pytest.mark.parametrize("failure", [InferenceError("failed"), httpx.ConnectError("offline"),
                                    TimeoutError(), ""])
def test_failures_preserve_original_and_never_cache_partial_output(summary, failure):
    s = summary

    async def translate(text, language):
        if isinstance(failure, Exception):
            raise failure
        return failure

    s.memory.inference.translate = translate
    response = s.client.post(s.path)
    assert response.status_code == 502
    assert response.json()["detail"] == "Translation failed. The original summary is available."
    assert s.memory.store.summary_translation_source(s.id)["text"] == s.text
    assert s.client.get(s.path).json()["translation"] is None


def test_summary_change_during_inference_rejects_old_translation(summary):
    s = summary

    async def translate(text, language):
        with s.memory.store.connect() as db:
            db.execute("UPDATE summary_outputs SET text=? WHERE id=?", (DANISH, s.id))
        return DANISH

    s.memory.inference.translate = translate
    assert s.client.post(s.path).status_code == 409


def test_chunking_and_repeated_clicks_only_translate_once(summary):
    s = summary
    with s.memory.store.connect() as db:
        db.execute("UPDATE summary_outputs SET text=? WHERE id=?", (ENGLISH * 150, s.id))

    async def clicks():
        from omi_local.summary_translation import generate_summary_translation

        return await asyncio.gather(*[
            generate_summary_translation(s.memory, UUID(s.id))
            for _ in range(2)
        ])

    results = asyncio.run(clicks())
    assert results[0] == results[1]
    assert "".join(text for text, _ in s.calls) == ENGLISH * 150
    assert len(s.calls) > 1
    assert all(len(text) <= 4000 for text, _ in s.calls)


def test_preexisting_saved_recap_can_be_translated(summary):
    s = summary
    with s.memory.store.connect() as db:
        db.execute("DELETE FROM summary_outputs WHERE id=?", (s.id,))
        db.execute("DELETE FROM summary_output_sources WHERE output_id=?", (s.id,))
        db.execute("INSERT INTO diary_versions VALUES(?,?,?,?,?)",
                   (s.id, "2026-10-18", "legacy", json.dumps({"summary": s.text}), "now"))
    assert s.client.post(s.path).status_code == 200
    with s.memory.store.connect() as db:
        db.execute("DELETE FROM diary_versions WHERE id=?", (s.id,))
        assert not db.execute("SELECT * FROM summary_translations").fetchall()


def test_direct_window_summary_is_saved_for_translation(summary):
    s = summary

    async def summarize(rows, purpose):
        return ENGLISH

    s.memory.inference = SimpleNamespace(summarize=summarize)
    result = asyncio.run(s.memory.window(utc_now() - timedelta(minutes=5),
                                        utc_now() + timedelta(minutes=5), "rewind"))
    assert s.memory.store.summary_translation_source(result["output_id"])["text"] == ENGLISH


def test_durable_translation_job_reports_activity_notifies_and_tracks_sources(summary):
    s = summary
    jobs = s.client.app.state.profiles.runtimes[DEFAULT_PROFILE].jobs
    job_id = str(uuid4())
    response = s.client.post("/v1/jobs", json={"id": job_id, "kind": "summary_translation",
                                              "parameters": {"output_id": s.id}})
    assert response.status_code == 202

    async def translate(text, language):
        assert jobs.observation()["stages"] == {"translating": 1}
        return DANISH

    s.memory.inference.translate = translate
    assert asyncio.run(jobs.process_one())
    job = s.client.get(f"/v1/jobs/{job_id}").json()
    assert job["state"] == "completed"
    assert job["result"]["translation"]["text"] == DANISH
    jobs.notify_completed()
    notice = s.memory.store.notifications_after(0)["notifications"][0]
    assert notice["kind"] == "summary_translation"
    assert "Summary" in notice["body"]
    s.memory.store.correct_transcript(s.source["id"], "Correction")
    assert s.client.get(f"/v1/jobs/{job_id}").json()["result"] is None


def test_preexisting_saved_rewind_job_can_be_translated(summary):
    s = summary
    with s.memory.store.connect() as db:
        db.execute("DELETE FROM summary_outputs WHERE id=?", (s.id,))
        db.execute("DELETE FROM summary_output_sources WHERE output_id=?", (s.id,))
        db.execute(
            "INSERT INTO generation_jobs(id,kind,parameters,state,result,created_at) "
            "VALUES(?,'rewind','{}','completed',?,'now')",
            (str(uuid4()), json.dumps({"output_id": s.id, "summary": s.text})),
        )
    assert s.client.post(s.path).status_code == 200
    assert s.calls == [(s.text, "da")]
