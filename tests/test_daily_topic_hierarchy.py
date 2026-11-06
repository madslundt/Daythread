"""A meeting's useful details remain discoverable beneath their shared subject."""

import json
import re
from uuid import uuid4

import httpx
import pytest

from omi_local.daily_synthesis import note_items, parse_plan
from omi_local.inference import Inference, InferenceError
from omi_local.settings import Settings
from omi_local.summary_coherence import request, validate_edit


@pytest.fixture
def anyio_backend():
    return "asyncio"


def meeting_plan():
    return {"title": "NNCD.SAR meeting", "scope": "The connected product review",
            "items": ["n1", "n2"], "subtopics": [
                {"title": "Release", "scope": "Release timing", "items": ["n1"]},
                {"title": "Bookmarks", "scope": "Sharing analysis state", "items": ["n2"]},
            ]}


def test_plan_preserves_optional_subtopics():
    topic = meeting_plan()
    assert parse_plan(json.dumps({"topics": [topic]}), {"n1", "n2"}) == [topic]


def test_prepared_subtopics_keep_their_parent_context_in_later_planning():
    key = str(uuid4())
    items = note_items([{"id": key, "text": (
        f"## NNCD.SAR meeting\n### Release\nTwo weeks. [{key}]\n"
        f"### Bookmarks\nShare a URL. [{key}]\n## Bicycle\nCollect Monday. [{key}]"
    )}])
    assert [item["context"] for item in items] == ["Release", "Bookmarks", "Bicycle"]
    assert [item["parent_context"] for item in items] == [
        "NNCD.SAR meeting", "NNCD.SAR meeting", "Bicycle",
    ]


@pytest.mark.parametrize("change", ["unknown", "missing", "nested", "duplicate", "single"])
def test_subtopics_cannot_lose_evidence_or_introduce_invalid_structure(change):
    topic = meeting_plan()
    if change == "unknown":
        topic["subtopics"][0]["items"] = ["n3"]
    elif change == "missing":
        topic["subtopics"][1]["items"] = ["n1"]
    elif change == "nested":
        topic["subtopics"][0]["subtopics"] = [topic["subtopics"][1]]
    elif change == "duplicate":
        topic["subtopics"][1]["title"] = "Release"
    else:
        topic["subtopics"] = topic["subtopics"][:1]
    with pytest.raises(InferenceError):
        parse_plan(json.dumps({"topics": [topic]}), {"n1", "n2", "n3"})


@pytest.mark.anyio
async def test_daily_review_renders_meeting_once_and_writes_each_subtopic_with_scoped_sources(
    tmp_path,
):
    ids = [str(uuid4()) for _ in range(3)]
    notes = [{"id": key, "text": f"## Draft detail\n{fact} [{key}]"}
             for key, fact in zip(ids, [
                 "NNCD.SAR release is planned in two weeks.",
                 "NNCD.SAR bookmarks share analysis state via a URL.",
                 "Remember to collect the bicycle Monday.",
             ], strict=True)]
    writers = []

    def provider(req):
        payload = json.loads(json.loads(req.content)["messages"][1]["content"])
        task = payload["task"]
        if task.startswith("Review the complete daily topic plan"):
            topics = [meeting_plan(), {"title": "Bicycle collection", "scope": "Monday errand",
                                       "items": ["n3"]}]
        elif task.startswith("Organize useful recorded"):
            rows = json.loads(payload["evidence"])
            topics = rows["proposed_topics"] if isinstance(rows, dict) else [
                {"title": f"Detail {n}", "scope": row["text"], "items": [row["id"]]}
                for n, row in enumerate(rows)]
        else:
            writers.append(payload)
            refs = re.findall(r"\[([0-9a-f-]{36})\]", payload["evidence"])
            assert len(set(refs)) == 1
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": f"Supported detail. [{refs[0]}]"}}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"topics": topics})}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result, trace = await Inference(Settings(data_dir=tmp_path, _env_file=None), client
                                       ).summarize_daily_traced(notes, "a daily recap")
    assert re.findall(r"^## (.+)$", result, re.M) == ["NNCD.SAR meeting", "Bicycle collection"]
    assert re.findall(r"^### (.+)$", result, re.M) == ["Release", "Bookmarks"]
    assert len(writers) == 3 and all(key in result for key in ids)
    assert trace["topic_count"] == 2 and trace["subtopic_count"] == 2


def test_coherence_review_accepts_parent_with_only_cited_subtopics():
    key = str(uuid4())
    draft = (f"## NNCD.SAR meeting\n### Release\nPlanned in two weeks. [{key}]\n\n"
             f"### Bookmarks\nShare a URL. [{key}]\n\n## Bicycle\nCollect Monday. [{key}]")
    sources = [{"id": key, "text": "The complete original discussion."}]
    task, evidence, _size, reason = request(sources, draft, "daily recap", "en")
    assert reason is None and "subtopic" in task and evidence
    assert validate_edit(draft, sources, draft) == draft


@pytest.mark.parametrize("edited", [
    "### Orphan\nFact [source]\n## Parent\nFact [source]",
    "## Empty\n## Parent\nFact [source]",
    "## Parent\n### Empty\n### Child\nFact [source]",
    "## Parent\n#### Too deep\nFact [source]",
])
def test_coherence_review_rejects_orphan_empty_and_deeper_topics(edited):
    key = str(uuid4())
    source = {"id": key, "text": "Original fact."}
    draft = f"## Parent\n### Child\nFact [{key}]"
    with pytest.raises(InferenceError):
        validate_edit(edited.replace("[source]", f"[{key}]"), [source], draft)
