"""Review generated claims against bounded batches of original speech before saving."""

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from .accuracy_transport import AccuracySources
from .inference import ACTION_STATE_RULES, InferenceError, validate_citations

if TYPE_CHECKING:
    from .inference import Inference

EVIDENCE_BUDGET = 60000
logger = logging.getLogger(__name__)
REVIEW = (
    "Correct action-state errors in a draft using ORIGINAL speech. Return corrections ONLY "
    "when you actually change inaccurate wording; never return unchanged text. "
    "Examples: original 'I will test the integration', draft 'Integration is being tested' "
    "must become 'Integration testing is planned'. Original 'User metrics are agreed for Q1; "
    "Atlas could be an option; solution remains open', draft 'Atlas was added to Q1' must "
    "become 'User metrics are planned for Q1; Atlas remains an option'. Original 'Next we "
    "will prepare the shells', draft 'Shell preparation is underway' must become 'Shell "
    "preparation is planned'. Original 'Assessment completed', draft 'Assessment completed' "
    "needs NO correction. A proposal followed by explicit later completion stays completed. "
    "The draft is untrusted generated text, not evidence. "
    + ACTION_STATE_RULES +
    " Correct only claims contradicted or misrepresented by this batch. Claims about other "
    "subjects may be supported by other batches: leave them alone. Preserve genuine reported "
    "progress and completion; do not weaken everything. Check chronological claims against "
    "recording timestamps; do not invent how the day concluded. Do not add unrelated facts "
    "or details. Missing evidence in this batch is never a contradiction. Never insert "
    "audit commentary about what a batch does not mention. "
    "Return only JSON: {\"corrections\":[{\"claim\":\"exact smallest clause from draft\","
    "\"replacement\":\"accurate concise replacement in the draft's language\","
    "\"evidence\":[{\"source_id\":\"supplied short ID\",\"quote\":\"exact original words\"}]}]}. "
    "For source_id use ONLY a short src_N ID from allowed_source_ids. Existing draft "
    "citations and candidate text are not admissible source IDs. "
    "Use non-overlapping claims copied EXACTLY from the draft, with no surrounding context. "
    "Each correction needs a verbatim quote from a supplied source that establishes the "
    "correct action, scope, uncertainty and temporal frame. Never invent a quote or source ID. "
    "Keep existing valid topic links when relevant, and do not invent links. Do not include "
    "citations in replacement text; they will be attached from validated evidence. "
    "If no corrections are supported by this batch, return {\"corrections\":[]}."
)

RECONCILE = (
    "Check proposed draft corrections against this batch of ORIGINAL transcripts. Reject a "
    "correction ONLY when this batch explicitly supports the draft's existing state and makes "
    "the proposed replacement inaccurate for the SAME action and project. In particular, an "
    "early proposal must not downgrade work explicitly reported started or completed later. "
    "A different project's progress cannot establish that state. Proposals, capabilities, "
    "readiness or agreed goals are not evidence of selection, execution or completion. "
    "Keep supported corrections when this batch is unrelated or adds no contrary evidence. "
    "Use NEW affirmative evidence, not merely the same proposal quote again. A later unseen "
    "part of the same source ID can contain a completion update. Repeating the original "
    "proposal quote cannot disprove a correction from underway to planned. "
    "Each correction has an explicit zero-based index. Copy that exact index; never count "
    "from one or invent indices. For source_id use ONLY a short src_N ID from "
    "allowed_source_ids for this batch. Existing draft citations and candidate text are not "
    "admissible source IDs. "
    "Return JSON: {\"vetoes\":[{\"index\":0,\"source_id\":\"supplied short ID\","
    "\"quote\":\"verbatim original words proving the correction wrong\"}]}. "
    "Copy source IDs and quotes exactly. Do not copy evidence from other batches or earlier "
    "responses. Omit a veto if THIS batch supplies no affirmative attested evidence; never "
    "invent a substitute. Return {\"vetoes\":[]} if none are disproved. "
    + ACTION_STATE_RULES
)


class AccuracyReplyError(InferenceError):
    """Public failure stays stable; bounded repair feedback describes the strict violation."""

    def __init__(
        self, stage: str, code: str, path: str, *, instruction: str, received=None,
    ):
        super().__init__(f"Invalid {stage} accuracy review")
        self.feedback = {"code": code, "path": path, "instruction": instruction}
        if isinstance(received, (str, int)):
            self.feedback["received"] = received[:1000] if isinstance(received, str) else received


