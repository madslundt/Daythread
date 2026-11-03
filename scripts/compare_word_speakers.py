"""Join exact transcript tokens to anonymous reference speaker utterances."""

import argparse
import difflib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compare_diarization import _global_mapping, _merge

_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_MIN_MATCH_TOKENS = 5


def _finite_time(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {field}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Invalid {field}")
    return result


def _timed_rows(rows: list[dict], *, duration: float, reference_unit: str | None = None):
    if not isinstance(rows, list):
        raise ValueError("Expected timed rows")
    factor = {"seconds": 1.0, "milliseconds": 0.001}.get(reference_unit, 1.0)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid timed row")
        start = _finite_time(row.get("start"), "start") * factor
        end = _finite_time(row.get("end"), "end") * factor
        speaker = row.get("speaker")
        if (not isinstance(speaker, (str, int)) or isinstance(speaker, bool)
                or not str(speaker).strip() or not 0 <= start < end <= duration):
            raise ValueError("Invalid timed row")
        result.append((start, end, str(speaker)))
    return result


def _validate_anchors(canonical_text: str, anchors: list[dict], duration: float):
    if not isinstance(anchors, list):
        raise ValueError("Expected a list of exact word anchors")
    tokens = list(_TOKEN.finditer(canonical_text))
    timed: dict[int, tuple[float, float]] = {}
    unsupported_tokens: set[int] = set()
    unsupported_anchor_count = 0
    previous_offset = previous_time = 0.0
    for row in anchors:
        if not isinstance(row, dict) or row.get("kind") != "word":
            raise ValueError("Expected exact word anchors")
        start, end = _finite_time(row.get("start"), "word start"), _finite_time(
            row.get("end"), "word end",
        )
        left, right, text = row.get("start_offset"), row.get("end_offset"), row.get("text")
        if (type(left) is not int or type(right) is not int
                or not 0 <= left < right <= len(canonical_text)
                or canonical_text[left:right] != text
                or not 0 <= start < end <= duration
                or left < previous_offset or start < previous_time):
            raise ValueError("Invalid exact word anchor")
        contained = [i for i, token in enumerate(tokens)
                     if left <= token.start() and token.end() <= right]
        if not contained or any(
            index in timed or index in unsupported_tokens for index in contained
        ):
            raise ValueError("Word anchor must cover unique canonical tokens")
        if len(contained) != 1:
            # Exact timing spans covering multiple lexical tokens are not word timing.
            unsupported_anchor_count += 1
            unsupported_tokens.update(contained)
            previous_offset, previous_time = right, end
            continue
        timed[contained[0]] = (start, end)
        previous_offset, previous_time = right, end
    return tokens, timed, unsupported_anchor_count, len(unsupported_tokens)


def _reference_tokens(utterances: list[dict], duration: float, time_unit: str):
    rows = _timed_rows(utterances, duration=duration, reference_unit=time_unit)
    result = []
    previous_start = -1.0
    for utterance, (start, end, speaker) in zip(utterances, rows, strict=True):
        text = utterance.get("text")
        if not isinstance(text, str) or start < previous_start:
            raise ValueError("Invalid ordered reference utterances")
        previous_start = start
        result.extend((token.group().casefold(), speaker, start, end)
                      for token in _TOKEN.finditer(text))
    return rows, result


def _merge_turn_rows(turns: list[tuple[float, float, str]]):
    merged = []
    for speaker in sorted({speaker for _, _, speaker in turns}):
        for start, end in sorted((a, b) for a, b, label in turns if label == speaker):
            if merged and merged[-1][2] == speaker and start - merged[-1][1] <= 1 / 16000:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end), speaker)
            else:
                merged.append((start, end, speaker))
    return sorted(merged)


def _word_turn_label(
    interval: tuple[float, float], turns: list[tuple[float, float, str]],
    mapping: dict[str, str],
) -> str | None:
    start, end = interval
    containing = {speaker for left, right, speaker in turns
                  if left <= start and right >= end}
    touching = {speaker for left, right, speaker in turns
                if left < end and right > start}
    if len(containing) != 1 or touching != containing:
        return None
    return mapping.get(next(iter(containing)))


