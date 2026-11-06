"""Keep written, cited recap sections intact while grouping their navigation."""

import json
import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .inference import SOURCE_UUID_PATTERN, InferenceError, summary_language_instruction
from .recap_languages import normalize_source_languages

if TYPE_CHECKING:
    from .inference import Inference
    from .service import Memory

VERSION = "parent-contexts-5-source-language-json"
MAX_INPUT = 48000
_CITATION = re.compile(
    r"\[\s*" + SOURCE_UUID_PATTERN + r"(?:\s*,\s*" + SOURCE_UUID_PATTERN + r")*\s*\]", re.I,
)
TASK = (
    "Group these already-written recap sections into MAIN DISCUSSION CONTEXTS. This is "
    "navigation organization ONLY: do not write or edit recap prose. Existing section titles "
    "are often agenda items or features, not independent main topics. First identify the "
    "shared meeting subject, project, product or personal plan established by the section "
    "details, then assign its sections to that parent. In particular, release planning, "
    "bookmarks, interface interactions, data structure and search can all belong under ONE "
    "analysis-tool review when the details describe that same application. Likewise, market "
    "position, branding and capacity can be aspects of one product discussion. Preserve those "
    "sections as subtopics rather than promoting each aspect to a main topic. Use a singleton "
    "group only for a genuinely independent subject that does not belong with another "
    "section; different technical areas alone do not make independent main contexts. "
    "Do not merge unrelated projects merely because they share vocabulary or recording time. "
    "Use concise, specific parent titles grounded in the supplied details. Do not invent "
    "project names, meeting attendance or relationships. There is no numeric topic quota. "
    "Return only JSON: {\"groups\":[{\"title\":\"shared main subject\","
    "\"section_ids\":[\"t1\",\"t2\"]}]}. Assign EVERY supplied section ID exactly once. "
    "Do not omit, duplicate or invent sections. Never return prose, citations or Markdown."
)


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    section_ids: list[str] = Field(min_length=1)


class Organization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[Group] = Field(min_length=1)


def _request_heading(summary: str) -> bool:
    decoder = json.JSONDecoder()
    for heading in re.finditer(r"^#{2,3}[ \t]+", summary, re.M):
        try:
            value, _end = decoder.raw_decode(summary[heading.end():].lstrip())
        except ValueError:
            continue
        if isinstance(value, dict) and {"task", "evidence"} <= value.keys():
            return True
    return False


def _selected_versions(content: dict) -> str | None:
    sources = content.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    identities = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("id"):
            return None
        fields = (("recording_id", "result_version", "input_fingerprint", "pipeline_fingerprint",
                   "selection_version", "source_ids")
                  if source.get("reference_type") == "recording_transcription" else
                  ("source_version", "transcript_version"))
        if any(not source.get(field) for field in fields):
            return None
        identities.append({key: source.get(key) for key in ("id", "reference_type", *fields)})
    return json.dumps(sorted(identities, key=lambda item: item["id"]), sort_keys=True)


def recover_recap_prose(memory: Memory, result: dict, day: date) -> tuple[str, str | None]:
    """Recover only a malformed heading from an unchanged, verified prose snapshot."""
    summary = result.get("summary") or ""
    if not _request_heading(summary):
        return summary, None
    fingerprint = result.get("prose_fingerprint")
    selected = _selected_versions(result)
    if fingerprint and selected is not None:
        for version in memory.store.diary_versions(day.isoformat()):
            content = version["content"]
            candidate = content.get("summary")
            if (not isinstance(candidate, str) or not candidate.strip()
                    or _request_heading(candidate) or _selected_versions(content) != selected
                    or not (content.get("prose_fingerprint") == fingerprint
                            or version["fingerprint"] == fingerprint)):
                continue
            # Preserve the current source list and CAS target. Selection metadata follows
            # the restored prose rather than the invalid language-normalization attempt.
            for field in ("generated_at", "included_through", "completeness", "evidence"):
                if field in content:
                    result[field] = content[field]
            return candidate, content.get("output_id") or version.get("id") or "saved-version"
    raise InferenceError("No matching valid recap snapshot is available for recovery")


