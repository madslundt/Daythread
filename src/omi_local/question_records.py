"""Question history uses real transcript provenance, never model-invented timestamps."""

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from .domain import stamp
from .recent_questions import RecentAnswer, RecentQuestion
from .semantic_metadata import segment_anchors

if TYPE_CHECKING:
    from .service import Memory


def question_records(answer: RecentAnswer, sources: list[dict], job_id: str,
                     cutoff: datetime) -> list[dict]:
    from .inference import validate_citations

    originals = {row["id"]: row for row in sources}
    detected = answer.questions
    if not detected and answer.question and sources:
        detected = [RecentQuestion(question=answer.question, source_id=sources[-1]["id"])]
    records, used = [], set()
    for index, question in enumerate(detected):
        source = originals[question.source_id]
        asked = min(datetime.fromisoformat(source["ended_at"]), cutoff)
        precision, left, right = "recording", None, None
        quote = (question.quote or "").strip()
        matches = list(re.finditer(r"\s+".join(re.escape(word) for word in quote.split()),
                                   source["text"])) if quote else []
        if matches:
            left, right = matches[-1].span()
            anchors = [segment for start, end, segment in segment_anchors(source)
                       if start < right and end > left]
            if anchors:
                asked = min(datetime.fromisoformat(source["started_at"])
                            + timedelta(seconds=anchors[0]["start"]), cutoff)
                precision = "segment"
        identity = (f"source:{source['id']}:{left}:{right}" if left is not None
                    else f"job:{job_id}:{index}")
        if identity in used:
            identity += f":{index}"
        used.add(identity)
        short = RecentAnswer.short_question(question.question)
        response = answer.answer if index == len(detected) - 1 else question.answer
        if response:
            response = validate_citations(response, sources)
        records.append({
            "id": str(uuid5(NAMESPACE_URL, "daythread-question:" + identity)),
            "job_id": job_id, "question": short,
            "position": index,
            "answer": response,
            "asked_at": stamp(asked), "time_precision": precision,
            "source_id": source["id"], "start_offset": left, "end_offset": right,
        })
    return records


async def historical_questions(memory: Memory, job: dict) -> list[dict]:
    from .wearable_actions import excerpt

    saved = job["result"]
    cutoff = datetime.fromisoformat(job["parameters"]["end"])
    start = cutoff - timedelta(seconds=job["parameters"]["duration_seconds"])
    originals = [memory.store.get_audio(source["id"]) for source in saved.get("sources", [])]
    originals = [row for row in originals if row and row.get("text")]
    selected = [excerpt(row, start, cutoff) for row in originals]
    selected = [row for row in selected if row["text"].strip()]
    if not selected:
        return []
    answer = await memory.inference.answer_recent(selected)
    records = question_records(answer, originals, job["id"], cutoff)
    old_question = saved.get("question") or saved.get("text", "").partition("\n")[0]
    old_answer = saved.get("answer") or saved.get("text", "").partition("\n")[2]
    for record in records:
        same = re.sub(r"\W+", "", old_question).casefold() == re.sub(
            r"\W+", "", record["question"],
        ).casefold()
        if same and old_answer:
            record["answer"] = old_answer
    return records
