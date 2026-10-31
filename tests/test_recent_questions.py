"""Count all distinct recent questions without revealing earlier question contents."""

import asyncio
import json

import httpx
import pytest
from test_generation_jobs import jobs as jobs
from test_wearable_actions import request, run, source

from omi_local.inference import Inference
from omi_local.inference_errors import InferenceError
from omi_local.recent_questions import RecentAnswer
from omi_local.wearable_actions import wearable_notice


@pytest.mark.parametrize("count,question,answer", [
    (3, "Who painted the Mona Lisa?", "Leonardo da Vinci."),
    (1, "Who painted the Mona Lisa?", "Leonardo da Vinci."),
    (0, None, "No clear question was found in the recent audio."),
])
def test_notification_counts_questions_but_reveals_only_latest(jobs, count, question, answer):
    reply = {"question_count": count, "question": question, "answer": answer}

    async def generate():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(reply)}}]},
        ))) as client:
            jobs.memory.inference = Inference(jobs.memory.settings, client)
            transcript = {
                3: "What is 2+2? What is Spain's capital? Who painted the Mona Lisa?",
                1: "Who painted the Mona Lisa? I mean, who was its painter?",
                0: "We are standing in the kitchen.",
            }[count]
            source(jobs, transcript)
            body = request()
            jobs.enqueue(body)
            await jobs.process_one()
            saved = jobs.get(str(body.id))
            jobs.notify_completed()
            return saved, jobs.store.notifications_after(0)["notifications"]

    saved, notices = asyncio.run(generate())
    assert saved["state"] == "completed"
    result = saved["result"]
    assert result["question_count"] == count
    assert result["question"] == question
    assert result["answer"] == answer
    text = (question + "\n" if question else "") + answer
    assert result["text"] == text
    assert notices[0]["body"] == text
    assert notices[0]["title"].startswith(f"{count} question" + ("s" if count != 1 else ""))
    assert "Spain" not in notices[0]["body"] and "2+2" not in notices[0]["body"]


@pytest.mark.parametrize("reply", [
    {"question_count": True, "question": "Latest?", "answer": "Yes."},
    {"question_count": -1, "question": "Latest?", "answer": "Yes."},
    {"question_count": 1.5, "question": "Latest?", "answer": "Yes."},
    {"question_count": "3", "question": "Latest?", "answer": "Yes."},
    {"question_count": 0, "question": "Latest?", "answer": "Yes."},
    {"question_count": 2, "question": None, "answer": "Yes."},
    {"question": "Latest?", "answer": "Yes."},
    {"question_count": 1, "question": "Latest?", "answer": ""},
    {"question_count": 1, "question": "Latest?", "answer": "Yes.", "questions": ["Earlier?"]},
    "An unstructured answer without a count",
])
def test_invalid_model_response_does_not_invent_a_question_count(jobs, reply):
    async def answer():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(reply)}}]},
        ))) as client:
            return await Inference(jobs.memory.settings, client).answer_recent([
                {"id": "source", "text": "Latest?"},
            ])

    with pytest.raises(InferenceError, match="Invalid recent question response"):
        asyncio.run(answer())


def test_no_audio_reports_zero_questions_without_inference(jobs):
    saved = run(jobs, request())
    assert saved["state"] == "completed"
    assert saved["result"]["question_count"] == 0
    assert saved["result"]["question"] is None
    jobs.notify_completed()
    notice = jobs.store.notifications_after(0)["notifications"][0]
    assert notice["title"] == "0 questions · No question found"
    assert notice["body"] == "No transcribed speech is available in this window."


def test_count_remains_visible_when_answer_preview_is_truncated():
    title, body = wearable_notice({"action": "answer", "question_count": 3,
                                   "text": "Latest question?\n" + "A long answer. " * 100})
    assert title == "3 questions · Latest answer"
    assert body.startswith("Latest question?\n")
    assert len(body) <= 550 and body.endswith("…")


def test_legacy_saved_answer_keeps_its_title_without_guessing_a_count():
    assert wearable_notice({"action": "answer", "text": "Latest?\nYes."}) == (
        "Answer to recent question", "Latest?\nYes.",
    )


def test_zero_questions_cannot_reveal_an_unrequested_older_answer():
    result = RecentAnswer(question_count=0, question=None, answer="An older answer must not appear")
    assert result.text == "No clear question was found in the recent audio."
