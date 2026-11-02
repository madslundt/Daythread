"""Compare anonymous diarization tracks after one global speaker-label mapping.

Example:
  python scripts/compare_diarization.py --hypothesis nemo.json --reference plaud.json \
      --duration 1122.28 --output /tmp/vendor-comparison.json

This is a vendor-reference comparison, not a human-reference DER score.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _rows(payload: Any, reference: bool) -> list[tuple[float, float, str]]:
    if reference and isinstance(payload, dict):
        rows = payload.get("plaud_transcript", payload.get("turns"))
    elif isinstance(payload, dict):
        rows = payload.get("segments", payload.get("turns"))
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("Expected a list of timed speaker segments")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid speaker segment")
        if reference:
            start = row.get("start_time", row.get("start"))
            end = row.get("end_time", row.get("end"))
            speaker = row.get("speaker")
        else:
            start, end, speaker = row.get("start"), row.get("end"), row.get("speaker")
        if (isinstance(start, bool) or not isinstance(start, (int, float))
                or not math.isfinite(start) or start < 0
                or isinstance(end, bool) or not isinstance(end, (int, float))
                or not math.isfinite(end) or end <= start
                or not isinstance(speaker, (str, int)) or isinstance(speaker, bool)):
            raise ValueError("Invalid speaker segment")
        result.append((float(start), float(end), str(speaker)))
    return result


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _intersection(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> float:
    total = 0.0
    a = b = 0
    while a < len(left) and b < len(right):
        total += max(0.0, min(left[a][1], right[b][1]) - max(left[a][0], right[b][0]))
        if left[a][1] <= right[b][1]:
            a += 1
        else:
            b += 1
    return total


def _global_mapping(
    hypothesis: dict[str, list[tuple[float, float]]],
    reference: dict[str, list[tuple[float, float]]],
) -> tuple[dict[str, str], float]:
    """Maximum-overlap one-to-one mapping. Labels never change within a recording."""
    predicted, actual = sorted(hypothesis), sorted(reference)
    if not predicted or not actual:
        return {}, 0.0
    matrix = [[_intersection(hypothesis[p], reference[r]) for r in actual] for p in predicted]
    best_score = -1.0
    best: dict[str, str] = {}
    size = min(len(predicted), len(actual))
    predicted_subsets = itertools.combinations(range(len(predicted)), size)
    for indices in predicted_subsets:
        for chosen in itertools.permutations(range(len(actual)), size):
            mapping = {predicted[i]: actual[j] for i, j in zip(indices, chosen, strict=True)}
            score = sum(matrix[i][actual.index(mapping[predicted[i]])]
                        for i in indices)
            if score > best_score:
                best_score, best = score, mapping
    return best, best_score


def _boundary_matches(
    hypothesis: list[tuple[float, float, str]], reference: list[tuple[float, float, str]],
    mapping: dict[str, str], tolerance: float,
) -> dict[str, Any]:
    hyp_edges = defaultdict(list)
    ref_edges = defaultdict(list)
    for start, end, speaker in hypothesis:
        if speaker in mapping:
            hyp_edges[mapping[speaker]].extend((start, end))
    total_hypothesis = 2 * len(hypothesis)
    for start, end, speaker in reference:
        ref_edges[speaker].extend((start, end))
    differences = []
    matches = total_reference = 0
    for speaker, ref_times in ref_edges.items():
        hyp_times = hyp_edges.get(speaker, [])
        total_reference += len(ref_times)
        candidates = sorted((abs(r - h), ri, hi) for ri, r in enumerate(ref_times)
                            for hi, h in enumerate(hyp_times) if abs(r - h) <= tolerance)
        used_ref, used_hyp = set(), set()
        for delta, ri, hi in candidates:
            if ri not in used_ref and hi not in used_hyp:
                used_ref.add(ri)
                used_hyp.add(hi)
                differences.append(delta)
        matches += len(used_ref)
    differences.sort()
    median = (differences[len(differences) // 2] if differences else None)
    return {"tolerance_seconds": tolerance, "matched_boundaries": matches,
            "reference_boundaries": total_reference, "hypothesis_boundaries": total_hypothesis,
            "precision": matches / total_hypothesis if total_hypothesis else 0.0,
            "recall": matches / total_reference if total_reference else 0.0,
            "median_absolute_error_seconds": median}


def _activity_mismatches(
    hypothesis: list[tuple[float, float, str]], reference: list[tuple[float, float, str]],
    mapping: dict[str, str], duration: float, step: float = 0.1,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    hyp_by_speaker = defaultdict(list)
    ref_by_speaker = defaultdict(list)
    for start, end, speaker in hypothesis:
        mapped = mapping.get(speaker, "__unmapped_hypothesis__" + speaker)
        hyp_by_speaker[mapped].append((start, end))
    for start, end, speaker in reference:
        ref_by_speaker[speaker].append((start, end))
    labels = set(hyp_by_speaker) | set(ref_by_speaker)
    hyp_by_speaker = {k: _merge(v) for k, v in hyp_by_speaker.items()}
    ref_by_speaker = {k: _merge(v) for k, v in ref_by_speaker.items()}
    hit = hyp_seconds = ref_seconds = 0.0
    activity_tp = activity_fp = activity_fn = 0.0
    collar_tp = collar_fp = collar_fn = 0.0
    reference_boundaries = [edge for start, end, _ in reference for edge in (start, end)]
    shared_speech = matched_speaker_seconds = shared_speaker_slots = 0.0
    hyp_overlap = ref_overlap = overlap_tp = overlap_fp = overlap_fn = 0.0
    mismatches = []
    start = None
    for index in range(math.ceil(duration / step)):
        left, right = index * step, min(duration, (index + 1) * step)
        mid = (left + right) / 2
        hyp_active = {s for s in labels if any(a <= mid < b for a, b in hyp_by_speaker.get(s, []))}
        ref_active = {s for s in labels if any(a <= mid < b for a, b in ref_by_speaker.get(s, []))}
        width = right - left
        hit += width * len(hyp_active & ref_active)
        hyp_seconds += width * len(hyp_active)
        ref_seconds += width * len(ref_active)
        hyp_speech, ref_speech = bool(hyp_active), bool(ref_active)
        activity_tp += width * (hyp_speech and ref_speech)
        activity_fp += width * (hyp_speech and not ref_speech)
        activity_fn += width * (ref_speech and not hyp_speech)
        if not any(abs(mid - edge) <= 0.25 for edge in reference_boundaries):
            collar_tp += width * (hyp_speech and ref_speech)
            collar_fp += width * (hyp_speech and not ref_speech)
            collar_fn += width * (ref_speech and not hyp_speech)
        if hyp_speech and ref_speech:
            shared_speech += width
            shared_speaker_slots += width * len(ref_active)
            matched_speaker_seconds += width * len(hyp_active & ref_active)
        hyp_is_overlap, ref_is_overlap = len(hyp_active) > 1, len(ref_active) > 1
        hyp_overlap += width * hyp_is_overlap
        ref_overlap += width * ref_is_overlap
        overlap_tp += width * (hyp_is_overlap and ref_is_overlap)
        overlap_fp += width * (hyp_is_overlap and not ref_is_overlap)
        overlap_fn += width * (ref_is_overlap and not hyp_is_overlap)
        if hyp_active != ref_active and start is None:
            start = left
        elif hyp_active == ref_active and start is not None:
            mismatches.append((start, left))
            start = None
    if start is not None:
        mismatches.append((start, duration))
    precision = hit / hyp_seconds if hyp_seconds else 0.0
    recall = hit / ref_seconds if ref_seconds else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    speech_precision = (
        activity_tp / (activity_tp + activity_fp) if activity_tp + activity_fp else 0.0
    )
    speech_recall = (
        activity_tp / (activity_tp + activity_fn) if activity_tp + activity_fn else 0.0
    )
    speech_f1 = (2 * speech_precision * speech_recall / (speech_precision + speech_recall)
                 if speech_precision + speech_recall else 0.0)
    collar_precision = collar_tp / (collar_tp + collar_fp) if collar_tp + collar_fp else 0.0
    collar_recall = collar_tp / (collar_tp + collar_fn) if collar_tp + collar_fn else 0.0
    collar_f1 = (2 * collar_precision * collar_recall / (collar_precision + collar_recall)
                 if collar_precision + collar_recall else 0.0)
    overlap_precision = overlap_tp / (overlap_tp + overlap_fp) if overlap_tp + overlap_fp else 0.0
    overlap_recall = overlap_tp / (overlap_tp + overlap_fn) if overlap_tp + overlap_fn else 0.0
    summary = {"global_mapped_activity_precision": precision,
               "global_mapped_activity_recall": recall,
               "global_mapped_activity_f1": f1,
               "hypothesis_speaker_seconds": hyp_seconds,
               "reference_speaker_seconds": ref_seconds,
               "shared_speaker_seconds": hit,
               "speech_activity_detection": {
                   "precision": speech_precision, "recall": speech_recall, "f1": speech_f1,
                   "true_positive_seconds": activity_tp, "false_positive_seconds": activity_fp,
                   "missed_speech_seconds": activity_fn,
                   "collar_250ms": {"precision": collar_precision, "recall": collar_recall,
                                    "f1": collar_f1}},
               "speaker_label_confusion_on_shared_speech": {
                   "shared_speech_seconds": shared_speech,
                   "speaker_slots_scored": shared_speaker_slots,
                   "correctly_mapped_speaker_seconds": matched_speaker_seconds,
                   "confused_speaker_seconds": max(0.0, shared_speaker_slots
                                                    - matched_speaker_seconds),
                   "agreement": (matched_speaker_seconds / shared_speaker_slots
                                if shared_speaker_slots else 0.0)},
               "overlap_activity": {
                   "hypothesis_overlap_seconds": hyp_overlap,
                   "reference_overlap_seconds": ref_overlap,
                   "precision": overlap_precision, "recall": overlap_recall},
               "disagreement_intervals": len(mismatches)}
    examples = [
        {"start": round(a, 2), "end": round(b, 2), "seconds": round(b - a, 2)}
        for a, b in sorted(mismatches, key=lambda pair: pair[1] - pair[0], reverse=True)[:30]
    ]
    return summary, examples


def compare(hypothesis_payload: Any, reference_payload: Any, duration: float) -> dict[str, Any]:
    hypothesis = _rows(hypothesis_payload, False)
    reference = _rows(reference_payload, True)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Duration must be positive and finite")
    # Plaud's export stores millisecond offsets; native diarizer JSON uses seconds.
    if reference and max(end for _, end, _ in reference) > duration + 1:
        reference = [(start / 1000, end / 1000, speaker) for start, end, speaker in reference]
    for rows in (hypothesis, reference):
        if any(end > duration + 0.25 for _, end, _ in rows):
            raise ValueError("Segment exceeds the supplied recording duration")
    hypothesis_tracks = {speaker: _merge([(a, b) for a, b, s in hypothesis if s == speaker])
                         for speaker in {s for _, _, s in hypothesis}}
    reference_tracks = {speaker: _merge([(a, b) for a, b, s in reference if s == speaker])
                        for speaker in {s for _, _, s in reference}}
    mapping, matched_seconds = _global_mapping(hypothesis_tracks, reference_tracks)
    activity, examples = _activity_mismatches(hypothesis, reference, mapping, duration)
    boundary_tolerances = [0.25, 0.5]
    return {"comparison_kind": "vendor-reference; not human-reference DER",
            "hypothesis_segment_count": len(hypothesis), "reference_segment_count": len(reference),
            "hypothesis_speaker_count": len(hypothesis_tracks),
            "reference_speaker_count": len(reference_tracks),
            "global_label_mapping": {
                f"hypothesis_{i + 1}": f"reference_{sorted(reference_tracks).index(target) + 1}"
                for i, (source, target) in enumerate(sorted(mapping.items()))
            },
            "global_mapping_shared_seconds": matched_seconds,
            **activity,
            "boundary_agreement": {
                f"boundary_within_{int(tolerance * 1000)}ms": _boundary_matches(
                    hypothesis, reference, mapping, tolerance,
                ) for tolerance in boundary_tolerances
            },
            "longest_disagreements": examples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hypothesis", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare(_read(args.hypothesis), _read(args.reference), args.duration)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
