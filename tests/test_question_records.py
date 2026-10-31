"""Question ordering and human times come from recording evidence, not invented word timings."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omi_local.domain import stamp
from omi_local.question_records import historical_questions, question_records
from omi_local.recent_questions import RecentAnswer, RecentQuestion

END = datetime(2026, 10, 31, 12, tzinfo=UTC)


def original():
    return {"id": str(uuid4()), "text": "First? Second? Third?",
            "started_at": stamp(END - timedelta(seconds=30)), "ended_at": stamp(END),
            "segments": json.dumps([
                {"start": 0, "end": 5, "text": "First?"},
                {"start": 10, "end": 15, "text": "Second?"},
                {"start": 20, "end": 25, "text": "Third?"},
            ])}


def detected(source):
    return RecentAnswer(question_count=3, answer="Latest answer", questions=[
        RecentQuestion(question=text, source_id=source["id"], quote=text)
        for text in ["First?", "Second?", "Third?"]
    ])


def test_questions_keep_chronological_positions_and_real_segment_times():
    source, job = original(), str(uuid4())
    rows = question_records(detected(source), [source], job, END)
    assert [row["question"] for row in rows] == ["First?", "Second?", "Third?"]
    assert [row["position"] for row in rows] == [0, 1, 2]
    assert [row["asked_at"] for row in rows] == [
        stamp(END - timedelta(seconds=seconds)) for seconds in [30, 20, 10]
    ]
    assert all(row["time_precision"] == "segment" for row in rows)
    assert [row["answer"] for row in rows] == [None, None, "Latest answer"]


def test_same_question_spans_have_stable_ids_across_overlapping_clicks():
    source = original()
    first = question_records(detected(source), [source], str(uuid4()), END)
    second = question_records(detected(source), [source], str(uuid4()), END)
    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert first[0]["job_id"] != second[0]["job_id"]


def test_unaligned_question_has_recording_time_without_inventing_segment_timing():
    source = original()
    answer = RecentAnswer(question_count=1, answer="Unknown", questions=[
        RecentQuestion(question="Unclear?", source_id=source["id"], quote="Not in transcript"),
    ])
    row = question_records(answer, [source], str(uuid4()), END)[0]
    assert row["time_precision"] == "recording"
    assert row["asked_at"] == stamp(END)
    assert row["start_offset"] is None and row["end_offset"] is None


@pytest.mark.parametrize("old_question,expected_answer", [
    ("Third?", "Original saved answer"), ("Different topic?", "Latest answer"),
])
def test_history_backfill_does_not_attach_an_old_answer_to_a_different_question(
    old_question, expected_answer,
):
    source = original()

    async def infer(_):
        return detected(source)

    memory = SimpleNamespace(store=SimpleNamespace(get_audio=lambda _: source),
                             inference=SimpleNamespace(answer_recent=infer))
    job = {"id": str(uuid4()), "parameters": {"end": stamp(END), "duration_seconds": 30},
           "result": {"sources": [{"id": source["id"]}], "question": old_question,
                      "answer": "Original saved answer"}}
    rows = asyncio.run(historical_questions(memory, job))
    assert rows[-1]["answer"] == expected_answer
    assert all(row["answer"] is None for row in rows[:-1])
