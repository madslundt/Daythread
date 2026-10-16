"""Deterministic local-calendar scoping before evidence retrieval."""

import re
from calendar import monthrange
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mandag": 0,
    "tirsdag": 1,
    "onsdag": 2,
    "torsdag": 3,
    "fredag": 4,
    "lørdag": 5,
    "søndag": 6,
}
STOPWORDS = set(
    """what when where who why how did do does was were is are the a an i we you
our my me it about happened happen say said tell show give recap summarize summary today yesterday
last this on in at of to for and or from please can could would recorded recording day days week
hvad hvornår hvor hvem hvorfor hvordan skete sker sagde siger om min mit mig vores vi jeg du den det
de en et af til fra på med og eller kan vil var er blev dag går forgårs sidste denne uge opsummer
opsummering vis fortæl der har havde havde have been before after since during meeting møde
they them their that those these more details decision decisions decide decided explain
all across every everything diary diaries recordings alle dagbog dagbøger dage tværs hele optagelser
month months year years måned måneder år
dem deres dette disse mere detaljer beslutning beslutninger besluttede uddyb
key main themes should follow up followup commitments commitment tasks task discussed
vigtigste hovedtemaer opfølgning følge op forpligtelser
""".split()
)


class DateScopeError(ValueError):
    """An explicitly requested calendar date is invalid."""


def date_scope(question: str, today: date) -> tuple[date, date] | None:
    """Return an inclusive range; explicit dates take precedence over relative language."""
    lowered = question.lower()
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", lowered)
    if dates:
        try:
            first = date.fromisoformat(dates[0])
            last = date.fromisoformat(dates[1]) if len(dates) > 1 else first
        except ValueError as exc:
            raise DateScopeError("Use valid ISO dates in chat questions") from exc
        return (min(first, last), max(first, last))
    if re.search(r"\b(day before yesterday|forgårs|forgaars)\b", lowered):
        day = today - timedelta(days=2)
        return day, day
    if re.search(r"\b(yesterday|i går|igår|i gaar|igaar)\b", lowered):
        day = today - timedelta(days=1)
        return day, day
    if re.search(r"\b(today|i dag|idag)\b", lowered):
        return today, today
    if re.search(r"\b(my day|min dag|daily recap)\b", lowered):
        return today, today
    if re.search(r"\b(last week|sidste uge)\b", lowered):
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if re.search(r"\b(this week|denne uge)\b", lowered):
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=6)
    if re.search(r"\b(last month|sidste måned)\b", lowered):
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if re.search(r"\b(this month|denne måned)\b", lowered):
        return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
    if re.search(r"\b(last year|sidste år)\b", lowered):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if re.search(r"\b(this year|dette år)\b", lowered):
        return date(today.year, 1, 1), date(today.year, 12, 31)
    for name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            days_ago = (today.weekday() - weekday) % 7
            if days_ago == 0 and re.search(r"\b(last|sidste)\b", lowered):
                days_ago = 7
            day = today - timedelta(days=days_ago)
            return day, day
    return None


def topic_query(question: str) -> str:
    question = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", question)
    return " ".join(
        word
        for word in re.findall(r"\w+", question.lower())
        if word not in STOPWORDS and word not in WEEKDAYS and len(word) > 1
    )


def contextual_query(question: str, history: list[dict]) -> tuple[str, str]:
    """Resolve short referential follow-ups without another model call or changing scope."""
    query = topic_query(question)
    referential = re.search(
        r"\b(it|its|that|those|they|them|he|she|his|her|det|den|han|hun|dem|deres)\b",
        question,
        re.I,
    )
    if query and len(query.split()) <= 2 and referential:
        for message in reversed(history):
            if message["role"] == "user":
                previous = topic_query(message["content"])
                if previous and previous != query:
                    return " ".join(dict.fromkeys((previous + " " + query).split())), (
                        message["content"][:1000] + "\nFollow-up: " + question
                    )
    return query, question


def archive_recall(question: str) -> bool:
    """An explicit archive-wide request should broaden an earlier topical conversation."""
    return bool(
        re.search(
            r"\b(?:all|every)\s+(?:the\s+)?(?:diar(?:y|ies)|days?|recordings?)\b"
            r"|\bacross\s+(?:all\s+)?(?:diaries|days)\b|\beverything\b"
            r"|\balle\s+(?:dagbøger|dage|optagelser)\b",
            question.lower(),
        )
    )