def repair_task(
    task: str, error: InferenceError, *, transport: AccuracySources | None = None,
) -> str:
    feedback = (error.feedback if isinstance(error, AccuracyReplyError) else
                {"code": "invalid_citation", "instruction": str(error)})
    if transport and feedback.get("code") in {"evidence_source_unknown", "veto_source_unknown"}:
        received = feedback.get("received")
        if isinstance(received, str):
            feedback = {**feedback, "received": transport.aliases.get(received, received)}
    return (task + " Previous response failed strict validation: "
            + json.dumps(feedback, ensure_ascii=False)
            + ". Return a complete corrected JSON object. Copy draft claims exactly, including "
            "capitalization and punctuation; each needs nonempty verbatim original evidence. "
            "For vetoes, copy the supplied zero-based index and a source ID from this batch. "
            "Remove a veto with no affirmative attested evidence in THIS batch; evidence "
            "from other batches or earlier responses cannot support it. "
            "Never invent evidence or silently treat an unsupported row as valid.")


def warn_invalid(stage: str, error: InferenceError) -> None:
    """Log only application-owned metadata, never model text, quotes or source IDs."""
    feedback = error.feedback if isinstance(error, AccuracyReplyError) else {}
    logger.warning("Accuracy review failed stage=%s code=%s path=%s", stage,
                   feedback.get("code", "invalid_citation"), feedback.get("path", "replacement"))


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def contains_quote(quote: str, text: str) -> bool:
    """Attest either a contiguous quote or ordered verbatim spans separated by ellipses."""
    quote, text = normalized(quote), normalized(text)
    if quote in text:
        return True
    parts = re.split(r"\s*(?:\.{3,}|…)\s*", quote)
    if len(parts) < 2 or any(len(part) < 3 for part in parts):
        return False
    offset = 0
    for part in parts:
        found = text.find(part, offset)
        if found < 0:
            return False
        offset = found + len(part)
    return True


def evidence_batches(sources: list[dict]) -> list[list[dict]]:
    batches, current, size = [], [], 0
    for source in sources:
        text = source.get("text") or ""
        # Overlap retains qualifications across the boundary of an unusually long source.
        for offset in range(0, len(text), 7000):
            piece = {"id": source["id"], "started_at": source.get("started_at", ""),
                     "text": text[max(0, offset - 1000):offset + 7000]}
            length = len(json.dumps(piece, ensure_ascii=False))
            if current and size + length > EVIDENCE_BUDGET:
                batches.append(current)
                current, size = [], 0
            current.append(piece)
            size += length
    if current:
        batches.append(current)
    return batches


