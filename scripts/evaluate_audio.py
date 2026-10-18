"""Compare local speech fixtures without opening the archive or downloading models."""

import argparse
import asyncio
import json
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from statistics import mean
from time import perf_counter

import httpx
from pydantic import BaseModel, Field

from omi_local.domain import wav_duration
from omi_local.inference import Inference
from omi_local.settings import Settings


class AudioCase(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    audio: str = Field(min_length=1)
    category: str = Field(pattern=r"^(clean|quiet|noisy|overlap|noise_only)$")
    language: str = Field(min_length=2, max_length=32)
    reference: str = Field(max_length=10000)
    required_phrases: list[str] = Field(default_factory=list, max_length=100)


def words(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def metrics(reference: str, hypothesis: str, phrases: list[str]) -> dict:
    expected, actual = words(reference), words(hypothesis)
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
    return {
        "reference_words": len(expected), "substitutions": substitutions,
        "deletions": deletions, "insertions": insertions,
        "wer": edits / len(expected) if expected else None,
        "deletion_rate": deletions / len(expected) if expected else None,
        "false_speech": bool(actual) if not expected else None,
        "required_phrase_recall": phrase_hits / len(phrases) if phrases else None,
    }


def aggregate(rows: list[dict], variant: str) -> dict:
    scored = [row[variant] for row in rows if row[variant] is not None]
    result = {"cases": len(rows), "scored": len(scored), "unavailable": len(rows) - len(scored)}
    for name in ("wer", "deletion_rate", "false_speech", "required_phrase_recall"):
        values = [row[name] for row in scored if row[name] is not None]
        result[name] = round(mean(values), 4) if values else None
    return result


async def evaluate(manifest: Path) -> dict:
    raw = json.loads(manifest.read_text())
    if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
        raise ValueError("Use 1–200 fixture cases")
    cases = [AudioCase.model_validate(case) for case in raw]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Fixture IDs must be unique")
    # Explicit OMI_* environment settings configure the model, never the owner's .env,
    # private settings or archive. All fixture processing is in memory.
    with tempfile.TemporaryDirectory(prefix="omi-audio-eval-") as root:
        config = Settings(data_dir=Path(root), worker_enabled=False, _env_file=None)
        async with httpx.AsyncClient(follow_redirects=False) as client:
            results = []
            for case in cases:
                path = manifest.parent / case.audio
                if path.stat().st_size > 4_000_000:
                    raise ValueError("Fixture exceeds upload size limit")
                data = path.read_bytes()
                wav_duration(data)
                if case.category == "noise_only" and words(case.reference):
                    raise ValueError("Noise-only fixtures need an empty reference")
                if any(not words(phrase) for phrase in case.required_phrases):
                    raise ValueError("Required phrases must contain words")
                reference = " " + " ".join(words(case.reference)) + " "
                if any(" " + " ".join(words(p)) + " " not in reference
                       for p in case.required_phrases):
                    raise ValueError("Required phrases must occur in the reference")
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
    groups = defaultdict(list)
    for result in results:
        groups["overall"].append(result)
        groups[f"category:{result['category']}"].append(result)
        groups[f"language:{result['language']}"].append(result)
    return {
        "notice": "Cleanup is an unreviewed candidate, never automatically saved as evidence. "
        "WER cannot measure simultaneous-speaker recovery without per-speaker human review.",
        "groups": {
            name: {
                variant: aggregate(rows, variant)
                for variant in ("baseline", "processed", "cleanup")
            }
            for name, rows in groups.items()
        },
        "processing_and_review_p95_ms": sorted(
            r["processing_and_review_ms"] for r in results
        )[min(len(results) - 1, int(len(results) * 0.95))],
        "cases": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON list of labeled local WAV fixtures")
    parser.add_argument("--live", action="store_true", help="Allow calls to configured local STT")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly allow local speech-model calls")
    try:
        print(json.dumps(asyncio.run(evaluate(args.manifest)), indent=2, ensure_ascii=False))
    except Exception as exc:
        # Avoid exposing gateway response bodies or credentials in a terminal traceback.
        message = f"Audio evaluation failed ({type(exc).__name__}); no quality claim"
        raise SystemExit(message) from None
