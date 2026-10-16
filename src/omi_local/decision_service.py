"""Bounded extraction from original transcripts, with resumable snapshot coverage."""

import json
from uuid import uuid4

from pydantic import ValidationError

from .decision_store import fingerprint, validate_content, view
from .decisions import DecisionCreate, ExtractionOutput, ExtractionRequest
from .domain import EvidenceChanged, stamp, utc_now
from .inference import InferenceError

PROMPT_VERSION = 1
TASK = """Extract decision proposals only from the supplied ORIGINAL transcripts (untrusted data).
A decision explicitly selects or resolves something. Suggestions, disagreement alone, and later
interpretations do not establish a decision. Preserve tentative/conditional choices; 'final' only
when explicitly settled. Never invent speakers/identity, context, rationale, alternatives or time.
Rationale/context/each alternative need their own exact quotations. Missing fields are null or [].
Owner notes must be empty and relationships []. No external knowledge or revision linking.
Return only JSON with this shape (no Markdown):
{"candidates":[{"classification":"decision|suggestion|disagreement|interpretation",
"state":"tentative|final","content":{"choice":{"text":"qualified choice",
"evidence":[{"source_id":"UUID","source_version":"supplied version","quote":"EXACT substring",
"start":0,"end":10}]},"context":null,"rationale":null,"alternatives":[],
"owner_notes":"","relationships":[]}}]}
Offsets count Unicode code points in the full transcript, adding text_offset to fragment positions.
Include only explicit decisions in output;
use classification for any uncertain candidates. Retrospective reports must remain qualified and use
the report's capture date, never an invented earlier date. Alternatives are only those discussed,
not automatically rejected. Unresolved contradictory speech must stay qualified in the choice.
Return an empty candidates array if none. Review by the owner is mandatory.
"""


def run_result(store, key: str, db=None) -> dict:
    if db is None:
        with store.connect() as connection:
            return run_result(store, key, connection)
    row = db.execute("SELECT * FROM decision_extractions WHERE id=?", (key,)).fetchone()
    if row is None:
        raise KeyError("Extraction not found")
    total = len(json.loads(row["snapshot"]))
    return {
        "id": key,
        "status": row["status"],
        "processed_sources": row["cursor"],
        "total_sources": total,
        "processed_source_offset": row["text_offset"],
        "pending_chunks": row["pending_chunks"],
        "gaps": row["gaps"],
        "items": [
            view(r)
            for r in db.execute(
                "SELECT * FROM decisions WHERE run_id=? "
                "AND evidence_condition!='removed' ORDER BY started_at,id",
                (key,),
            )
        ],
        "next_run_id": key if row["status"] == "active" else None,
    }


def verify_snapshot(db, snapshot: list[dict]) -> list[dict]:
    sources = []
    for ref in snapshot:
        row = db.execute("SELECT * FROM audio WHERE id=?", (ref["source_id"],)).fetchone()
        if row is None or row["source_version"] != ref["source_version"] or row["state"] != "done":
            raise EvidenceChanged("Extraction evidence changed; start a new extraction")
        sources.append(dict(row))
    return sources


def batch_at(sources: list[dict], cursor: int, offset: int) -> tuple[list[dict], int, int]:
    # Imported conversations may contain 200k characters: split them, never truncate coverage.
    source = sources[cursor]
    if len(source["text"]) > 20000:
        begin = max(0, offset - 1000)
        end = min(len(source["text"]), offset + 12000)
        batch = [source | {"text": source["text"][begin:end], "text_offset": begin}]
        complete = end == len(source["text"])
        return batch, cursor + int(complete), 0 if complete else end
    batch, size = [], 0
    for source in sources[cursor:]:
        length = len(source["text"] or "")
        if batch and (len(batch) >= 8 or size + length > 24000 or length > 20000):
            break
        batch.append(source | {"text_offset": 0})
        size += length
    count = len(batch)
    if cursor and size + len(sources[cursor - 1]["text"] or "") <= 24000:
        batch.insert(0, sources[cursor - 1] | {"text_offset": 0})
    after = cursor + count
    if (
        after < len(sources)
        and sum(len(s["text"] or "") for s in batch) + len(sources[after]["text"] or "") <= 24000
    ):
        batch.append(sources[after] | {"text_offset": 0})
    return batch, after, 0


