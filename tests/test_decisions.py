"""Decision register behavior through the HTTP/archive boundaries."""

import io
import wave
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.settings import Settings

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def client(tmp_path):
    config = Settings(
        data_dir=tmp_path, worker_enabled=False, timezone="Europe/Copenhagen", _env_file=None
    )
    with TestClient(create_app(config)) as session:
        yield session


def source(client, text="We agreed to use A because it works.", at=AT):
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "PCM"))
        wav.writeframes(b"\0\0" * 16000)
    key = str(uuid4())
    store = client.app.state.memory.store
    store.ingest(UUID(key), "test", at, audio.getvalue(), 1)
    store.complete(key, text)
    return client.get(f"/v1/transcripts/{key}").json()


def content(s, text="Use A"):
    anchor = dict(
        source_id=s["id"],
        source_version=s["source_version"],
        quote=s["text"],
        start=0,
        end=len(s["text"]),
    )
    return dict(
        choice=dict(text=text, evidence=[anchor]),
        context=None,
        rationale=None,
        alternatives=[],
        owner_notes="",
        relationships=[],
    )


def create(client, s, text="Use A", state="tentative"):
    response = client.post(
        "/v1/decisions", json=dict(command_id=str(uuid4()), state=state, content=content(s, text))
    )
    assert response.status_code == 200, response.text
    return response.json()


def event(client, record, kind, **extra):
    return client.post(
        f"/v1/decisions/{record['id']}/events",
        json=dict(command_id=str(uuid4()), expected_version=record["version"], type=kind, **extra),
    )


def test_manual_review_edit_and_stale_mutation(client):
    s = source(client)
    d = create(client, s)
    assert d["review_status"] == "proposed" and d["state"] == "tentative"
    confirmed = event(client, d, "confirm").json()
    assert confirmed["review_status"] == "confirmed" and confirmed["state"] == "tentative"
    assert event(client, d, "dismiss").status_code == 409
    edited = event(client, confirmed, "edit", content=content(s, "Use A for now")).json()
    assert edited["id"] == d["id"] and edited["review_status"] == "proposed"
    detail = client.get(f"/v1/decisions/{d['id']}").json()
    assert [v["event"] for v in detail["history"]] == ["created", "confirm", "edit"]
    bad = content(s)
    bad["choice"]["evidence"][0]["quote"] = "invented"
    assert (
        client.post("/v1/decisions", json=dict(command_id=str(uuid4()), content=bad)).status_code
        == 422
    )


def test_revision_chain_is_explicit_and_uses_capture_order(client):
    from datetime import timedelta

    first = create(client, source(client), "Use A", "final")
    first = event(client, first, "confirm").json()
    second = create(
        client, source(client, "We now choose B.", AT + timedelta(days=2)), "Use B", "final"
    )
    assert first["successor_id"] is None
    linked = event(
        client, second, "confirm", predecessor_id=first["id"], predecessor_version=first["version"]
    )
    assert linked.status_code == 200, linked.text
    linked = linked.json()
    old = client.get(f"/v1/decisions/{first['id']}").json()
    assert old["display_state"] == "revised" and old["state"] == "final"
    assert linked["thread_id"] == first["thread_id"] and linked["predecessor_id"] == first["id"]
    assert (
        client.delete(
            f"/v1/decisions/{linked['id']}?expected_version={linked['version']}"
        ).status_code
        == 200
    )
    assert client.get(f"/v1/decisions/{first['id']}").json()["display_state"] == "revised"
    assert client.get("/v1/decisions?day=2026-10-15").json()["items"][0]["id"] == first["id"]


def test_source_correction_requires_reanchor_and_deletion_purges_history(client):
    s = source(client)
    record = event(client, create(client, s), "confirm").json()
    revised = client.patch(f"/v1/transcripts/{s['id']}", json={"text": "We chose B."}).json()
    assert revised["source_version"] != s["source_version"]
    stale = client.get(f"/v1/decisions/{record['id']}").json()
    assert stale["evidence_condition"] == "stale"
    assert event(client, stale, "confirm").status_code == 409
    changed = event(client, stale, "edit", content=content(revised, "Choose B")).json()
    assert changed["evidence_condition"] == "current" and changed["review_status"] == "proposed"
    assert event(client, changed, "confirm").status_code == 200
    assert client.delete(f"/v1/audio/{s['id']}").status_code == 200
    removed = client.get(f"/v1/decisions/{record['id']}").json()
    assert removed["evidence_condition"] == "removed" and removed["content"] is None
    assert all(v["content"] is None for v in removed["history"])
    assert client.get("/v1/decisions?include_dismissed=true").json()["total"] == 0


