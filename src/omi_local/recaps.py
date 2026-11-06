"""Prepare transcribed recordings once, then synthesize versioned daily recaps."""

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .completion_events import publish_recap
from .conversation_state import (
    Conversation,
    activity,
    conversations,
    finished_file,
    manual_upload,
    meeting_timeline,
    recordings,
)
from .domain import EvidenceChanged, stamp, utc_now
from .embedding_chunking import topic_regions
from .imported_meetings import contains_source
from .inference import (
    COMPLETE_RECORDING_SUMMARY_VERSION,
    SUMMARY_REDUCTION_VERSION,
    InferenceError,
    validate_citations,
)
from .recap_organization import VERSION as ORGANIZATION_VERSION
from .recap_organization import organize_recap
from .recording_topics import VERSION as TOPIC_VERSION
from .recording_topics import ensure_topics
from .recording_transcription_selection import (
    RecordingTranscriptReference,
)
from .speaker_projection import project_utterances
from .speakers import fingerprint as speaker_fingerprint
from .summary_coherence import VERSION as COHERENCE_VERSION
from .summary_filter import NO_CONTENT
from .summary_filter import VERSION as SUMMARY_FILTER_VERSION
from .summary_templates import VERSION as TEMPLATE_VERSION
from .summary_templates import SummaryTemplate, purpose_with_template, selected
from .vendor_imports import transcript_import
from .voice_activity import analyze_voice

if TYPE_CHECKING:
    from .service import Memory

PROMPT_VERSION = (f"subject-chapters-10-selective-{TOPIC_VERSION}-{SUMMARY_FILTER_VERSION}"
                  f"-{SUMMARY_REDUCTION_VERSION}-{COHERENCE_VERSION}")
DAY_PROMPT_VERSION = f"daily-topics-21-hierarchical-{COHERENCE_VERSION}"
RELEVANCE = (
    "Select for lasting relevance: meaningful outcomes or changes, concrete follow-ups and "
    "deadlines, agreed plans, consequential unanswered questions, useful unresolved concerns, "
    "and durable information needed later. Retain explicit requests to remember something, "
    "including ordinary errands; importance does not require a work project or a major event. "
    "Omit small talk, fleeting conversational requests, routine momentary choices, incidental "
    "mentions, vague musings, generic suggestions and trivia unless the conversation establishes "
    "a concrete later use or meaningful personal significance. Length, repetition, speaker labels "
    "and nearby importance markers alone do not establish relevance. Never invent stakes, "
    "obligations or a connection to the owner. Do not fill a quota or create a section for every "
    "subject hint. A short recap is better than padding. State clearly supported meaning in "
    "plain language. Do not repeat unintelligible names, acronyms or phonetic fragments as "
    "confirmed concepts, and do not invent replacements. Omit an unclear term when it is not "
    "consequential; if it matters, say that it is unclear. "
)
BOUNDARY_NOTE = (
    "Uploaded recordings are included after transcription. Live conversations wait for the "
    "configured pause in recorded speech. Missing uploads do not count as silence. "
    "Conversation boundaries are approximate."
)
SPEAKER_GROUNDED_VERSION = "speaker-grounded-preparation-source-local-alias-5"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _speaker_alias_order(source: dict) -> list[str]:
    """Keep numbered aliases stable when one source is split into topic excerpts."""
    text = source.get("text")
    if not isinstance(text, str):
        return []
    recording_id = source.get("recording_id")
    ordered, seen = [], set()
    for item in source.get("speaker_evidence", []):
        if not isinstance(item, dict) or item.get("attribution") != "attributed":
            continue
        owner_matches = item.get("source_id") == source.get("id") or (
            source.get("reference_type") == "recording_transcription"
            and item.get("recording_id") == recording_id
        )
        start, end = item.get("start_offset"), item.get("end_offset")
        if (not owner_matches or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text)
                or text[start:end] != item.get("text")):
            continue
        try:
            speaker_id = str(UUID(item.get("speaker_id")))
        except (ValueError, TypeError, AttributeError):
            continue
        if speaker_id not in seen:
            ordered.append(speaker_id)
            seen.add(speaker_id)
    return ordered


