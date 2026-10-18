"""Readable completion notices, separate from generated evidence."""

import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

generating_job: ContextVar[bool] = ContextVar("generating_job", default=False)

LANGUAGE_NAMES = {
    "da": "Danish", "en": "English", "de": "German", "es": "Spanish", "fr": "French",
    "it": "Italian", "sv": "Swedish", "no": "Norwegian", "nb": "Norwegian Bokmål",
    "nn": "Norwegian Nynorsk", "fi": "Finnish", "nl": "Dutch", "pt": "Portuguese",
    "pl": "Polish", "uk": "Ukrainian", "ru": "Russian", "ar": "Arabic",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "tr": "Turkish",
}


def language_label(code: str) -> str:
    parts = code.lower().split("-", 1)
    name = LANGUAGE_NAMES.get(parts[0], code.upper())
    return f"{name} ({parts[1].upper()})" if len(parts) > 1 else name


def preview(text: str | None, limit: int = 180) -> str:
    text = text or ""
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[source:[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", re.sub(r"[#*_`]", "", text)).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def time_label(value: str, timezone: str) -> str:
    return datetime.fromisoformat(value).astimezone(ZoneInfo(timezone)).strftime("%d %b, %H:%M")


def publish_recap(memory, result: dict, fingerprint: str, guard=None) -> None:
    notifications = getattr(memory, "notifications", None)
    if notifications is None or generating_job.get() or not result.get("summary"):
        return
    day = result["day"]
    notifications.publish(
        kind="recap", title=f"Recap ready · {day}",
        body=preview(result["summary"]), target={"day": day},
        event_key=f"recap:{day}:{fingerprint}", guard=guard,
    )


def publish_weekly(memory, version: dict, guard=None) -> None:
    notifications = getattr(memory, "notifications", None)
    if notifications is None:
        return
    week = version["week_start"]
    count = len(version["items"])
    notifications.publish(
        kind="weekly", title=f"Weekly review ready · {week}",
        body=f"Week of {week}: {count} recorded decisions, commitments and discussion items.",
        target={"week_start": week, "version_id": version["id"]},
        event_key=f"weekly:{version['id']}", guard=guard,
    )


def job_notice(memory, job: dict, result: dict) -> tuple[str, str]:
    params, kind = job["parameters"], job["kind"]
    zone = memory.settings.timezone
    if kind == "rewind":
        count = len(result.get("sources", []))
        return (
            f"Your {params['minutes']}-minute rewind is ready",
            f"Window ending {time_label(params['end'], zone)} ({zone}) · "
            f"{count} recordings. " + preview(result.get("summary", "")),
        )
    if kind == "recap":
        return (f"Recap ready · {params['day']}",
                preview(result.get("summary", "")) or "Your recording coverage is ready to view.")
    if kind == "summary_translation":
        language = language_label(result.get("language", params.get("language", "")) or "")
        return (f"Translation ready · {language}", f"Summary is available in {language}.")
    if kind == "translation":
        source = memory.store.get_audio(params["source_id"]) or {}
        language = language_label(result.get("language", params.get("language", "")) or "")
        title = json.loads(source.get("topic_details") or "{}").get("title") or "Recording"
        when = time_label(source["started_at"], zone) if source.get("started_at") else ""
        scope = "Selected passage" if params.get("start_offset") is not None else "Transcript"
        return (f"Translation ready · {language}",
                f"{title} · {when} ({zone}). {scope} is available in {language}.")
    meeting = result.get("meeting", {})
    when = time_label(meeting["start"], zone) if meeting.get("start") else ""
    return ("Meeting recap ready", f"{when} ({zone}). " + preview(result.get("summary", "")))