class Extractor:
    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    async def generate(self, task, evidence):
        import json

        self.calls += 1
        rows = json.loads(evidence)
        if self.callback:
            self.callback()
        return json.dumps(
            {
                "candidates": [
                    {
                        "classification": "decision",
                        "state": "final",
                        "content": content(dict(r, source_version=r["source_version"])),
                    }
                    for r in rows
                ]
            }
        )


def test_bounded_extraction_resume_retry_and_dismissal(client):
    from datetime import timedelta

    for index in range(11):
        source(client, f"We chose item {index}.", AT + timedelta(minutes=index))
    ai = Extractor()
    client.app.state.memory.inference = ai
    request = {"request_id": str(uuid4()), "day": "2026-10-15"}
    first = client.post("/v1/decision-extractions", json=request)
    assert first.status_code == 200, first.text
    first = first.json()
    assert (
        first["processed_sources"] == 8
        and first["total_sources"] == 11
        and first["status"] == "active"
    )
    assert client.post("/v1/decision-extractions", json=request).json()["processed_sources"] == 8
    assert ai.calls == 1
    dismissed = event(client, first["items"][0], "dismiss").json()
    continued = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "run_id": first["id"]}
    ).json()
    assert continued["status"] == "complete" and continued["processed_sources"] == 11
    assert len(continued["items"]) == 11
    again = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    ).json()
    assert again["id"] == first["id"] and ai.calls == 2
    assert client.get(f"/v1/decisions/{dismissed['id']}").json()["review_status"] == "dismissed"


def test_deletion_during_inference_cannot_resurrect(client):
    s = source(client)
    ai = Extractor(lambda: client.app.state.memory.store.delete_audio(s["id"]))
    client.app.state.memory.inference = ai
    result = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    )
    assert result.status_code == 409
    assert client.get("/v1/decisions").json()["total"] == 0


def test_deleted_proposal_stays_suppressed_across_new_scopes(client):
    s = source(client)
    client.app.state.memory.inference = Extractor()
    result = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "source_ids": [s["id"]]}
    ).json()
    d = result["items"][0]
    assert (
        client.delete(f"/v1/decisions/{d['id']}?expected_version={d['version']}").status_code == 200
    )
    result = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    ).json()
    assert result["items"] == []


def test_audio_retention_preserves_decisions_transcript_retention_purges(client):
    from datetime import timedelta

    s = source(client)
    d = create(client, s)
    store = client.app.state.memory.store
    store.apply_retention(1, 0, AT + timedelta(days=2))
    assert client.get(f"/v1/transcripts/{s['id']}").json()["audio_available"] is False
    assert client.get(f"/v1/decisions/{d['id']}").json()["evidence_condition"] == "current"
    store.apply_retention(0, 1, AT + timedelta(days=2))
    d = client.get(f"/v1/decisions/{d['id']}").json()
    assert d["content"] is None and all(v["content"] is None for v in d["history"])