def topic_sources(sources: list[dict]) -> list[dict]:
    """Request copies share the transcript's subjects while retaining original UUIDs."""
    result = []
    for source in sources:
        text, sections = source["text"] or "", source.get("sections") or []
        alias_order = _speaker_alias_order(source)
        for start, end, index in topic_regions(text, sections):
            subject = sections[index]["title"] if index is not None else ""
            evidence = []
            for item in source.get("speaker_evidence", []):
                left, right = item.get("start_offset"), item.get("end_offset")
                correct_owner = (
                    item.get("source_id") == source["id"]
                    or source.get("reference_type") == "recording_transcription"
                    and item.get("recording_id") == source.get("recording_id")
                )
                if (correct_owner and type(left) is int
                        and type(right) is int and 0 <= left < right <= len(text)
                        and text[left:right] == item.get("text")
                        and max(start, left) < min(end, right)):
                    evidence.append(item)
            result.append({
                **source, "text": text[start:end], "subject_hint": subject,
                "speaker_evidence": evidence, "speaker_evidence_origin": start,
                "speaker_alias_order": alias_order,
                "canonical_text": text,
            })
    return result


def _daily_recap_purpose(
    day: date, sources: list[dict], presentation: SummaryTemplate | None = None,
) -> str:
    complete_recording = (
        len(sources) == 1 and sources[0].get("reference_type") == "recording_transcription"
    )
    source_description = "the complete original transcript" if complete_recording else (
        "transcribed recording notes"
    )
    filing_date = "for the selected day" if complete_recording else f"for {day.isoformat()}"
    meeting_detail = (
        "Use short specific ## main topic headings and optional ### subtopic headings, "
        "with coherent prose under each leaf subject. "
        "For this meeting recap, use as many focused paragraphs as needed to preserve the "
        "consequential details; do not impose a short sentence quota or fixed section count. "
        "Retain supported scope and constraints, concrete examples and quantities, approaches "
        "or options, resourcing and timing, and conditional follow-ups when present. An "
        "unanswered question or speculation is unresolved, not a confirmed fact or absence. "
        "Preserve spoken units and date precision; do not fill in missing units, dates or years. "
        if presentation is not None and presentation.template == "meeting" else
        "Use short specific ## main topic headings, optional ### subtopic headings, "
        "and one coherent summary per leaf subject in "
        "a few focused sentences, with a second paragraph only when needed. "
    )
    return (
        f"a readable diary recap {filing_date} from {source_description}. "
        + RELEVANCE +
        "Include only topics with useful supported substance. "
        f"If none qualify, return exactly {NO_CONTENT} and nothing else. "
        + (
            "The selected recap day and capture metadata are filing context only. Do not infer "
            "or repair a spoken date or year from them; state dates only when supported by the "
            "transcript. "
            if complete_recording else ""
        ) +
        "Group details only when the source connects them to the same project, plan, goal or "
        "workflow. Keep current role, team context and earlier project background with the "
        "subject they explain; a project mention alone does not make an independent recap "
        "subject. Keep substantial independent projects and personal arrangements separately "
        "findable. Shared vocabulary or appearing in one recording does not establish a "
        "connection. There is no target, minimum or maximum number of topics; do not preserve "
        "fragmented headings just because they already exist. Group substantial aspects of "
        "the same supported meeting or project as subtopics rather than main topics; keep "
        "simple subjects flat and use only one subtopic level. Do not infer a common meeting "
        "or project from capture time alone. Preserve useful details once and "
        "keep conflicting readings qualified. "
        "Prepared headings are provisional; do not copy their structure. Reconsider earlier "
        "discussions when later source evidence connects them, but later mentions alone do not "
        "prove agreement, completion or cancellation. Preserve changed plans, conditions and "
        "unique consequential details while removing repetition. "
        "Keep machine speaker aliases anonymous and source-local. Use a reviewed speaker name "
        "only to identify that speaker. Preserve names and roles mentioned in speech as third "
        "parties; do not map them to an anonymous voice or assign them its actions. Do not "
        "infer a speaker's identity or broader ownership from a label or first-person wording. "
        "Distinguish current roles and projects from proposed or future work, keep options "
        "tentative until explicitly decided, and do not turn wishes or implied intentions into "
        "actions. "
        + meeting_detail +
        "Do not create a "
        "recap title, date heading, overview, separate short and long summaries, previews, "
        "bullet lists or headings deeper than ###. Keep decisions, follow-ups and unresolved "
        "questions in their subject. Omit opaque fragments and incidental background. "
        "Do not turn garbled names or labels into confident subject headings; "
        "use a plain description only when "
        "supported, otherwise qualify or omit the unclear label. Do not create empty sections "
        "or placeholders, mix unrelated subjects, or claim the day is complete."
    )


