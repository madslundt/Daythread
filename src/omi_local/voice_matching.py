"""Conservative, conversation-balanced voice suggestions and evaluation report gates.

Cosine scores measure similarity, never the probability that an identity is correct.
Only explicitly confirmed examples may enter the gallery. Callers enforce retained
source evidence and audio-quality requirements before saving those examples.
"""

import hashlib
import json
import math
from collections import defaultdict
from numbers import Real

MATCH_POLICY = "conversation-centroids-v3"
REPORT_SCHEMA = 1
QUALITY_POLICY = {"min_clean_seconds": 10.0, "max_overlap_ratio": 0.1,
                  "max_clipped_ratio": 0.01}
REPORT_CRITERIA = {"min_precision": 0.95, "min_known_coverage": 0.5,
                   "min_accepted": 30, "min_test_conversations": 5,
                   "min_unknown_cases": 10, "max_unknown_false_accepts": 0}
THRESHOLDS = tuple(round(value / 100, 2) for value in range(50, 101))
MARGINS = tuple(round(value / 100, 2) for value in range(2, 51, 2))


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def validate_vector(vector, dimension: int) -> tuple[float, ...]:
    if type(dimension) is not int or not 1 <= dimension <= 4096:
        raise ValueError("Unsupported embedding dimension")
    if not isinstance(vector, (list, tuple)) or len(vector) != dimension:
        raise ValueError("Embedding dimension does not match its model")
    if any(isinstance(value, bool) or not isinstance(value, Real)
           or not math.isfinite(value) for value in vector):
        raise ValueError("Embedding must contain finite numbers")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Embedding has no usable magnitude")
    return tuple(float(value / norm) for value in vector)


def validate_model(model: dict) -> dict:
    if not isinstance(model, dict) or set(model) != {
        "id", "revision", "dimension", "normalization",
    }:
        raise ValueError("Complete immutable embedding model descriptor required")
    for field in ("id", "revision"):
        if not isinstance(model[field], str) or not model[field].strip() or len(model[field]) > 300:
            raise ValueError("Invalid embedding model descriptor")
    if len(model["revision"]) != 64 or any(char not in "0123456789abcdef"
                                            for char in model["revision"]):
        raise ValueError("Embedding model revision must be its immutable SHA-256 fingerprint")
    if model["normalization"] != "l2-v1":
        raise ValueError("Unsupported embedding normalization")
    if type(model["dimension"]) is not int or not 1 <= model["dimension"] <= 4096:
        raise ValueError("Unsupported embedding dimension")
    return dict(model)


