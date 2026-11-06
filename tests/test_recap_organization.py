"""Organize existing agenda sections without asking writers to repeat their work."""

import json
from collections import Counter
from types import SimpleNamespace
from uuid import UUID

import pytest

from omi_local.inference import InferenceError
from omi_local.recap_organization import organize_summary


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Organizer:
    settings = SimpleNamespace(summary_language="en")

    def __init__(self, groups):
        self.groups, self.calls = groups, []

    def for_task(self, task):
        assert task == "summary"
        return self

    async def generate(self, task, evidence):
        self.calls.append((task, json.loads(evidence)))
        return json.dumps({"groups": self.groups})


def paragraphs(text):
    return Counter(line for line in text.splitlines() if line and not line.startswith("#"))


@pytest.mark.anyio
async def test_real_fragmentation_pattern_becomes_parents_without_rewriting_cited_prose():
    titles = ["Release strategy", "Bookmarks", "UI interactions", "Data structure", "Search",
              "Tablet market", "Capacity history"]
    text = "\n\n".join(f"## {title}\nOriginal detail {n}. [{UUID(int=n)}]"
                         for n, title in enumerate(titles, 1))
    model = Organizer([
        {"title": "Analysis tool review", "section_ids": [f"t{n}" for n in range(1, 6)]},
        {"title": "Tablet market discussion", "section_ids": ["t6", "t7"]},
    ])
    result, trace = await organize_summary(model, text)
    assert result.count("\n## ") + result.startswith("## ") == 2
    assert result.count("### ") == 7
    assert paragraphs(result) == paragraphs(text)
    assert trace["topic_count"] == 2 and trace["subtopic_count"] == 7
    assert len(model.calls) == 1
    assert str(UUID(int=1)) not in json.dumps(model.calls[0][1])


@pytest.mark.anyio
@pytest.mark.parametrize("ids", [["t1"], ["t1", "t1", "t2"], ["t1", "t2", "t9"]])
async def test_grouping_must_account_for_each_section_exactly_once(ids):
    model = Organizer([{"title": "Project", "section_ids": ids}])
    with pytest.raises(InferenceError):
        await organize_summary(model, "## Release\nKeep this.\n\n## Search\nAnd this.")
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_singletons_keep_original_titles_and_source_order():
    text = "## Bicycle\nCollect Monday.\n\n## Holiday\nBook Friday."
    model = Organizer([
        {"title": "Renamed holiday", "section_ids": ["t2"]},
        {"title": "Renamed bicycle", "section_ids": ["t1"]},
    ])
    result, _ = await organize_summary(model, text)
    assert result == text


@pytest.mark.anyio
@pytest.mark.parametrize("text", [
    "## Meeting\n### Release\nA detail.\n### Search\nAnother.",
    "## One subject\nOne coherent discussion.", "Plain legacy recap.",
])
async def test_existing_hierarchy_and_simple_recaps_do_not_call_the_model(text):
    model = Organizer([])
    result, trace = await organize_summary(model, text)
    assert result == text and trace["model_calls"] == 0 and not model.calls


def recovery_snapshot(summary, *, prose="same-prose", version="transcript-1", output="clean"):
    return {"summary": summary, "prose_fingerprint": prose, "output_id": output,
            "sources": [{"id": "source-1", "source_version": "audio-1",
                         "transcript_version": version}],
            "generated_at": "2026-11-04T10:00:00+00:00", "included_through": "cutoff",
            "completeness": {"as_of": "original-selection"},
            "evidence": {"pipeline": {"original_writer": True}, "selected": [version]}}


def malformed_heading(level="##"):
    return level + ' {"task":"Translate the heading","evidence":"Meeting"}\nCited prose.'