def test_export_and_backup_restore_preserve_record_history(client, tmp_path):
    import importlib.util
    import shutil
    import zipfile
    from pathlib import Path

    d = create(client, source(client))
    d = event(client, d, "confirm").json()
    markdown = client.get("/v1/diary/2026-10-15.md")
    assert markdown.status_code == 200 and "## Decision register" in markdown.text
    assert "Not stated." in markdown.text and d["id"] in markdown.text
    backup = client.app.state.memory.store.export_archive()
    try:
        with zipfile.ZipFile(backup) as archive:
            assert "Use A" in archive.read("diary/2026-10-15.md").decode()
        spec = importlib.util.spec_from_file_location(
            "archive_restore", Path(__file__).parents[1] / "scripts/archive.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        target = tmp_path / "restored"
        module.restore(backup, target, 10000000)
        from omi_local.store import Store

        assert Store(target).decision(d["id"])["review_status"] == "confirmed"
    finally:
        shutil.rmtree(backup.parent)
    s = client.get("/v1/transcripts").json()["items"][0]
    client.patch(f"/v1/transcripts/{s['id']}", json={"text": "Changed choice"})
    stale_export = client.get("/v1/diary/2026-10-15.md")
    assert "Use A" not in stale_export.text


def test_extraction_classification_and_unknown_evidence_are_rejected(client):
    import json

    s = source(client, "We could choose A. I disagree.")

    class NonDecisions:
        async def generate(self, task, evidence):
            return json.dumps(
                {
                    "candidates": [
                        {"classification": classification, "content": content(s)}
                        for classification in ["suggestion", "disagreement", "interpretation"]
                    ]
                }
            )

    client.app.state.memory.inference = NonDecisions()
    request = {"request_id": str(uuid4()), "source_ids": [s["id"]]}
    assert client.post("/v1/decision-extractions", json=request).json()["items"] == []

    class Unknown:
        async def generate(self, task, evidence):
            c = content(s)
            c["choice"]["evidence"][0]["source_id"] = str(uuid4())
            return json.dumps({"candidates": [{"classification": "decision", "content": c}]})

    client.app.state.memory.inference = Unknown()
    assert (
        client.post(
            "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
        ).status_code
        == 502
    )


def test_long_import_is_bounded_and_resumes_fragments(client):
    import json

    source(client, "We chose A. " * 5000)
    sizes = []

    class FragmentExtractor:
        async def generate(self, task, evidence):
            rows = json.loads(evidence)
            sizes.append(sum(len(r["text"]) for r in rows))
            return '{"candidates":[]}'

    client.app.state.memory.inference = FragmentExtractor()
    result = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    ).json()
    assert result["processed_sources"] == 0 and result["processed_source_offset"] == 12000
    while result["next_run_id"]:
        result = client.post(
            "/v1/decision-extractions", json={"request_id": str(uuid4()), "run_id": result["id"]}
        ).json()
    assert result["processed_sources"] == 1 and result["status"] == "complete"
    assert max(sizes) <= 24000 and len(sizes) == 5


def test_dst_day_filter_uses_capture_time_not_insertion_time(client):
    before = source(client, "Before local midnight", datetime(2026, 10, 24, 21, 59, tzinfo=UTC))
    inside = source(client, "Inside long DST day", datetime(2026, 10, 25, 22, 30, tzinfo=UTC))
    first = create(client, inside, "Inside")
    create(client, before, "Before")
    result = client.get("/v1/decisions?day=2026-10-25").json()
    assert [r["id"] for r in result["items"]] == [first["id"]]


def test_command_retries_are_idempotent_and_reused_ids_cannot_change_content(client):
    s = source(client)
    body = {"command_id": str(uuid4()), "state": "tentative", "content": content(s)}
    first = client.post("/v1/decisions", json=body).json()
    assert client.post("/v1/decisions", json=body).json()["id"] == first["id"]
    body["state"] = "final"
    assert client.post("/v1/decisions", json=body).status_code == 409
    assert client.get("/v1/decisions").json()["total"] == 1


def test_conflict_resolution_is_separate_from_confirmation(client):
    d = create(client, source(client))
    conflicted = event(client, d, "conflict").json()
    assert conflicted["evidence_condition"] == "conflicted"
    assert event(client, conflicted, "confirm").status_code == 409
    resolved = event(client, conflicted, "resolve").json()
    assert resolved["evidence_condition"] == "current" and resolved["review_status"] == "proposed"
    assert event(client, resolved, "confirm").json()["review_status"] == "confirmed"


def test_correction_during_inference_is_rejected_even_when_text_unchanged(client):
    s = source(client)
    client.app.state.memory.inference = Extractor(
        lambda: client.app.state.memory.store.correct_transcript(s["id"], s["text"])
    )
    response = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    )
    assert response.status_code == 409 and client.get("/v1/decisions").json()["total"] == 0


def test_deleted_manual_record_suppresses_model_recreation(client):
    s = source(client)
    d = create(client, s)
    client.delete(f"/v1/decisions/{d['id']}?expected_version={d['version']}")
    client.app.state.memory.inference = Extractor()
    result = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    )
    assert result.status_code == 200 and result.json()["items"] == []