async def organize_summary(inference: Inference, summary: str) -> tuple[str, dict]:
    trace = {"version": VERSION, "model_calls": 0}
    headings = list(re.finditer(r"^## ([^\n]+)\n", summary, re.M))
    if len(headings) < 2 or headings[0].start() or re.search(r"^#{3,6} ", summary, re.M):
        return summary, trace | {"skip_reason": "already_organized_or_simple"}
    if not hasattr(inference, "for_task"):
        return summary, trace | {"skip_reason": "organization_unavailable"}
    sections = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(summary)
        sections.append({"id": f"t{index + 1}", "title": match[1],
                         "text": summary[match.start():end].rstrip()})
    rows = [{"id": item["id"], "title": item["title"],
             "details": _CITATION.sub("", item["text"].split("\n", 1)[1])}
            for item in sections]
    evidence = json.dumps(rows, ensure_ascii=False)
    if len(evidence) > MAX_INPUT:
        return summary, trace | {"skip_reason": "organization_input_over_budget"}
    task = TASK + summary_language_instruction(inference.settings.summary_language, summary)
    model = inference.for_task("summary")
    lookup = {item["id"]: item for item in sections}
    for attempt in range(2):
        trace["model_calls"] += 1
        if hasattr(model, "generate_structured_json"):
            answer = await model.generate_structured_json(
                task, evidence, Organization.model_json_schema(),
            )
        else:
            answer = await model.generate(task, evidence)
        try:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
            plan = Organization.model_validate_json(text)
            used, titles = [], set()
            for group in plan.groups:
                if "\n" in group.title or "\r" in group.title or group.title.startswith("#"):
                    raise ValueError()
                used.extend(group.section_ids)
                title = (lookup[group.section_ids[0]]["title"]
                         if len(group.section_ids) == 1 else group.title)
                if title.casefold() in titles:
                    raise ValueError()
                titles.add(title.casefold())
            if len(used) != len(lookup) or set(used) != set(lookup):
                raise ValueError()
            break
        except (ValueError, KeyError):
            if attempt:
                raise InferenceError("Invalid recap organization") from None
            task += (" The previous organization was invalid. Return every known section ID "
                     "exactly once, with nonempty plain parent titles and no extra fields.")
    order = {item["id"]: index for index, item in enumerate(sections)}
    groups = sorted(plan.groups, key=lambda group: min(order[key] for key in group.section_ids))
    rendered = []
    for group in groups:
        children = [lookup[key]["text"] for key in sorted(group.section_ids, key=order.get)]
        rendered.append(children[0] if len(children) == 1 else
                        f"## {group.title}\n\n" + "\n\n".join("#" + child for child in children))
    return "\n\n".join(rendered), trace | {
        "topic_count": len(groups),
        "subtopic_count": sum(len(group.section_ids) for group in groups
                              if len(group.section_ids) > 1),
    }


async def organize_recap(memory: Memory, result: dict, day: date, now: datetime) -> dict:
    """Rebuild evidence for changed navigation; reuse all original prose/source snapshots."""
    from uuid import uuid4

    from .domain import stamp

    summary = result.get("summary")
    if not summary:
        return result
    summary, recovered = recover_recap_prose(memory, result, day)
    organized, organization = await organize_summary(memory.inference, summary)
    organized, languages = await normalize_source_languages(
        memory.inference, organized, result["sources"],
    )
    if _request_heading(organized):
        raise InferenceError("Recap organization returned a request envelope as a heading")
    if recovered:
        organization["recovered_from_output_id"] = recovered
    organization["source_languages"] = languages
    organization["model_calls"] += languages["model_calls"]
    organization["organized_at"] = stamp(now)
    result["topic_organization"] = organization
    result["summary"] = organized
    result["output_id"] = str(uuid4())
    previous = result.get("evidence") or {}
    trace = previous.get("pipeline", {}) | {"topic_organization": organization}
    counts = {key: previous[key] for key in (
        "eligible_transcripts", "pending_chunks", "blocked_chunks", "selection_observed_at",
    ) if key in previous}
    start, end = memory.day_window(day)
    result["evidence"] = memory.evidence_metadata(
        result["sources"], organized, "transcribed_recordings", start, end,
        result["completeness"], trace, counts, kind="recap",
    )
    return result