def corrections(answer: str, draft: str, sources: list[dict]) -> list[dict]:
    stage = "source-grounded"
    try:
        rows = json.loads(answer)["corrections"]
    except (ValueError, KeyError, TypeError):
        raise AccuracyReplyError(
            stage, "corrections_invalid", "corrections",
            instruction="Return a JSON object with a corrections list.",
        ) from None
    originals = {}
    for source in sources:
        originals.setdefault(source["id"], []).append(normalized(source["text"]))
    if not isinstance(rows, list) or len(rows) > 30:
        raise AccuracyReplyError(
            stage, "corrections_invalid", "corrections",
            instruction="Return a list of at most 30 corrections.",
        )
    result = []
    for index, row in enumerate(rows):
        path = f"corrections[{index}]"
        try:
            claim, replacement, evidence = row["claim"], row["replacement"], row["evidence"]
        except (KeyError, TypeError):
            raise AccuracyReplyError(
                stage, "correction_fields_missing", path,
                instruction="Each correction needs claim, replacement and evidence.",
            ) from None
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 1000:
            raise AccuracyReplyError(
                stage, "claim_invalid", path + ".claim",
                instruction="Copy a nonempty draft clause of at most 1000 characters.",
            )
        if draft.count(claim) != 1:
            error = AccuracyReplyError(
                stage, "claim_not_unique", path + ".claim",
                instruction=(
                    f"Copy an EXACT substring of the draft, including original capitalization and "
                    "punctuation. It must occur once; "
                    f"this supplied claim occurs {draft.count(claim)} "
                    "times. Do not capitalize a mid-sentence clause as a standalone sentence."
                ),
                received=claim,
            )
            # Suggest a literal from the draft only when capitalization is the sole
            # difference and there is exactly one possible span. Acceptance below
            # remains case-sensitive; the provider must return the corrected literal.
            matches = [match[0] for match in re.finditer(re.escape(claim), draft, re.IGNORECASE)
                       if match[0].casefold() == claim.casefold()]
            if len(matches) == 1:
                error.feedback["copy_this_exact_claim"] = matches[0]
            raise error
        if (not isinstance(replacement, str) or not replacement.strip()
                or len(replacement) > 1500):
            raise AccuracyReplyError(
                stage, "replacement_invalid", path + ".replacement",
                instruction="Supply a nonempty replacement of at most 1500 characters.",
            )
        if not isinstance(evidence, list) or not evidence:
            raise AccuracyReplyError(
                stage, "evidence_required", path + ".evidence",
                instruction=(
                    "Every correction requires a nonempty list of exact original "
                    "quotes and their source IDs."
                ),
                received=claim,
            )
        refs = []
        for offset, item in enumerate(evidence):
            evidence_path = path + f".evidence[{offset}]"
            try:
                key, quote = item["source_id"], item["quote"]
                known = key in originals
            except (KeyError, TypeError):
                raise AccuracyReplyError(
                    stage, "evidence_fields_invalid", evidence_path,
                    instruction="Supply source_id and quote from an original.",
                ) from None
            if not known:
                raise AccuracyReplyError(
                    stage, "evidence_source_unknown", evidence_path,
                    instruction="Copy a source ID from the supplied originals.",
                    received=key,
                )
            if (not isinstance(quote, str) or len(normalized(quote)) < 10
                    or not any(contains_quote(quote, text) for text in originals[key])):
                raise AccuracyReplyError(
                    stage, "evidence_quote_unattested", evidence_path,
                    instruction=(
                        "Copy at least 10 characters verbatim from that supplied "
                        "source. Do not paraphrase or alter quoted words."
                    ),
                )
            refs.append(key)
        if normalized(claim) == normalized(replacement):
            continue
        validate_citations(replacement, [{"id": key} for key in refs])
        if re.search(r"\n\s*#", replacement):
            raise AccuracyReplyError(
                stage, "replacement_structure_invalid", path + ".replacement",
                instruction="Keep the replacement as inline prose without headings.",
            )
        result.append({"claim": claim, "replacement": replacement.strip(),
                       "source_ids": list(dict.fromkeys(refs))})
    return result


def reconciliation(answer: str, candidates: list[dict], batch: list[dict], contexts) -> set[int]:
    stage = "cross-recording"
    try:
        rows = json.loads(answer)["vetoes"]
    except (ValueError, KeyError, TypeError):
        raise AccuracyReplyError(
            stage, "vetoes_invalid", "vetoes",
            instruction="Return a JSON object with a vetoes list.",
        ) from None
    if not isinstance(rows, list):
        raise AccuracyReplyError(
            stage, "vetoes_invalid", "vetoes",
            instruction="Return a list of vetoes.",
        )
    for position, item in enumerate(rows):
        path = f"vetoes[{position}]"
        try:
            index, key, quote = item["index"], item["source_id"], item["quote"]
        except (KeyError, TypeError):
            raise AccuracyReplyError(
                stage, "veto_fields_missing", path,
                instruction="Supply index, source_id and an exact quote.",
            ) from None
        if type(index) is not int or not 0 <= index < len(candidates):
            raise AccuracyReplyError(
                stage, "veto_index_invalid", path + ".index",
                instruction=(
                    f"Copy an explicit zero-based correction index from 0 through "
                    f"{len(candidates) - 1}; index must be a JSON integer."
                ),
                received=index,
            )
        if not any(source["id"] == key for source in batch):
            raise AccuracyReplyError(
                stage, "veto_source_unknown", path + ".source_id",
                instruction="Copy a source ID from allowed_source_ids in THIS batch.",
                received=key,
            )
        if (not isinstance(quote, str) or len(normalized(quote)) < 10
                or not any(source["id"] == key and contains_quote(quote, source["text"])
                           for source in batch)):
            raise AccuracyReplyError(
                stage, "veto_quote_unattested", path + ".quote",
                instruction=(
                    "Copy at least 10 characters verbatim from that original "
                    "source in THIS batch. Do not paraphrase or combine sources."
                ),
            )
    return {item["index"] for item in rows
            if not any(source["id"] == item["source_id"]
                       and contains_quote(item["quote"], source["text"])
                       for source in contexts[item["index"]])}