@pytest.mark.parametrize("level", ["##", "###"])
@pytest.mark.parametrize("legacy", [False, True])
def test_recovers_latest_clean_prose_only_from_the_same_selected_evidence(level, legacy):
    from datetime import date

    from omi_local.recap_organization import recover_recap_prose

    current = recovery_snapshot(malformed_heading(level), output="bad")
    clean = recovery_snapshot("## Product meeting\nOriginal cited prose.", output="latest-clean")
    if legacy:
        clean.pop("prose_fingerprint")
    versions = [
        {"fingerprint": "same-prose", "content": current},
        {"fingerprint": "other", "content": recovery_snapshot("## Wrong policy\nNo.",
                                                               prose="changed-policy")},
        {"fingerprint": "same-prose", "content": recovery_snapshot("## Wrong evidence\nNo.",
                                                                    version="transcript-2")},
        {"fingerprint": "same-prose" if legacy else "organization-v1", "content": clean},
        {"fingerprint": "same-prose", "content": recovery_snapshot("## Older\nOlder prose.")},
    ]
    memory = SimpleNamespace(store=SimpleNamespace(diary_versions=lambda day: versions))
    sources = current["sources"]
    summary, recovered = recover_recap_prose(memory, current, date(2026, 11, 4))
    assert summary == clean["summary"] and recovered == "latest-clean"
    assert current["sources"] is sources and current["output_id"] == "bad"
    assert current["evidence"] == clean["evidence"]


@pytest.mark.parametrize("mismatch", ["policy", "sources", "malformed", "absent"])
def test_malformed_heading_recovery_refuses_unrelated_or_missing_snapshots(mismatch):
    from datetime import date

    from omi_local.recap_organization import recover_recap_prose

    current = recovery_snapshot(malformed_heading(), output="bad")
    candidate = recovery_snapshot("## Product meeting\nOriginal prose.")
    if mismatch == "policy":
        candidate["prose_fingerprint"] = "different-policy"
    elif mismatch == "sources":
        candidate["sources"][0]["source_version"] = "different-source"
    elif mismatch == "malformed":
        candidate["summary"] = malformed_heading()
    versions = [] if mismatch == "absent" else [{"fingerprint": "different", "content": candidate}]
    memory = SimpleNamespace(store=SimpleNamespace(diary_versions=lambda day: versions))
    with pytest.raises(InferenceError):
        recover_recap_prose(memory, current, date(2026, 11, 4))


def test_normal_headings_do_not_read_saved_history_for_recovery():
    from datetime import date

    from omi_local.recap_organization import recover_recap_prose

    current = recovery_snapshot('## Request design\nDiscussed {"task":"x","evidence":"y"}.')
    memory = SimpleNamespace(store=SimpleNamespace(
        diary_versions=lambda day: pytest.fail("Clean headings require no history lookup"),
    ))
    assert recover_recap_prose(memory, current, date(2026, 11, 4)) == (current["summary"], None)


@pytest.mark.anyio
@pytest.mark.parametrize("corrupt_translation", [False, True])
async def test_recovery_runs_before_normalization_and_rejects_new_envelope_headings(
    monkeypatch, corrupt_translation,
):
    from datetime import UTC, datetime

    from omi_local.recap_organization import organize_recap

    clean = recovery_snapshot("## Product meeting\n### Release\nOriginal prose.", output="clean")
    current = recovery_snapshot(malformed_heading(), output="bad")
    current["generated_at"] = "bad-translation-time"
    current["evidence"] = {"pipeline": {"bad_translation": True}}
    sources = current["sources"]
    seen = []

    async def normalize(inference, summary, selected):
        seen.append(summary)
        assert summary == clean["summary"] and selected is sources
        return (malformed_heading() if corrupt_translation else summary), {"model_calls": 0}

    monkeypatch.setattr("omi_local.recap_organization.normalize_source_languages", normalize)
    now = datetime(2026, 11, 5, tzinfo=UTC)
    memory = SimpleNamespace(
        inference=SimpleNamespace(),
        store=SimpleNamespace(diary_versions=lambda day: [
            {"fingerprint": "old-organization", "content": clean},
        ]),
        day_window=lambda day: (now, now),
        evidence_metadata=lambda selected, text, mode, start, end, completeness, trace, counts,
        kind: {"pipeline": trace, "completeness": completeness},
    )
    if corrupt_translation:
        with pytest.raises(InferenceError):
            await organize_recap(memory, current, now.date(), now)
        assert seen == [clean["summary"]]
        return
    result = await organize_recap(memory, current, now.date(), now)
    assert result["summary"] == clean["summary"] and result["sources"] is sources
    assert result["generated_at"] == clean["generated_at"]
    assert result["output_id"] not in ("bad", "clean")
    assert result["topic_organization"]["recovered_from_output_id"] == "clean"
    assert result["evidence"]["pipeline"]["original_writer"]
    assert "bad_translation" not in result["evidence"]["pipeline"]