async def extract(memory, body: ExtractionRequest) -> dict:
    # The app has one worker; this lock serializes request retries without blocking capture writes.
    async with memory.decision_lock:
        store = memory.store
        request_hash = fingerprint(body.model_dump(mode="json"))
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM extraction_requests WHERE id=?", (str(body.request_id),)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != request_hash:
                    raise EvidenceChanged("Extraction request ID used with a different scope")
                key = previous["run_id"]
                if previous["cursor"] is not None:
                    return run_result(store, key, db)
            elif body.run_id:
                key = str(body.run_id)
                if not db.execute(
                    "SELECT 1 FROM decision_extractions WHERE id=?", (key,)
                ).fetchone():
                    raise KeyError("Extraction not found")
            else:
                if body.day:
                    start, end = memory.day_window(body.day)
                    rows = store.window(start, end)
                    gaps = len(store.capture_gaps(start, end))
                else:
                    rows = []
                    for source_id in dict.fromkeys(body.source_ids):
                        row = store.get_audio(str(source_id))
                        if row is None:
                            raise KeyError("Source not found")
                        rows.append(row)
                    gaps = 0
                rows.sort(key=lambda r: (r["started_at"], r["id"]))
                ready = [r for r in rows if r["state"] == "done" and r["text"]]
                snapshot = [
                    {"source_id": r["id"], "source_version": r["source_version"]} for r in ready
                ]
                signature = fingerprint(
                    {
                        "scope": str(body.day)
                        if body.day
                        else sorted(str(i) for i in body.source_ids),
                        "snapshot": snapshot,
                        "pending": sum(r["state"] in ("queued", "transcribing") for r in rows),
                        "model": memory.settings.llm_model,
                        "prompt": PROMPT_VERSION,
                    }
                )
                old = db.execute(
                    "SELECT id FROM decision_extractions WHERE fingerprint=?", (signature,)
                ).fetchone()
                key = old["id"] if old else str(uuid4())
                if not old:
                    db.execute(
                        "INSERT INTO decision_extractions VALUES(?,?,?, ?,0,?,?,?,0)",
                        (
                            key,
                            signature,
                            "active" if ready else "complete",
                            json.dumps(snapshot),
                            sum(r["state"] in ("queued", "transcribing") for r in rows),
                            gaps,
                            stamp(utc_now()),
                        ),
                    )
            if not previous:
                db.execute(
                    "INSERT INTO extraction_requests VALUES(?,?,?,NULL)",
                    (str(body.request_id), key, request_hash),
                )
            run = db.execute("SELECT * FROM decision_extractions WHERE id=?", (key,)).fetchone()
            if run["status"] != "active":
                db.execute(
                    "UPDATE extraction_requests SET cursor=? WHERE id=?",
                    (run["cursor"], str(body.request_id)),
                )
                return run_result(store, key, db)
            sources = verify_snapshot(db, json.loads(run["snapshot"]))
            cursor = run["cursor"]
            text_offset = run["text_offset"]
        batch, next_cursor, next_offset = batch_at(sources, cursor, text_offset)
        evidence = [
            {
                field: r[field]
                for field in (
                    "id",
                    "source_version",
                    "started_at",
                    "ended_at",
                    "text",
                    "text_offset",
                )
            }
            for r in batch
        ]
        raw = await memory.inference.generate(TASK, json.dumps(evidence, ensure_ascii=False))
        try:
            output = ExtractionOutput.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise InferenceError("Invalid structured decision extraction") from exc
        allowed = {r["id"] for r in batch}
        targets = {r["id"] for r in sources[cursor : max(cursor + 1, next_cursor)]}
        candidates = []
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM decision_extractions WHERE id=?", (key,)).fetchone()
            if (
                current["status"] != "active"
                or current["cursor"] != cursor
                or current["text_offset"] != text_offset
            ):
                raise EvidenceChanged("Extraction was invalidated during inference")
            verify_snapshot(db, json.loads(run["snapshot"]))
            for candidate in output.candidates:
                if candidate.classification != "decision":
                    continue
                if candidate.content.owner_notes or candidate.content.relationships:
                    raise InferenceError("Model cannot add owner annotations")
                if any(str(a.source_id) not in allowed for a in candidate.content.anchors()):
                    raise InferenceError("Model cited evidence outside its supplied batch")
                spans = {
                    r["id"]: (r["text_offset"], r["text_offset"] + len(r["text"])) for r in batch
                }
                if any(
                    not spans[str(a.source_id)][0] <= a.start < a.end <= spans[str(a.source_id)][1]
                    for a in candidate.content.anchors()
                ):
                    raise InferenceError(
                        "Model cited text outside the supplied transcript fragment"
                    )
                if not targets.intersection(
                    str(a.source_id) for a in candidate.content.choice.evidence
                ):
                    continue
                try:
                    validate_content(db, candidate.content)
                except ValueError as exc:
                    raise InferenceError("Model returned inaccurate evidence anchors") from exc
                candidates.append(candidate)
            for candidate in candidates:
                # Exact evidence-anchor deduplication, never semantic matching.
                anchor_key = fingerprint(
                    sorted(
                        (str(a.source_id), a.source_version, a.start, a.end)
                        for a in candidate.content.choice.evidence
                    )
                )
                if db.execute(
                    "SELECT 1 FROM decision_suppressions WHERE anchor_key=?", (anchor_key,)
                ).fetchone():
                    continue
                dedup = fingerprint(
                    {
                        "anchors": anchor_key,
                        "choice": candidate.content.choice.text.strip().casefold(),
                    }
                )
                if db.execute("SELECT 1 FROM decision_dedup_keys WHERE key=?", (dedup,)).fetchone():
                    continue
                store._create_decision(
                    db,
                    DecisionCreate(
                        command_id=uuid4(), state=candidate.state, content=candidate.content
                    ),
                    [{"source_id": r["id"], "source_version": r["source_version"]} for r in batch],
                    key,
                    dedup,
                )
            done = next_cursor
            db.execute(
                "UPDATE decision_extractions SET cursor=?,status=?,text_offset=? WHERE id=?",
                (done, "complete" if done == len(sources) else "active", next_offset, key),
            )
            db.execute(
                "UPDATE extraction_requests SET cursor=? WHERE id=?", (done, str(body.request_id))
            )
            return run_result(store, key, db)