def evaluate_word_speakers(
    canonical_text: str,
    word_anchors: list[dict],
    turns: list[dict],
    reference_utterances: list[dict],
    *,
    duration_seconds: float,
    reference_time_unit: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare exact matched canonical words with their own reference token labels.

    The reference time unit is mandatory; it is never inferred from magnitudes.
    Untimed canonical words remain in the global token sequence and are unknown.
    """
    duration = _finite_time(duration_seconds, "duration")
    if duration <= 0 or not isinstance(canonical_text, str):
        raise ValueError("Invalid canonical transcript")
    if reference_time_unit not in {"seconds", "milliseconds"}:
        raise ValueError("reference_time_unit must be seconds or milliseconds")
    if provenance is not None and (
        not isinstance(provenance, dict)
        or any(not isinstance(key, str) or not key for key in provenance)
        or any(type(value) not in (str, int, float) for value in provenance.values())
        or any(type(value) in (int, float) and not math.isfinite(value)
               for value in provenance.values())
    ):
        raise ValueError("Provenance must contain only named scalar metadata")

    canonical_tokens, timed_words, unsupported_anchors, unsupported_words = _validate_anchors(
        canonical_text, word_anchors, duration,
    )
    hypothesis_turns = _merge_turn_rows(_timed_rows(turns, duration=duration))
    reference_rows, reference_tokens = _reference_tokens(
        reference_utterances, duration, reference_time_unit,
    )
    hyp_tracks = {
        speaker: _merge([(a, b) for a, b, value in hypothesis_turns if value == speaker])
        for speaker in {speaker for _, _, speaker in hypothesis_turns}
    }
    ref_tracks = {
        speaker: _merge([(a, b) for a, b, value in reference_rows if value == speaker])
        for speaker in {speaker for _, _, speaker in reference_rows}
    }
    label_map, _ = _global_mapping(hyp_tracks, ref_tracks)

    hypothesis = [token.group().casefold() for token in canonical_tokens]
    reference = [token[0] for token in reference_tokens]
    matched_pairs = []
    matcher = difflib.SequenceMatcher(None, hypothesis, reference, autojunk=False)
    for block in matcher.get_matching_blocks():
        if block.size >= _MIN_MATCH_TOKENS:
            matched_pairs.extend((block.a + offset, block.b + offset)
                                 for offset in range(block.size))

    reference_labels: dict[int, set[str]] = defaultdict(set)
    matched_reference_indices = set()
    for hyp_index, ref_index in matched_pairs:
        # Both indices refer to the original flattened token streams. The reference
        # speaker is attached to this token's owning utterance, not to its index.
        reference_labels[hyp_index].add(reference_tokens[ref_index][1])
        matched_reference_indices.add(ref_index)

    unknown = {"untimed": 0, "turn_or_overlap": 0, "reference_ambiguous": 0}
    compared = agreement = disagreement = 0
    matched_words = set(reference_labels)
    for hyp_index in matched_words:
        interval = timed_words.get(hyp_index)
        if interval is None:
            unknown["untimed"] += 1
            continue
        hypothesis_label = _word_turn_label(interval, hypothesis_turns, label_map)
        if hypothesis_label is None:
            unknown["turn_or_overlap"] += 1
            continue
        labels = reference_labels[hyp_index]
        ref_start, ref_end = interval
        competing_reference = {
            speaker for left, right, speaker in reference_rows
            if left < ref_end and right > ref_start and speaker not in labels
        }
        if len(labels) != 1 or competing_reference:
            unknown["reference_ambiguous"] += 1
            continue
        compared += 1
        if hypothesis_label == next(iter(labels)):
            agreement += 1
        else:
            disagreement += 1

    matched_token_count = len(matched_pairs)
    matched_word_count = len(matched_words)
    result = {
        "comparison_kind": "anonymous vendor reference; not word-level ground truth",
        "duration_seconds": duration,
        "reference_time_unit": reference_time_unit,
        "minimum_exact_match_run_tokens": _MIN_MATCH_TOKENS,
        "global_label_mapping": label_map,
        "canonical_word_count": len(canonical_tokens),
        "anchored_word_count": len(timed_words),
        "untimed_word_count": len(canonical_tokens) - len(timed_words),
        "unsupported_multiword_anchor_count": unsupported_anchors,
        "unsupported_multiword_word_count": unsupported_words,
        "reference_token_count": len(reference_tokens),
        "matched_token_pair_count": matched_token_count,
        "matched_reference_token_count": len(matched_reference_indices),
        "matched_canonical_word_count": matched_word_count,
        "unmatched_canonical_word_count": len(canonical_tokens) - matched_word_count,
        "unknown_word_counts": unknown,
        "compared_word_count": compared,
        "label_agreement_count": agreement,
        "label_disagreement_count": disagreement,
    }
    if provenance is not None:
        result["provenance"] = provenance
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reference-time-unit", choices=("seconds", "milliseconds"),
                        required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    result = evaluate_word_speakers(
        payload["canonical_text"], payload["word_anchors"], payload["turns"],
        payload["reference_utterances"], duration_seconds=payload["duration_seconds"],
        reference_time_unit=args.reference_time_unit,
        provenance=payload.get("provenance"),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