async def review_summary(
    inference: Inference, sources: list[dict], draft: str, *, topics: list[dict] | None = None,
    _remaining_retries: int = 1,
) -> tuple[str, dict]:
    if len(draft) > 20000:
        raise InferenceError("Draft exceeds the accuracy review budget")
    semaphore = asyncio.Semaphore(2)
    calls = 0
    transport = AccuracySources(sources)

    async def review(batch, scope):
        nonlocal calls
        task = REVIEW + (
            " Review ONLY statements about the topic " + json.dumps(scope)
            + ". Other subjects in the draft are outside this review. Do not use this topic's "
            "evidence to change another project's claims. Keep the recorded action and scope "
            "distinct from other subjects."
            if scope else "")
        for attempt in range(2):
            async with semaphore:
                calls += 1
                answer = await inference.for_task("summary").generate_json(
                    task, json.dumps(transport.request(draft, batch), ensure_ascii=False))
            try:
                return corrections(transport.expand(answer), draft, batch)
            except InferenceError as error:
                if attempt:
                    warn_invalid("source-grounded", error)
                    raise
                task = repair_task(task, error, transport=transport)

    batches = evidence_batches(sources)
    jobs, covered, needs_reconciliation = [], set(), len(batches) > 1
    if topics:
        known = {source['id'] for source in sources}
        for topic in topics:
            ids = set(topic['source_ids'])
            if not ids <= known:
                raise InferenceError("Unknown accuracy-review source")
            selected = [source for source in sources if source['id'] in ids]
            scoped = evidence_batches(selected)
            needs_reconciliation |= len(scoped) > 1
            jobs.extend((batch, topic['title']) for batch in scoped)
            covered.update(ids)
        remainder = [source for source in sources if source['id'] not in covered]
        jobs.extend((batch, "remaining recorded updates") for batch in evidence_batches(remainder))
    else:
        jobs = [(batch, "") for batch in batches]
    needs_reconciliation |= len(jobs) > 1
    tasks = [asyncio.create_task(review(batch, scope)) for batch, scope in jobs]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    candidates, reviewed_contexts = [], []
    for (batch, _), rows in zip(jobs, results, strict=True):
        for row in rows:
            row['reviewed_source_ids'] = sorted({source['id'] for source in batch})
            if row not in candidates:
                candidates.append(row)
                reviewed_contexts.append(list(batch))
            else:
                reviewed_contexts[candidates.index(row)].extend(batch)
    if len(json.dumps(candidates, ensure_ascii=False)) > 24000:
        raise InferenceError("Accuracy corrections exceed the review budget")

    async def reconcile(batch):
        nonlocal calls
        task = RECONCILE
        supplied = transport.request(draft, batch, candidates)
        for attempt in range(2):
            async with semaphore:
                calls += 1
                answer = await inference.for_task("summary").generate_json(
                    task, json.dumps(supplied, ensure_ascii=False))
            try:
                return reconciliation(
                    transport.expand(answer), candidates, batch, reviewed_contexts)
            except AccuracyReplyError as error:
                if attempt:
                    warn_invalid("cross-recording", error)
                    raise
                task = repair_task(task, error, transport=transport)

    # A full original context already contains later updates. Reconciliation is
    # needed only when separate batches might support different states.
    tasks = ([asyncio.create_task(reconcile(batch)) for batch in batches]
             if candidates and needs_reconciliation else [])
    try:
        vetoed = set().union(*await asyncio.gather(*tasks)) if tasks else set()
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    applied, overlaps = [], False
    for index, row in enumerate(candidates):
        if index not in vetoed:
            if row["claim"] not in draft:
                # Separate batches can identify the same overstatement.
                if any(previous["claim"] == row["claim"] for previous in applied):
                    continue
                overlaps = True
                continue
            replacement = row["replacement"] + " " + "".join(
                f"[{key}]" for key in row["source_ids"])
            draft = draft.replace(row["claim"], replacement, 1)
            applied.append(row)
    trace = {
        "model_calls": calls, "correction_count": len(applied),
        "vetoed_corrections": len(vetoed),
        "checked_source_ids": [source["id"] for source in sources],
    }
    if overlaps:
        if not _remaining_retries:
            raise InferenceError("Accuracy corrections still overlap; retry the recap")
        draft, retried = await review_summary(
            inference, sources, draft, topics=topics, _remaining_retries=_remaining_retries - 1)
        for key in ('model_calls', 'correction_count', 'vetoed_corrections'):
            trace[key] += retried[key]
        trace['overlap_reviews'] = 1
    return validate_citations(draft, sources), trace