def test_unicode_anchors_use_codepoints_and_validate_each_field(client):
    s = source(client, "😀 Vi valgte æble. Rationale: frisk.")
    c = content(s)
    quote = "Vi valgte æble."
    c["choice"]["evidence"][0].update(quote=quote, start=2, end=2 + len(quote))
    c["rationale"] = {
        "text": "Fresh",
        "evidence": [dict(c["choice"]["evidence"][0], quote="made up")],
    }
    assert (
        client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c}).status_code
        == 422
    )
    c["rationale"] = None
    response = client.post("/v1/decisions", json={"command_id": str(uuid4()), "content": c})
    assert response.status_code == 200
    assert response.json()["content"]["choice"]["evidence"][0]["start"] == 2


def test_extraction_preserves_manual_review_and_owner_edits(client):
    s = source(client)
    d = create(client, s)
    d = event(client, d, "confirm").json()
    c = content(s)
    c["owner_notes"] = "My later explanation, not from the recording."
    d = event(client, d, "edit", content=c).json()
    client.app.state.memory.inference = Extractor()
    response = client.post(
        "/v1/decision-extractions", json={"request_id": str(uuid4()), "day": "2026-10-15"}
    )
    assert response.status_code == 200 and response.json()["items"] == []
    assert client.get("/v1/decisions").json()["total"] == 1
    assert (
        client.get(f"/v1/decisions/{d['id']}").json()["content"]["owner_notes"] == c["owner_notes"]
    )


def test_automatic_decisions_resume_and_include_late_historical_transcripts(client, monkeypatch):
    import asyncio
    from datetime import timedelta

    from omi_local.worker import scheduled_decisions

    monkeypatch.setattr("omi_local.worker.utc_now", lambda: AT + timedelta(days=2))
    memory = client.app.state.memory
    for index in range(11):
        source(client, f"We chose item {index}.", AT + timedelta(minutes=index))
    ai = Extractor()
    memory.inference = ai
    asyncio.run(scheduled_decisions(memory))
    asyncio.run(scheduled_decisions(memory))
    records = client.get("/v1/decisions").json()
    assert records["total"] == 11 and ai.calls == 2
    dismissed = event(client, records["items"][0], "dismiss").json()
    asyncio.run(scheduled_decisions(memory))
    assert ai.calls == 2
    late = source(client, "We chose a late addition.", AT + timedelta(minutes=12))
    asyncio.run(scheduled_decisions(memory))
    asyncio.run(scheduled_decisions(memory))
    records = client.get("/v1/decisions", params={"include_dismissed": True}).json()
    assert records["total"] == 12
    assert any(late["id"] == a["source_id"] for r in records["items"]
               for a in r["content"]["choice"]["evidence"])
    assert client.get(f"/v1/decisions/{dismissed['id']}").json()["review_status"] == "dismissed"
    calls = ai.calls
    asyncio.run(scheduled_decisions(memory))
    assert ai.calls == calls
    with memory.store.connect() as db:
        assert db.execute("SELECT count(*) FROM extraction_requests").fetchone()[0] == 0


def test_automatic_long_transcript_continues_after_inference_failure(client, monkeypatch):
    import asyncio
    import json

    from omi_local.worker import scheduled_decisions

    monkeypatch.setattr("omi_local.worker.utc_now", lambda: AT)
    source(client, "We chose A. " * 5000)
    sizes = []

    class FlakyExtractor:
        failed = False

        async def generate(self, task, evidence):
            if not self.failed:
                self.failed = True
                raise RuntimeError("Temporary inference failure")
            sizes.append(sum(len(row["text"]) for row in json.loads(evidence)))
            return '{"candidates":[]}'

    memory = client.app.state.memory
    memory.inference = FlakyExtractor()
    for _ in range(6):
        asyncio.run(scheduled_decisions(memory))
    with memory.store.connect() as db:
        run = db.execute("SELECT * FROM decision_extractions").fetchone()
        assert run["status"] == "complete" and run["cursor"] == 1
        assert db.execute("SELECT count(*) FROM extraction_requests").fetchone()[0] == 0
    assert len(sizes) == 5 and max(sizes) <= 24000
    asyncio.run(scheduled_decisions(memory))
    assert len(sizes) == 5
