"""Compare local speech fixtures without opening the archive or downloading models."""

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omi_local.domain import wav_duration
from omi_local.inference import Inference
from omi_local.settings import Settings

Label = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/+@-]{0,199}$")]
OFFLINE_ALIGNMENT_CELL_BUDGET = 20_000_000


class SafeMetadata(BaseModel):
    """Only non-secret identifiers, never arbitrary settings or endpoint URLs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", check_fields=False)
    @classmethod
    def no_credentials(cls, value):
        if isinstance(value, str) and value.lower().startswith(("sk-", "sk_", "bearer")):
            raise ValueError("Metadata must contain non-secret identifiers only")
        return value


class ReferenceProvenance(SafeMetadata):
    kind: Literal["human", "vendor"]
    source: Label
    revision: Label


class RunMetadata(SafeMetadata):
    provider: Label
    model: Label
    revision: Label
    configuration: Label


class AudioCase(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    audio: str = Field(min_length=1)
    category: str = Field(pattern=r"^(clean|quiet|noisy|overlap|noise_only|unknown)$")
    language: str = Field(min_length=2, max_length=32)
    reference: str = Field(max_length=10000)
    required_phrases: list[str] = Field(default_factory=list, max_length=100)
    reference_provenance: ReferenceProvenance | None = None
    reference_context: Literal["same_audio", "full_recording_excerpt"] = "same_audio"


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=100)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(max_length=10000)
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CandidateRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Label
    metadata: RunMetadata
    cases: list[Hypothesis] = Field(min_length=1, max_length=200)


class SavedHypotheses(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    runs: list[CandidateRun] = Field(min_length=2, max_length=10)


def words(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def character_distance(expected: str, actual: str) -> int:
    """Exact Levenshtein distance using bit vectors rather than a character matrix."""
    if len(expected) > len(actual):
        expected, actual = actual, expected
    if not expected:
        return len(actual)
    masks: dict[str, int] = {}
    for index, character in enumerate(expected):
        masks[character] = masks.get(character, 0) | (1 << index)
    positive, negative = ~0, 0
    distance = len(expected)
    final_bit = 1 << (len(expected) - 1)
    for character in actual:
        matches = masks.get(character, 0)
        vertical = matches | negative
        horizontal = (((matches & positive) + positive) ^ positive) | matches | negative
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        distance += bool(plus & final_bit) - bool(minus & final_bit)
        plus = (plus << 1) | 1
        minus <<= 1
        positive = minus | ~(vertical | plus)
        negative = horizontal & plus
    return distance


def metrics(reference: str, hypothesis: str, phrases: list[str]) -> dict:
    expected, actual = words(reference), words(hypothesis)
    if len(expected) * len(actual) > 4_000_000:
        raise ValueError("Word alignment exceeds work limit; use shorter evaluation fixtures")
    # Levenshtein alignment with separate substitution/deletion/insertion counts.
    previous = [(i, 0, 0, i) for i in range(len(actual) + 1)]
    for index, token in enumerate(expected, 1):
        current = [(index, 0, index, 0)]
        for column, other in enumerate(actual, 1):
            cost, substitutions, deletions, insertions = previous[column - 1]
            same = token == other
            diagonal = (cost + (not same), substitutions + (not same), deletions, insertions)
            cost, substitutions, deletions, insertions = previous[column]
            deletion = (cost + 1, substitutions, deletions + 1, insertions)
            cost, substitutions, deletions, insertions = current[column - 1]
            insertion = (cost + 1, substitutions, deletions, insertions + 1)
            current.append(min(diagonal, deletion, insertion))
        previous = current
    edits, substitutions, deletions, insertions = previous[-1]
    padded = " " + " ".join(actual) + " "
    phrase_hits = sum(" " + " ".join(words(phrase)) + " " in padded for phrase in phrases)
    expected_chars, actual_chars = " ".join(expected), " ".join(actual)
    character_edits = character_distance(expected_chars, actual_chars)
    return {
        "reference_words": len(expected), "substitutions": substitutions,
        "hypothesis_words": len(actual), "word_errors": edits,
        "deletions": deletions, "insertions": insertions, "character_edits": character_edits,
        "reference_characters": len(expected_chars), "hypothesis_characters": len(actual_chars),
        "wer": edits / len(expected) if expected else None,
        "cer": character_edits / len(expected_chars) if expected_chars else None,
        "deletion_rate": deletions / len(expected) if expected else None,
        "insertion_rate": insertions / len(expected) if expected else None,
        "false_speech": bool(actual) if not expected else None,
        "hallucinated_words": len(actual) if not expected else 0,
        "required_phrase_hits": phrase_hits, "required_phrase_count": len(phrases),
        "required_phrase_recall": phrase_hits / len(phrases) if phrases else None,
    }


def aggregate(rows: list[dict], variant: str) -> dict:
    scored = [row[variant] for row in rows if row[variant] is not None]
    result = {"cases": len(rows), "scored": len(scored), "unavailable": len(rows) - len(scored)}
    for name in (
        "reference_words", "hypothesis_words", "substitutions", "deletions", "insertions",
        "word_errors", "reference_characters", "hypothesis_characters", "character_edits",
        "required_phrase_hits", "required_phrase_count", "hallucinated_words",
    ):
        result[name] = sum(row[name] for row in scored)
    for name, numerator, denominator in (
        ("wer", "word_errors", "reference_words"),
        ("cer", "character_edits", "reference_characters"),
        ("deletion_rate", "deletions", "reference_words"),
        ("insertion_rate", "insertions", "reference_words"),
        ("required_phrase_recall", "required_phrase_hits", "required_phrase_count"),
    ):
        result[name] = (round(result[numerator] / result[denominator], 4)
                        if result[denominator] else None)
    for name in ("wer", "cer", "false_speech"):
        values = [row[name] for row in scored if row[name] is not None]
        result[name if name == "false_speech" else f"macro_{name}"] = (
            round(mean(values), 4) if values else None
        )
    result["empty_reference_cases"] = sum(row["false_speech"] is not None for row in scored)
    result["false_speech_cases"] = sum(row["false_speech"] is True for row in scored)
    return result


def read_cases(manifest: Path) -> tuple[list[AudioCase], str]:
    raw, digest = read_json(manifest)
    if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
        raise ValueError("Use 1–200 fixture cases")
    cases = [AudioCase.model_validate(case) for case in raw]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Fixture IDs must be unique")
    for case in cases:
        if case.category == "noise_only" and words(case.reference):
            raise ValueError("Noise-only fixtures need an empty reference")
        if any(not words(phrase) for phrase in case.required_phrases):
            raise ValueError("Required phrases must contain words")
        reference = " " + " ".join(words(case.reference)) + " "
        if any(" " + " ".join(words(p)) + " " not in reference for p in case.required_phrases):
            raise ValueError("Required phrases must occur in the reference")
    return cases, digest


def read_json(path: Path):
    with path.open("rb") as source:
        data = source.read(32_000_001)
    if len(data) > 32_000_000:
        raise ValueError("Evaluation JSON exceeds size limit")
    return json.loads(data), hashlib.sha256(data).hexdigest()


def read_audio(manifest: Path, case: AudioCase) -> bytes:
    with (manifest.parent / case.audio).open("rb") as source:
        data = source.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("Fixture exceeds upload size limit")
    wav_duration(data)
    return data


def grouped_scores(results: list[dict], variants: list[str]) -> dict:
    groups = defaultdict(list)
    for result in results:
        groups["overall"].append(result)
        groups[f"category:{result['category']}"].append(result)
        groups[f"language:{result['language']}"].append(result)
    return {
        name: {variant: aggregate(rows, variant) for variant in variants}
        for name, rows in groups.items()
    }


def evaluate_offline(manifest: Path, hypotheses: Path) -> dict:
    """Score complete saved runs against the same audio and explicitly sourced references."""
    cases, manifest_digest = read_cases(manifest)
    raw, hypotheses_digest = read_json(hypotheses)
    saved = SavedHypotheses.model_validate(raw)
    variants = [run.id for run in saved.runs]
    if len(set(variants)) != len(variants):
        raise ValueError("Run IDs must be unique")
    reserved = {"id", "category", "language", "audio_sha256", "duration_seconds",
                "reference_sha256", "reference_provenance", "reference_context", "latency"}
    if reserved.intersection(variants):
        raise ValueError("Run IDs must not conflict with case metadata fields")
    if any(case.reference_provenance is None for case in cases):
        raise ValueError("Offline cases require explicit reference provenance")
    kinds = {case.reference_provenance.kind for case in cases}
    if len(kinds) != 1:
        raise ValueError("Human and vendor references require separate evaluations")
    kind = kinds.pop()
    expected = {case.id for case in cases}
    indexed = {}
    alignment_cells = 0
    for run in saved.runs:
        by_case = {item.id: item for item in run.cases}
        if len(by_case) != len(run.cases) or set(by_case) != expected:
            raise ValueError("Every run must cover exactly the same unique fixture IDs")
        indexed[run.id] = by_case
        alignment_cells += sum(
            len(words(case.reference)) * len(words(by_case[case.id].text)) for case in cases
        )
    if alignment_cells > OFFLINE_ALIGNMENT_CELL_BUDGET:
        raise ValueError("Evaluation exceeds total word alignment work limit; use smaller batches")
    results = []
    for case in cases:
        audio = read_audio(manifest, case)
        digest = hashlib.sha256(audio).hexdigest()
        duration = wav_duration(audio)
        row = {
            "id": case.id, "category": case.category, "language": case.language,
            "audio_sha256": digest, "duration_seconds": duration,
            "reference_sha256": hashlib.sha256(case.reference.encode()).hexdigest(),
            "reference_provenance": case.reference_provenance.model_dump(),
            "reference_context": case.reference_context, "latency": {},
        }
        for variant in variants:
            item = indexed[variant][case.id]
            if item.audio_sha256 != digest:
                raise ValueError("Saved hypothesis audio hash does not match fixture bytes")
            row[variant] = metrics(case.reference, item.text, case.required_phrases)
            row["latency"][variant] = {
                "milliseconds": item.latency_ms,
                "real_time_factor": (round(item.latency_ms / 1000 / duration, 4)
                                     if item.latency_ms is not None else None),
            }
        results.append(row)
    excerpt_count = sum(case.reference_context == "full_recording_excerpt" for case in cases)
    return {
        "schema_version": 1, "mode": "offline", "reference_kind": kind,
        "comparison": ("human_reference_accuracy" if kind == "human"
                       else "vendor_reference_agreement"),
        "notice": ("Scores compare against supplied human-reviewed references. " if kind == "human"
                   else "Vendor-reference agreement is not an accuracy benchmark. ")
        + (f"{excerpt_count} reference excerpts were produced with full-recording context; "
           "their source system did not receive the same cropped audio as these candidates. "
           if excerpt_count else "")
        + "All runs cover the same fixtures. Model metadata and latency are supplied, "
        "not verified. "
        "WER/CER use Unicode normalization, case folding and punctuation removal; "
        "numbers and overlapping speakers still require human review.",
        "manifest_sha256": manifest_digest, "hypotheses_sha256": hypotheses_digest,
        "reference_context_counts": {"same_audio": len(cases) - excerpt_count,
                                     "full_recording_excerpt": excerpt_count},
        "runs": [{"id": run.id, "metadata": run.metadata.model_dump()} for run in saved.runs],
        "groups": grouped_scores(results, variants), "cases": results,
    }


async def evaluate(manifest: Path) -> dict:
    cases, _ = read_cases(manifest)
    # Explicit OMI_* environment settings configure the model, never the owner's .env,
    # private settings or archive. All fixture processing is in memory.
    with tempfile.TemporaryDirectory(prefix="omi-audio-eval-") as root:
        config = Settings(data_dir=Path(root), worker_enabled=False, _env_file=None)
        async with httpx.AsyncClient(follow_redirects=False) as client:
            results = []
            for case in cases:
                data = read_audio(manifest, case)
                raw_config = config.model_copy(update={
                    "stt_max_gain_db": 0, "stt_enhancement_mode": "off",
                    "stt_boundary_search_seconds": 0, "stt_preserve_quiet": False,
                    "stt_filter_speech": False,
                })
                started = perf_counter()
                baseline = await Inference(raw_config, client).transcribe_result(data)
                baseline_ms = (perf_counter() - started) * 1000
                reviewed_config = config.model_copy(update={"stt_enhancement_mode": "compare"})
                started = perf_counter()
                processed = await Inference(reviewed_config, client).transcribe_result(data)
                processing_ms = (perf_counter() - started) * 1000
                chunks = processed["processing"]["chunks"]
                cleanup_complete = all(
                    c["enhancement_status"] in ("complete", "digital_silence") for c in chunks
                )
                cleanup = "\n".join(c.get("candidate", {}).get("text", "") for c in chunks)
                def score(text):
                    return metrics(case.reference, text, case.required_phrases)

                results.append({
                    "id": case.id, "category": case.category, "language": case.language,
                    "baseline": score(baseline["text"]), "processed": score(processed["text"]),
                    "cleanup": score(cleanup) if cleanup_complete else None,
                    "cleanup_status": [c["enhancement_status"] for c in chunks],
                    "baseline_ms": round(baseline_ms, 2),
                    "processing_and_review_ms": round(processing_ms, 2),
                })
    return {
        "notice": "Cleanup is an unreviewed candidate, never automatically saved as evidence. "
        "WER cannot measure simultaneous-speaker recovery without per-speaker human review.",
        "groups": grouped_scores(results, ["baseline", "processed", "cleanup"]),
        "processing_and_review_p95_ms": sorted(
            r["processing_and_review_ms"] for r in results
        )[math.ceil(len(results) * 0.95) - 1],
        "cases": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON list of labeled local WAV fixtures")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="Allow calls to configured local STT")
    mode.add_argument("--hypotheses", type=Path, help="Score saved paired runs without model calls")
    args = parser.parse_args()
    try:
        report = (evaluate_offline(args.manifest, args.hypotheses) if args.hypotheses
                  else asyncio.run(evaluate(args.manifest)))
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except Exception as exc:
        # Avoid exposing gateway response bodies or credentials in a terminal traceback.
        message = f"Audio evaluation failed ({type(exc).__name__}); no quality claim"
        raise SystemExit(message) from None