class Recaps:
    def __init__(self, memory: Memory):
        self.memory = memory

    def presentation(self, day: date):
        return selected(self.memory, day)

    def groups(self, now: datetime, db: sqlite3.Connection | None = None) -> list[Conversation]:
        return recordings(self.memory.store.recap_snapshot(db), now,
                          5 * 60)

    def for_day(self, groups: list[Conversation], day: date) -> list[Conversation]:
        start, end = map(stamp, self.memory.day_window(day))
        # Cross-midnight conversations remain whole; both days disclose full source context.
        return [group for group in groups if group.start < end and group.end > start]

    def speaker_projection(self, group: Conversation, db=None) -> dict[str, dict]:
        """Read current source-local diarization evidence in the caller's SQLite snapshot."""
        speakers = self.memory.speakers
        if db is None:
            with self.memory.store.connect() as connection:
                connection.execute("BEGIN")
                return self.speaker_projection(group, connection)
        snapshot = self.memory.store.recap_snapshot(db)
        now = utc_now()
        timeline = meeting_timeline(snapshot, now)
        groups = conversations(snapshot, now, 5 * 60, timeline=timeline)
        source_ids = {row["id"] for row in group.sources}
        scopes = {}
        if group.sources:
            first = speakers.resolve(
                "source", group.sources[0]["id"], db, snapshot=snapshot,
                groups=groups, timeline=timeline,
            )
            if source_ids <= {row["id"] for row in first["rows"]}:
                scopes = {source_id: first for source_id in source_ids}
            else:
                scopes[group.sources[0]["id"]] = first
                for row in group.sources[1:]:
                    scope = speakers.resolve(
                        "source", row["id"], db, snapshot=snapshot,
                        groups=groups, timeline=timeline,
                    )
                    scopes[row["id"]] = scope

        projected_scopes = {}
        projections = {}
        for source_id, scope in scopes.items():
            scope_key = scope["key"]
            if scope_key not in projected_scopes:
                saved = speakers.load(db, scope)
                ready = saved is not None and saved["fingerprint"] == speaker_fingerprint(scope)
                payload = json.loads(saved["payload"]) if saved else {"speakers": [], "turns": []}
                projected_scopes[scope_key] = {
                    "state": "ready" if ready else "stale" if saved else "idle",
                    "version": saved["version"] if saved else None,
                    "evidence_version": saved["version"] if ready else None,
                    "turns": payload["turns"] if ready else [],
                    "speakers": payload["speakers"] if ready else [],
                    "utterances": project_utterances(
                        payload["turns"], scope["rows"], ready, payload["speakers"],
                    ),
                }
            projections[source_id] = projected_scopes[scope_key]
        return projections

    def selected_recording_variants(self, group: Conversation, db=None) -> dict[str, dict]:
        from .recording_transcription_recap import selected_recording_variants

        return selected_recording_variants(self, group, db)

    def recording_speaker_evidence(self, group: Conversation, recording_id: str,
                                   variant: dict, db=None):
        from .recording_transcription_recap import recording_speaker_evidence

        return recording_speaker_evidence(self, group, recording_id, variant, db)

    def group_key(self, group: Conversation, db=None) -> str:
        settings = self.memory.settings
        return digest({
            "sources": [(r["id"], r["text"], r["source_version"], r["transcript_version"])
                        for r in group.sources],
            "boundary": (group.closed_at, group.basis, group.proof),
            "hints": group.hints,
            "model": settings.llm_route("summary").identity,
            "thinking": settings.effective_summary_thinking,
            "thinking_token_budget": settings.llm_summary_thinking_token_budget,
            "language": settings.summary_language.lower(), "timezone": settings.timezone,
            "prompt": PROMPT_VERSION,
            "speaker_grounded": SPEAKER_GROUNDED_VERSION,
            "speakers": {
                source_id: {
                    "state": projection["state"],
                    "evidence_version": projection["evidence_version"],
                    "utterances": [
                        utterance for utterance in projection["utterances"]
                        if utterance["source_id"] == source_id
                    ],
                }
                for source_id, projection in self.speaker_projection(group, db).items()
            },
            "recording_transcripts": {
                recording_id: {
                    "selection_version": selected["selection_version"],
                    "result_version": selected["variant"]["version"],
                    "input_fingerprint": selected["input_fingerprint"],
                    "pipeline_fingerprint": selected["pipeline_fingerprint"],
                    "summary_strategy": COMPLETE_RECORDING_SUMMARY_VERSION,
                    "speaker_evidence": self.recording_speaker_evidence(
                        group, recording_id, selected["variant"], db,
                    )[1:],
                }
                for recording_id, selected in self.selected_recording_variants(group, db).items()
            },
        })

    def day_key(
        self, groups: list[Conversation], day: date | None = None, db=None, *,
        include_organization: bool = True,
    ) -> str:
        identity = {
            "conversations": [self.group_key(group, db) for group in groups if group.ready],
            "prompt": DAY_PROMPT_VERSION,
        }
        if include_organization:
            identity["topic_organization"] = ORGANIZATION_VERSION
        presentation = selected(self.memory, day, db)
        if presentation != SummaryTemplate():
            identity.update(presentation=presentation.model_dump(),
                            presentation_version=TEMPLATE_VERSION)
        return digest(identity)

    def guard(
        self, day: date, now: datetime, groups: list[Conversation],
    ) -> Callable[[sqlite3.Connection], None]:
        expected = {self.group_key(group) for group in groups if group.ready}

        def check(db):
            current = self.for_day(self.groups(now, db), day)
            actual = {self.group_key(group, db) for group in current if group.ready}
            # New independent recordings can join the next recap. Corrections,
            # deletion or regrouping of selected evidence must still reject it.
            if not expected <= actual:
                raise EvidenceChanged("Transcribed recording evidence changed; retry the recap")
        return check

    def status(self, groups: list[Conversation]) -> dict:
        ready = [group for group in groups if group.ready]
        pending = [group for group in groups if not group.ready]
        note = BOUNDARY_NOTE
        if any(r["source_kind"] == "omi_audio_webhook" for g in groups for r in g.sources):
            note += (" Raw Omi audio forwarding has estimated timestamps; import finished Omi "
                     "conversations to establish reliable conversation boundaries.")
        return {
            "included_through": max((g.end for g in ready), default=None),
            "deferred_conversations": len(pending),
            "deferred_sources": sum(len(g.sources) for g in pending),
            "waiting_for_pause": sum(
                not g.closed_at and any(not finished_file(r) for r in g.sources) for g in pending
            ),
            "pending_upload_sources": sum(
                len(g.sources) for g in pending if all(finished_file(r) for r in g.sources)
            ),
            "boundary_note": note,
        }

    def cached(self, day: date, now: datetime, groups: list[Conversation] | None = None) -> dict:
        memory = self.memory
        start, end = memory.day_window(day)
        groups = self.for_day(self.groups(now) if groups is None else groups, day)
        row = memory.store.diary(day.isoformat())
        if row:
            result = json.loads(row["content"])
            stale = row["fingerprint"] != self.day_key(groups, day=day)
            pending_ids = {r["id"] for g in groups if not g.ready for r in g.sources}
            if any(source["id"] in pending_ids for source in result["sources"]):
                # Late speech or a related upload can reopen selected evidence.
                # Hide its old interpretation while that group is still pending.
                result.update(summary=None, sources=[], output_id=None, evidence=None,
                              included_through=None, no_recap_content=False)
            # Preserve the generation-time cutoff; live pending counts are separate.
            return result | self.status(groups) | {
                "included_through": result.get("included_through"), "stale": stale,
                "completeness": memory.store.completeness(start, end),
            }
        result = memory.window_data(start, end)
        return result | self.status(groups) | {
            "sources": [], "day": day.isoformat(), "timezone": memory.settings.timezone,
            "generated_at": None, "stale": False, "no_recap_content": False,
            "included_through": None,
        }

    async def observe(self) -> None:
        """Backfill voice observations used for live pause detection and speaker grouping."""
        store = self.memory.store
        for row in store.recap_snapshot()["rows"]:
            if (manual_upload(row) or transcript_import(row["source_kind"])
                    or row["state"] != "done" or activity(row)):
                continue
            if row["audio_available"] and await asyncio.to_thread(store.has_audio, row["id"]):
                data = await asyncio.to_thread(store.read_audio, row["id"])
                value = await asyncio.to_thread(analyze_voice, data)
                store.save_recap_activity(row["id"], row["digest"], value)

    def sources(self, group: Conversation) -> list[dict]:
        from .service import public_source

        hints = group.hints
        selected = self.selected_recording_variants(group)
        replaced = {source_id for value in selected.values() for source_id in value["source_ids"]}
        rows = self.memory.store.get_audio_many([
            r["id"] for r in group.sources if r["state"] == "done" and r["text"]
            and r["id"] not in replaced
        ])
        sources = [public_source(rows[r["id"]]) for r in group.sources if r["id"] in rows]
        for source in sources:
            manual = manual_upload(source)
            source["important"] = not manual and any(
                source["started_at"] < mark["context_end"]
                and source["ended_at"] > mark["context_start"] for mark in hints["markers"]
            )
            source["meeting_hint_ids"] = [
                meeting["id"] for meeting in hints["meetings"]
                if (not manual or "source_ids" in meeting) and contains_source(meeting, source)
            ]
        for recording_id, value in selected.items():
            recording, variant = value["recording"], value["variant"]
            snapshot = recording.snapshot()
            evidence, speaker_version, speaker_state = self.recording_speaker_evidence(
                group, recording_id, variant,
            )
            reference: RecordingTranscriptReference = {
                "id": recording_id,
                "reference_type": "recording_transcription",
                "recording_id": recording_id,
                "result_version": variant["version"],
                "input_fingerprint": value["input_fingerprint"],
                "pipeline_fingerprint": value["pipeline_fingerprint"],
                "selection_version": value["selection_version"],
                "source_ids": value["source_ids"],
                "text": variant["text"],
            }
            sources.append({
                **reference,
                "device_id": "recording",
                "state": "done",
                "kind": "recording_transcription",
                "started_at": snapshot["started_at"],
                "ended_at": snapshot["ended_at"],
                "duration_seconds": snapshot["duration_seconds"],
                "audio_available": snapshot["audio_available"],
                "audio_availability": snapshot["audio_availability"],
                "title": snapshot["title"],
                "sections": [{
                    "title": "Full recording transcript",
                    "chapter_id": f"{recording_id}:transcript:{variant['version']}",
                    "start_offset": 0,
                    "end_offset": len(variant["text"]),
                }],
                "source_map": variant["source_map"],
                "projection": variant["projection"],
                "provenance": variant["provenance"],
                "speaker_evidence": evidence,
                "speaker_evidence_version": speaker_version,
                "speaker_evidence_state": speaker_state,
            })
        sources.sort(key=lambda source: (source.get("started_at", ""), source["id"]))
        return sources

    async def prepare_group(
        self, group: Conversation, guard: Callable[[sqlite3.Connection], None],
        *, force: bool = False, coherence_trace: list[dict] | None = None,
        generation_trace: list[dict] | None = None,
    ) -> str:
        memory = self.memory
        key = self.group_key(group)
        cached = None if force else self.group_summary(key)
        if cached is not None:
            if coherence_trace is not None:
                coherence_trace.append({"status": "cached", "model_calls": 0})
            return cached
        # Forced summaries use original transcripts directly. Navigation labels are
        # optional hints; their independent worker must not hold up an explicit redo.
        sources = self.sources(group)
        single_recording_candidate = (
            len(sources) == 1
            and sources[0].get("reference_type") == "recording_transcription"
        )
        if not force and not single_recording_candidate:
            await self.prepare_topics(group)
            sources = self.sources(group)
        projections = self.speaker_projection(group)
        for source in sources:
            if source.get("reference_type") == "recording_transcription":
                continue
            projection = projections.get(source["id"], {})
            source["speaker_evidence_version"] = projection.get("evidence_version")
            source["speaker_evidence_state"] = projection.get("state", "idle")
            evidence = [
                item for item in projection.get("utterances", [])
                if item.get("source_id") == source["id"]
            ]
            source["speaker_evidence"] = evidence if any(
                item.get("attribution") in {"attributed", "overlap"} for item in evidence
            ) else []
        if not sources:
            summary = NO_CONTENT
            if coherence_trace is not None:
                coherence_trace.append({"status": "skipped", "skip_reason": "no_sources",
                                        "model_calls": 0})
        else:
            context = (
                "notes from received audio before interrupted capture. A capture stop is not "
                "evidence of silence or that the conversation ended. "
                if group.basis == "capture_stopped" else
                "notes from a finished upload or a live conversation followed by a long pause. "
            )
            purpose = (
                context + "An uploaded file may contain only part of a conversation. " + RELEVANCE +
                "Current speaker evidence is provided per original source as exact transcript "
                "character spans with source-local times; a full recording source uses elapsed "
                "concatenated-file times. Use it only to attribute words within "
                "those exact spans. `attribution=unknown` or `overlap` has no known owner; text "
                "not listed in the speaker spans is also unknown. Never fill those gaps from "
                "nearby turns or first-person wording. Aliases such as speaker_1 are anonymous "
                "clusters local to one source; they do not verify human identity or carry across "
                "recordings. "
                "Use a personal name to identify a speaker only when the evidence explicitly "
                "supplies a reviewed speaker_name. Preserve names and roles mentioned in speech "
                "as third parties; do not map them to an anonymous voice or assign them its "
                "actions. Keep the original source UUID citations. Distinguish a person's "
                "current role and current project from a proposed, possible or future project; "
                "keep options tentative until the speakers explicitly decide. Describe explicit "
                "actions separately from wishes or implied intentions. " +
                "Preserve short meaningful answers in context, dates, "
                "agreements and decisions without claiming the conversation has ended. "
                "Omit fillers, unintelligible fragments and isolated "
                "words without supported meaning. Do not infer events from background noise. "
                f"If nothing useful is supported, return exactly {NO_CONTENT} and nothing else. "
                "Multiple sources can record the same meeting. Describe shared points once, "
                "retain unique details selected for their usefulness from each source, including "
                "its beginning and end when relevant, "
                "and disclose conflicting readings instead of guessing. "
                "Keep related details together by subject. A recording can cover several "
                "subjects; summarize the useful ones without forcing event, decision or question "
                "categories. "
                "Use specific subject headings (##) matching the supplied subject hints when "
                "they fit the transcript. Hints are navigation labels, never additional facts. "
                "A subject continuing across audio parts belongs in the same section, even "
                "when its title is phrased differently. Split at sustained changes of subject, "
                "never at upload boundaries, timestamps, speaker changes or language switches. "
                "Do not repeat the same point in different categories or add empty placeholders. "
                "Otherwise preserve original source UUID citations for every useful point"
            )
            direct_trace = None
            if single_recording_candidate:
                method = getattr(memory.inference, "summarize_complete_recording_traced", None)
                if method is not None:
                    direct = await method(sources[0], purpose)
                    if direct is not None:
                        summary, direct_trace = direct
            if direct_trace is None:
                if not force and single_recording_candidate:
                    # The original 12k reduction remains the fallback when the direct
                    # full-recording prompt cannot safely fit the provider context.
                    await self.prepare_topics(group)
                    sources = self.sources(group)
                summary = await memory.inference.summarize(self.note_sources(sources), purpose)
            summary = validate_citations(summary, sources).strip()
            if summary != NO_CONTENT and not any(s["id"].lower() in summary.lower()
                                                 for s in sources):
                raise InferenceError("Conversation notes require original source citations")
            if direct_trace is not None:
                if generation_trace is not None:
                    generation_trace.append(direct_trace)
                review = direct_trace.get("coherence_review", {
                    "status": "skipped", "skip_reason": "review_trace_unavailable",
                    "model_calls": 0,
                })
                if coherence_trace is not None:
                    coherence_trace.append(review)
            elif summary != NO_CONTENT and hasattr(memory.inference, "cohere_completed_summary"):
                summary, review = await memory.inference.cohere_completed_summary(
                    sources, summary, purpose,
                )
                summary = validate_citations(summary, sources).strip()
                if summary != NO_CONTENT and not any(s["id"].lower() in summary.lower()
                                                     for s in sources):
                    raise InferenceError("Conversation notes require original source citations")
                if coherence_trace is not None:
                    coherence_trace.append(review)
            elif coherence_trace is not None:
                coherence_trace.append({
                    "status": "skipped", "skip_reason": (
                        "no_content" if summary == NO_CONTENT else "editor_unavailable"
                    ), "model_calls": 0,
                })
        self.save_group_summary(key, summary, sources, guard)
        return summary

    def group_summary(self, key):
        return self.memory.store.recap_summary(key)

    def save_group_summary(self, key, summary, sources, guard):
        self.memory.store.save_recap_summary(key, summary, sources, guard)

    async def prepare_topics(self, group):
        await ensure_topics(self.memory, {source["id"] for source in group.sources})

    def note_sources(self, sources):
        return topic_sources(sources)

    async def prepare(self, now: datetime) -> None:
        groups = self.groups(now)
        for group in groups:
            if not group.ready:
                continue
            # Guard the conversation itself rather than unrelated archive arrivals.
            expected = self.group_key(group)

            def guard(db, expected=expected):
                current = self.groups(now, db)
                if not any(g.ready and self.group_key(g, db) == expected for g in current):
                    raise EvidenceChanged("Conversation changed during preparation")

            await self.prepare_group(group, guard)

    def can_reuse_prose(self, day: date, now: datetime) -> bool:
        existing = self.memory.store.diary(day.isoformat())
        if not existing:
            return False
        groups = self.for_day(self.groups(now), day)
        prose_key = self.day_key(groups, day=day, include_organization=False)
        return (existing["fingerprint"] in (self.day_key(groups, day=day), prose_key)
                or json.loads(existing["content"]).get("prose_fingerprint") == prose_key)

    async def generate(
        self, day: date, now: datetime, *, force: bool = False, reuse_only: bool = False,
    ) -> dict:
        memory = self.memory
        start, end = memory.day_window(day)
        groups = self.for_day(self.groups(now), day)
        if force and any(not group.ready and any(source["text"] for source in group.sources)
                         for group in groups):
            raise EvidenceChanged("Transcribed recording evidence changed; retry the recap")
        key = self.day_key(groups, day=day)
        guard = self.guard(day, now, groups)
        publication_guard = guard
        existing = memory.store.diary(day.isoformat())
        if not force and existing and existing["fingerprint"] == key:
            return self.cached(day, now)
        prose_key = self.day_key(groups, day=day, include_organization=False)
        previous = json.loads(existing["content"]) if existing else {}
        reuse = not force and existing and (
            existing["fingerprint"] == prose_key or previous.get("prose_fingerprint") == prose_key
        )
        if reuse_only and not reuse:
            raise EvidenceChanged("Recorded evidence changed; try the recap again")
        if reuse:
            # Only navigation policy changed. The selected originals, prose policy,
            # model and preferences still match; never rerun the grounded writers.
            result = previous
            source_guard = guard

            def guard(db):
                source_guard(db)
                current = db.execute(
                    "SELECT fingerprint,content FROM diary WHERE day=?", (day.isoformat(),),
                ).fetchone()
                if (current is None or current["fingerprint"] != existing["fingerprint"]
                        or current["content"] != existing["content"]):
                    raise EvidenceChanged("Saved recap changed during organization; try again")
        else:
            result = await self.synthesize(
                day, now, groups, guard, memory.window_data(start, end) | self.status(groups),
                force=force,
            )
        result = await organize_recap(memory, result, day, now)
        result["prose_fingerprint"] = prose_key
        if any(group.ready for group in groups) or existing:
            memory.store.save_diary(day.isoformat(), key, result, guard=guard)
            publish_recap(memory, result, key, guard=publication_guard)
            return self.cached(day, now)
        return result

    async def synthesize(self, day, now, groups, guard, result, *, force: bool = False):
        """Share the grounded preparation/synthesis pipeline across independent recap scopes."""
        memory = self.memory
        template = self.presentation(day)
        start, end = memory.day_window(day)
        ready = [g for g in groups if g.ready]
        blocked = sum(r["state"] == "blocked" for r in result["sources"])
        sources, inputs, seen, repeated, coherence_reviews, generation_reviews = (
            [], [], set(), [], [], [],
        )
        for group in ready:
            originals = self.sources(group)
            sources.extend(originals)
            texts = [" ".join((source.get("text") or "").split()) for source in originals]
            # Repeating a substantial exact excerpt adds no new information or importance.
            # Keep every source and timestamp in the archive/result; omit only repeated input.
            exact_repeat = bool(texts) and all(text in seen for text in texts)
            hinted = bool(group.hints["markers"] or group.hints["meetings"])
            seen.update(text for text in texts if len(text) >= 500 and len(text.split()) >= 80)
            if exact_repeat and not hinted:
                repeated.extend(source["id"] for source in originals)
                continue
            notes = await self.prepare_group(
                group, guard, force=force, coherence_trace=coherence_reviews,
                generation_trace=generation_reviews,
            )
            if notes != NO_CONTENT and originals:
                inputs.extend(
                    {
                        **source,
                        "text": notes if index == 0 else "",
                        **({"citation_source_ids": [item["id"] for item in originals]}
                           if index == 0 else {}),
                    }
                    for index, source in enumerate(originals)
                )
        # Sources are originals; bounded cached notes are used only for daily synthesis.
        result.update(sources=sources, day=day.isoformat(), timezone=memory.settings.timezone,
                      model=memory.settings.llm_route("summary").model,
                      generated_at=stamp(now), no_recap_content=bool(ready) and not inputs,
                      stale=False, summary_template=template.model_dump())
        trace = {"conversation_preparation": "cached_transcribed_recordings",
                 "conversation_count": len(ready), "exact_repeated_excerpt_ids": repeated,
                 "conversation_coherence": {
                     "reviews": coherence_reviews,
                     "model_calls": sum(review.get("model_calls", 0)
                                        for review in coherence_reviews),
                 },
                 "conversation_summary_generation": {
                     "reviews": generation_reviews,
                     "model_calls": sum(review.get("model_calls", 0)
                                        for review in generation_reviews),
                 }}
        if inputs:
            purpose = _daily_recap_purpose(day, sources, template)
            purpose = purpose_with_template(purpose, template)
            synthesis_key = digest({
                "kind": "unique_daily_synthesis", "inputs": inputs,
                "prompt": DAY_PROMPT_VERSION, "purpose": purpose,
                "preparation_policy": PROMPT_VERSION,
                **({"complete_recording_summary_strategy": COMPLETE_RECORDING_SUMMARY_VERSION}
                   if any(source.get("reference_type") == "recording_transcription"
                          for source in sources) else {}),
                "model": memory.settings.llm_route("summary").identity,
                "thinking": memory.settings.effective_summary_thinking,
                "thinking_token_budget": memory.settings.llm_summary_thinking_token_budget,
                "language": memory.settings.summary_language.lower(),
            })
            summary = None if force else memory.store.recap_summary(synthesis_key)
            if summary is not None:
                trace["daily_synthesis"] = {
                    "model_calls": 0, "reduction_used": False,
                    "cache_hit": "identical_unique_notes",
                    "final_prompt_source_ids": sorted({
                        source["id"] for source in sources
                        if any(source["id"] in item["text"] for item in inputs)
                    }),
                }
            else:
                if hasattr(memory.inference, "summarize_daily_traced"):
                    summary, pipeline = await memory.inference.summarize_daily_traced(
                        inputs, purpose, original_sources=sources,
                    )
                    trace["daily_synthesis"] = pipeline
                elif hasattr(memory.inference, "summarize_traced"):
                    summary, pipeline = await memory.inference.summarize_traced(inputs, purpose)
                    trace["daily_synthesis"] = pipeline
                else:
                    summary = await memory.inference.summarize(inputs, purpose)
                summary = validate_citations(summary, sources).strip()
                memory.store.save_recap_summary(synthesis_key, summary, sources, guard)
            if summary == NO_CONTENT:
                result.update(summary=None, no_recap_content=True)
            else:
                result["summary"] = summary
                result["output_id"] = str(uuid4())
                counts = {"eligible_transcripts": len(sources),
                          "pending_chunks": result["pending_chunks"],
                          "blocked_chunks": blocked,
                          "selection_observed_at": (result.get("completeness") or {}).get(
                              "as_of", stamp(now))}
                result["evidence"] = memory.evidence_metadata(
                    sources, result["summary"], "transcribed_recordings", start, end,
                    result["completeness"], trace, counts, kind="recap",
                )
        return result