def _identifier(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError(f"Invalid {field}")
    return value


def _source_tokens(example: dict, field: str) -> list[str]:
    values = example.get(field, [])
    if not isinstance(values, (list, tuple)):
        raise ValueError("Invalid gallery source provenance")
    return sorted({_sha(value) if field == "source_digests"
                   else _identifier(value, "source ID") for value in values})


def gallery_fingerprint(gallery: list[dict], model: dict | None = None) -> str:
    """Bind a report to exact example IDs, owners, conversations, vectors and model."""
    if model is not None:
        validate_model(model)
    examples = []
    for example in gallery:
        if "confirmed" in example and example["confirmed"] is not True:
            continue
        descriptor = example.get("model", model)
        if descriptor is not None:
            validate_model(descriptor)
            if model is not None and descriptor != model:
                raise ValueError("Gallery uses a different embedding model")
        vector = example["embedding"]
        validate_vector(vector, descriptor["dimension"] if descriptor else len(vector))
        examples.append({"id": _identifier(example["id"], "example ID"),
                         "profile_id": _identifier(example["profile_id"], "profile ID"),
                         "conversation_id": _identifier(example["conversation_id"],
                                                        "conversation ID"),
                         "embedding": vector, "model": descriptor,
                         "source_ids": _source_tokens(example, "source_ids"),
                         "source_digests": _source_tokens(example, "source_digests")})
    if len({example["id"] for example in examples}) != len(examples):
        raise ValueError("Duplicate gallery example ID")
    return canonical_hash({"model": model, "examples": sorted(examples, key=lambda e: e["id"])})


def _conversation_supports(gallery: list[dict]) -> dict[str, list[list[dict]]]:
    """Group confirmed conversations transitively sharing any original source."""
    grouped = defaultdict(lambda: defaultdict(list))
    provenance = defaultdict(lambda: defaultdict(set))
    for example in gallery:
        if "confirmed" in example and example["confirmed"] is not True:
            continue
        owner = _identifier(example["profile_id"], "profile ID")
        conversation = _identifier(example["conversation_id"], "conversation ID")
        grouped[owner][conversation].append(example)
        for field in ("source_ids", "source_digests"):
            for token in _source_tokens(example, field):
                provenance[owner][conversation].add(field + ":" + token)
    supports = {}
    for owner, conversations in grouped.items():
        parent = {key: key for key in conversations}

        def find(key):
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        seen = {}
        for conversation in conversations:
            for token in provenance[owner][conversation]:
                if token in seen:
                    parent[find(conversation)] = find(seen[token])
                else:
                    seen[token] = conversation
        components = defaultdict(list)
        for conversation, examples in conversations.items():
            components[find(conversation)].extend(examples)
        supports[owner] = list(components.values())
    return supports


def independent_conversation_counts(gallery: list[dict]) -> dict[str, int]:
    """Count independent retained supports using the matcher's exact provenance rule."""
    return {owner: len(supports) for owner, supports in _conversation_supports(gallery).items()}


def rank_voice(vector, gallery: list[dict]) -> dict | None:
    """Rank identities; an underenrolled identity can block but cannot win a suggestion."""
    dimension = len(vector) if isinstance(vector, (list, tuple)) else 0
    query = validate_vector(vector, dimension)
    grouped = _conversation_supports(gallery)
    descriptor = None
    scores = []
    for owner, conversations in grouped.items():
        support = []
        for examples in conversations:
            vectors = []
            for example in examples:
                if example.get("model") is not None:
                    candidate_model = validate_model(example["model"])
                    if candidate_model["dimension"] != dimension or (descriptor is not None
                            and candidate_model != descriptor):
                        raise ValueError("Gallery uses a different embedding model")
                    descriptor = candidate_model
                vectors.append(validate_vector(example["embedding"], dimension))
            mean = tuple(math.fsum(column) / len(vectors) for column in zip(*vectors, strict=True))
            try:
                centroid = validate_vector(mean, dimension)
            except ValueError:
                # Contradictory examples cannot create positive identity evidence.
                support.append(-1.0)
                continue
            support.append(max(-1.0, min(1.0, math.fsum(
                a * b for a, b in zip(query, centroid, strict=True)
            ))))
        scores.append((math.fsum(support) / len(support), owner))
    if not scores:
        return None
    scores.sort(key=lambda item: (-item[0], item[1]))
    best, owner = scores[0]
    if len(grouped[owner]) < 2:
        return None
    runner_up = scores[1][0] if len(scores) > 1 else -1.0
    return {"profile_id": owner, "score": best, "margin": best - runner_up}


def match_voice(vector, gallery: list[dict], threshold: float, margin: float) -> dict | None:
    for value, lower, upper in ((threshold, -1, 1), (margin, 0, 2)):
        if isinstance(value, bool) or not isinstance(value, Real) \
                or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError("Invalid voice matching threshold or margin")
    ranked = rank_voice(vector, gallery)
    if ranked is None or ranked["score"] < threshold or ranked["margin"] < margin:
        return None
    # An exact runner-up tie is ambiguous even when the configured margin is zero.
    return ranked if ranked["margin"] > 1e-12 else None


def quality_eligible(voice: dict) -> bool:
    values = [voice.get(key) for key in ("clean_seconds", "overlap_ratio", "clipped_ratio")]
    if any(isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
           for value in values):
        raise ValueError("Complete finite voice quality measurements required")
    seconds, overlap, clipped = values
    if seconds < 0 or not 0 <= overlap <= 1 or not 0 <= clipped <= 1:
        raise ValueError("Invalid voice quality measurements")
    return seconds >= 10 and overlap <= 0.1 and clipped <= 0.01


def wilson(successes: int, count: int) -> list[float] | None:
    """Two-sided 95% Wilson interval for an observed binomial proportion."""
    if not count:
        return None
    z = 1.959963984540054
    rate = successes / count
    denominator = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def evaluation_metrics(cases: list[dict], threshold: float, margin: float) -> dict:
    accepted = [case for case in cases if case["eligible"] and case["ranking"] is not None
                and case["ranking"]["score"] >= threshold
                and case["ranking"]["margin"] >= margin
                and case["ranking"]["margin"] > 1e-12]
    correct = sum(case["expected"] is not None
                  and case["ranking"]["profile_id"] == case["expected"] for case in accepted)
    known = sum(case["expected"] is not None for case in cases)
    unknown = sum(case["expected"] is None and case["eligible"] for case in cases)
    false_unknown = sum(case["expected"] is None for case in accepted)
    return {"cases": len(cases), "known_cases": known, "eligible_unknown_cases": unknown,
            "accepted": len(accepted), "correct": correct,
            "incorrect": len(accepted) - correct, "unknown_false_accepts": false_unknown,
            "precision": correct / len(accepted) if accepted else None,
            "known_coverage": correct / known if known else 0.0,
            "test_conversations": len({case["conversation_id"] for case in accepted
                                       if case["expected"] is not None
                                       and case["ranking"]["profile_id"] == case["expected"]}),
            "eligible_test_conversations": len({case["conversation_id"] for case in cases
                                                if case["eligible"]}),
            "precision_wilson_95": wilson(correct, len(accepted)),
            "known_coverage_wilson_95": wilson(correct, known),
            "unknown_false_accept_rate_wilson_95": wilson(false_unknown, unknown)}


def meets_criteria(metrics: dict) -> bool:
    return (metrics["precision"] is not None
            and metrics["precision"] >= REPORT_CRITERIA["min_precision"]
            and metrics["known_coverage"] >= REPORT_CRITERIA["min_known_coverage"]
            and metrics["accepted"] >= REPORT_CRITERIA["min_accepted"]
            and metrics["test_conversations"] >= REPORT_CRITERIA["min_test_conversations"]
            and metrics["eligible_unknown_cases"] >= REPORT_CRITERIA["min_unknown_cases"]
            and metrics["unknown_false_accepts"] <= REPORT_CRITERIA["max_unknown_false_accepts"])


def calibrate(cases: list[dict]) -> tuple[float, float]:
    if len({case["conversation_id"] for case in cases}) < 2 \
            or not any(case["eligible"] and case["expected"] is not None for case in cases) \
            or not any(case["eligible"] and case["expected"] is None for case in cases):
        raise ValueError("Calibration needs known and unknown voices from separate conversations")
    candidates = []
    for threshold in THRESHOLDS:
        for margin in MARGINS:
            metrics = evaluation_metrics(cases, threshold, margin)
            if metrics["accepted"] and metrics["precision"] >= 0.95 \
                    and metrics["unknown_false_accepts"] == 0:
                candidates.append((metrics["correct"], -metrics["incorrect"], threshold, margin))
    if not candidates:
        raise ValueError("Calibration found no usable conservative matching policy")
    _, _, threshold, margin = max(candidates)
    return threshold, margin


def _sha(value) -> str:
    if not isinstance(value, str) or len(value) != 64 \
            or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Invalid audio fingerprint")
    return value


def _conversation(value: str) -> str:
    value = _identifier(value, "conversation ID")
    return value.split(":", 1)[1] if value.startswith(("source:", "meeting:")) else value


def report_cases(recording: dict, entry: dict, gallery: list[dict], model: dict) -> list[dict]:
    """Validate actual runtime output against complete human cluster ground truth."""
    for field in ("conversation_id", "split", "audio_sha256", "pcm_sha256"):
        if recording[field] != entry[field]:
            raise ValueError("Recording results do not match the manifest")
    duration = recording["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, Real) \
            or not math.isfinite(duration) or not 0 < duration <= 28800:
        raise ValueError("Invalid recording duration")
    response = recording["response"]
    if response.get("embedding_model") != model:
        raise ValueError("Diarization uses a different embedding model")
    turns = response["turns"]
    if not isinstance(turns, list) or len(turns) > 100000:
        raise ValueError("Invalid diarization turns")
    labels = set()
    for turn in turns:
        start, end = turn["start"], turn["end"]
        if any(isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
               for value in (start, end)) or not 0 <= start < end <= duration + 0.001:
            raise ValueError("Diarization turn is outside the recording")
        labels.add(_identifier(turn["speaker"], "speaker label"))
    expected = entry["speakers"]
    if not isinstance(expected, dict) or set(expected) != labels:
        raise ValueError("Ground truth must label every runtime speaker exactly once")
    for label, identity in expected.items():
        _identifier(label, "speaker label")
        if identity is not None:
            _identifier(identity, "ground truth identity")
    voices = response.get("voices", [])
    if not isinstance(voices, list) or len(voices) > len(labels):
        raise ValueError("Invalid runtime voice examples")
    by_label = {}
    for voice in voices:
        label = voice["speaker"]
        if label not in labels or label in by_label:
            raise ValueError("Runtime voice labels do not match its turns")
        vector = validate_vector(voice["embedding"], model["dimension"])
        if not math.isclose(math.hypot(*voice["embedding"]), 1, rel_tol=1e-4, abs_tol=1e-6):
            raise ValueError("Runtime embeddings must use l2-v1 normalization")
        eligible = quality_eligible(voice)
        if voice["clean_seconds"] > duration + 0.001:
            raise ValueError("Voice quality duration exceeds recording duration")
        by_label[label] = {"eligible": eligible,
                           "ranking": rank_voice(vector, gallery) if eligible else None}
    return [{"conversation_id": entry["conversation_id"], "speaker": label,
             "expected": identity, **by_label.get(label, {"eligible": False, "ranking": None})}
            for label, identity in sorted(expected.items())]


def verify_partitions(manifest: list[dict], gallery: list[dict],
                      gallery_pcm_hashes: list[str] = ()) -> None:
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("A nonempty calibration/test manifest is required")
    gallery_conversations = {_conversation(example["conversation_id"]) for example in gallery
                             if example.get("confirmed", True) is True}
    gallery_audio = {_sha(digest) for example in gallery for digest in
                     example.get("source_digests", []) if example.get("confirmed", True) is True}
    gallery_pcm = {_sha(digest) for digest in gallery_pcm_hashes}
    conversations, audio_hashes, pcm_hashes, splits = set(), set(), set(), set()
    for entry in manifest:
        if set(entry) != {"path", "conversation_id", "split", "speakers", "audio_sha256",
                          "pcm_sha256"}:
            raise ValueError("Unexpected or missing manifest fields")
        _identifier(entry["path"], "audio path")
        conversation = _conversation(entry["conversation_id"])
        if conversation in conversations or conversation in gallery_conversations:
            raise ValueError("Gallery, calibration and test conversations must be disjoint")
        conversations.add(conversation)
        if entry["split"] not in {"calibration", "test"}:
            raise ValueError("Manifest split must be calibration or test")
        splits.add(entry["split"])
        audio, pcm = _sha(entry["audio_sha256"]), _sha(entry["pcm_sha256"])
        if audio in audio_hashes or audio in gallery_audio \
                or pcm in pcm_hashes or pcm in gallery_pcm:
            raise ValueError("Repeated audio across gallery, calibration or test")
        audio_hashes.add(audio)
        pcm_hashes.add(pcm)
        if not isinstance(entry["speakers"], dict):
            raise ValueError("Complete speaker ground truth map required")
    if splits != {"calibration", "test"}:
        raise ValueError("Both calibration and untouched test partitions are required")


def build_report(manifest: list[dict], recordings: list[dict], gallery: list[dict], model: dict,
                 gallery_pcm_hashes: list[str] = ()) -> dict:
    validate_model(model)
    fingerprint = gallery_fingerprint(gallery, model)
    verify_partitions(manifest, gallery, gallery_pcm_hashes)
    if not isinstance(recordings, list) or len(recordings) != len(manifest):
        raise ValueError("Every manifest recording requires runtime results")
    cases = {"calibration": [], "test": []}
    for entry, recording in zip(manifest, recordings, strict=True):
        cases[entry["split"]].extend(report_cases(recording, entry, gallery, model))
    threshold, margin = calibrate(cases["calibration"])
    calibration_metrics = evaluation_metrics(cases["calibration"], threshold, margin)
    metrics = evaluation_metrics(cases["test"], threshold, margin)
    report = {"schema_version": REPORT_SCHEMA, "match_policy": MATCH_POLICY,
              "provenance": "local-diarize-wav-v1", "embedding_model": model,
              "gallery_fingerprint": fingerprint, "manifest": manifest,
              "manifest_fingerprint": canonical_hash(manifest),
              "gallery_pcm_hashes": sorted(set(gallery_pcm_hashes)),
              "recordings": recordings, "threshold": threshold, "margin": margin,
              "quality_policy": QUALITY_POLICY, "criteria": REPORT_CRITERIA,
              "calibration_metrics": calibration_metrics, "metrics": metrics,
              "passed": meets_criteria(metrics),
              "interpretation": "Observed held-out metrics, not a guarantee of future accuracy. "
              "Cosine similarity is not an identity probability. Wilson intervals describe "
              "binomial sampling uncertainty; recording and speaker correlations can reduce "
              "effective sample size. Mocked or synthetic runs are software checks only."}
    report["report_sha256"] = canonical_hash(report)
    return report


def validate_report(report: dict, gallery: list[dict], model: dict) -> tuple[bool, str]:
    """Fail closed, recomputing the policy and observed outcomes instead of trusting passed.

    The checksum detects incomplete/accidental edits; this is not a signature or
    independent proof of ground truth. The operator must protect the configured
    report and archive, and produce the report with the real-WAV evaluator.
    """
    try:
        if not isinstance(report, dict) or type(report.get("schema_version")) is not int \
                or report["schema_version"] != REPORT_SCHEMA:
            return False, "Unsupported evaluation report schema"
        if report.get("embedding_model") != validate_model(model):
            return False, "Evaluation embedding model changed"
        if report.get("gallery_fingerprint") != gallery_fingerprint(gallery, model):
            return False, "Confirmed voice gallery changed; evaluate again"
        supplied_hash = report.get("report_sha256")
        if supplied_hash != canonical_hash({k: v for k, v in report.items()
                                            if k != "report_sha256"}):
            return False, "Evaluation report checksum does not match"
        rebuilt = build_report(report["manifest"], report["recordings"], gallery, model,
                               report["gallery_pcm_hashes"])
        if canonical_hash(report) != canonical_hash(rebuilt):
            return False, "Evaluation report policy, metrics or completeness changed"
        if report["passed"] is not True:
            return False, "Held-out evaluation does not meet suggestion criteria"
        return True, "Passed observed held-out criteria; future accuracy is not guaranteed"
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as exc:
        return False, f"Invalid or incomplete evaluation report: {exc}"
