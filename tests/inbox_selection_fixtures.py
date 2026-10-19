"""Explicit positive selection judgments for non-selection inbox test fixtures."""

CRITERIA = {
    "commitment": "lasting_follow_up",
    "deadline": "lasting_follow_up",
    "question": "consequential_question",
    "reminder": "explicit_reminder",
    "event": "agreed_event",
    "reflection": "meaningful_reflection",
    "note": "durable_reference",
}


def selection(key, text, kind):
    return {
        "criterion": CRITERIA[kind],
        "reason": "This fixture explicitly judges the cited point useful beyond its conversation.",
        "evidence": [
            {"source_id": key, "start": 0, "end": min(len(text), 3000), "quote": text[:3000]}
        ],
    }
