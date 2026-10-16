"""Software checks using synthetic vectors; these are never accuracy evidence."""

import copy
import math

import pytest

from omi_local.voice_matching import (
    build_report,
    canonical_hash,
    gallery_fingerprint,
    independent_conversation_counts,
    match_voice,
    quality_eligible,
    rank_voice,
    validate_model,
    validate_report,
    validate_vector,
    wilson,
)

MODEL = {"id": "fixture/embedding", "revision": "a" * 64,
         "dimension": 2, "normalization": "l2-v1"}


def gallery():
    return [{"id": f"{owner}-{conversation}", "profile_id": owner,
             "conversation_id": f"gallery-{owner}-{conversation}", "embedding": vector,
             "model": dict(MODEL), "source_digests": [canonical_hash([owner, conversation])]}
            for owner, vector in (("mads", [1.0, 0.0]), ("alice", [0.0, 1.0]))
            for conversation in range(2)]


@pytest.mark.parametrize("vector,dimension", [
    ([], 0), ([0, 0], 2), ([1], 2), ([1, True], 2), ([1, "0"], 2),
    ([math.nan, 0], 2), ([math.inf, 0], 2), ("12", 2), ([1], True),
])
def test_vectors_fail_closed(vector, dimension):
    with pytest.raises(ValueError):
        validate_vector(vector, dimension)


def test_cosine_normalizes_and_requires_score_and_runner_up_margin():
    assert validate_vector([3, 4], 2) == (0.6, 0.8)
    assert match_voice([9, 0], gallery(), 0.9, 0.2) == {
        "profile_id": "mads", "score": 1.0, "margin": 1.0,
    }
    assert match_voice([0.7, 0.7], gallery(), 0.5, 0) is None
    assert match_voice([0.8, 0.6], gallery(), 0.9, 0.1) is None
    assert match_voice([0.8, 0.6], gallery(), 0.7, 0.3) is None


@pytest.mark.parametrize("threshold,margin", [(math.nan, 0.1), (0.5, math.inf),
                                              (True, 0.1), (0.5, -0.1), (1.1, 0)])
def test_invalid_matching_policy_is_rejected(threshold, margin):
    with pytest.raises(ValueError):
        match_voice([1, 0], gallery(), threshold, margin)


def test_two_confirmed_examples_from_distinct_conversations_required():
    examples = gallery()[:2]
    examples[1]["conversation_id"] = examples[0]["conversation_id"]
    assert rank_voice([1, 0], examples) is None
    examples[1]["conversation_id"] = "second"
    examples[1]["confirmed"] = False
    assert rank_voice([1, 0], examples) is None
    examples[1]["confirmed"] = True
    assert match_voice([1, 0], examples, 0.9, 0.1)["profile_id"] == "mads"


def test_many_examples_in_one_conversation_do_not_outvote_another():
    examples = gallery()[:2]
    examples[1]["embedding"] = [0, 1]
    examples.extend({**examples[0], "id": f"duplicate-{i}"} for i in range(100))
    assert rank_voice([1, 0], examples)["score"] == 0.5
    assert match_voice([1, 0], examples, 0.8, 0.1) is None


def test_gallery_fingerprint_binds_raw_examples_and_model_but_not_order():
    examples = gallery()
    fingerprint = gallery_fingerprint(examples, MODEL)
    assert gallery_fingerprint(list(reversed(examples)), MODEL) == fingerprint
    for key, value in (("id", "changed"), ("profile_id", "changed"),
                       ("conversation_id", "changed"), ("embedding", [0.99, 0.01])):
        modified = copy.deepcopy(examples)
        modified[0][key] = value
        assert gallery_fingerprint(modified, MODEL) != fingerprint
    changed_model = {**MODEL, "revision": "b" * 64}
    with pytest.raises(ValueError, match="different"):
        gallery_fingerprint(examples, changed_model)
    with pytest.raises(ValueError, match="Duplicate"):
        gallery_fingerprint(examples + [examples[0]], MODEL)


@pytest.mark.parametrize("change", [{"normalization": "none"}, {"dimension": True},
                                    {"revision": "latest"}, {"revision": ""}])
def test_unsupported_model_is_rejected(change):
    with pytest.raises(ValueError):
        validate_model({**MODEL, **change})


def test_quality_thresholds_and_wilson_uncertainty():
    voice = {"clean_seconds": 10, "overlap_ratio": 0.1, "clipped_ratio": 0.01}
    assert quality_eligible(voice)
    for key, value in (("clean_seconds", 9.99), ("overlap_ratio", 0.1001),
                       ("clipped_ratio", 0.0101)):
        assert not quality_eligible({**voice, key: value})
    with pytest.raises(ValueError):
        quality_eligible({**voice, "overlap_ratio": math.nan})
    assert wilson(0, 0) is None
    assert wilson(30, 30)[0] == pytest.approx(0.8864866)
    assert wilson(0, 10)[1] > 0.27


def report_fixture():
    manifest, recordings = [], []
    for split, count in (("calibration", 2), ("test", 5)):
        for index in range(count):
            labels = {f"known-{i}": "mads" for i in range(6)} | {
                "unknown-0": None, "unknown-1": None,
            }
            entry = {"path": f"{split}-{index}.wav", "conversation_id": f"{split}-{index}",
                     "split": split, "speakers": labels,
                     "audio_sha256": canonical_hash([split, index, "audio"]),
                     "pcm_sha256": canonical_hash([split, index, "pcm"])}
            voices = [{"speaker": label, "embedding": [1.0, 0.0] if identity else [-1.0, 0.0],
                       "clean_seconds": 10, "overlap_ratio": 0, "clipped_ratio": 0}
                      for label, identity in labels.items()]
            response = {"embedding_model": MODEL, "voices": voices, "turns": [
                {"speaker": label, "start": i * 10, "end": (i + 1) * 10}
                for i, label in enumerate(labels)
            ]}
            manifest.append(entry)
            recordings.append({key: entry[key] for key in (
                "conversation_id", "split", "audio_sha256", "pcm_sha256",
            )} | {"duration_seconds": 80, "response": response})
    return manifest, recordings


def test_report_validator_recomputes_observed_metrics_and_policy():
    manifest, recordings = report_fixture()
    report = build_report(manifest, recordings, gallery(), MODEL)
    assert report["passed"]
    assert report["metrics"]["accepted"] == 30
    assert report["metrics"]["eligible_unknown_cases"] == 10
    assert report["metrics"]["unknown_false_accepts"] == 0
    assert report["metrics"]["precision_wilson_95"][0] < 0.9
    assert validate_report(report, gallery(), MODEL)[0]
    for field, value in (("passed", False), ("threshold", 0.1),
                         ("metrics", {"precision": 1.0})):
        edited = copy.deepcopy(report)
        edited[field] = value
        edited["report_sha256"] = canonical_hash({k: v for k, v in edited.items()
                                                  if k != "report_sha256"})
        assert not validate_report(edited, gallery(), MODEL)[0]
    examples = gallery()
    examples.pop()
    assert not validate_report(report, examples, MODEL)[0]
    assert not validate_report({"passed": True}, gallery(), MODEL)[0]


def test_test_partition_never_changes_calibrated_policy_and_false_accepts_fail():
    manifest, recordings = report_fixture()
    original = build_report(manifest, recordings, gallery(), MODEL)
    recordings[-1]["response"]["voices"][-1]["embedding"] = [1.0, 0.0]
    report = build_report(manifest, recordings, gallery(), MODEL)
    assert (report["threshold"], report["margin"]) == (original["threshold"], original["margin"])
    assert not report["passed"]
    assert report["metrics"]["unknown_false_accepts"] == 1
    assert not validate_report(report, gallery(), MODEL)[0]


def test_quality_ineligible_known_cases_remain_in_coverage_denominator():
    manifest, recordings = report_fixture()
    for recording in recordings[2:]:
        for voice in recording["response"]["voices"][:4]:
            voice["clean_seconds"] = 9
    report = build_report(manifest, recordings, gallery(), MODEL)
    assert report["metrics"]["known_cases"] == 30
    assert report["metrics"]["known_coverage"] == pytest.approx(1 / 3)
    assert not report["passed"]


@pytest.mark.parametrize("alter", ["missing_label", "extra_label", "wrong_model",
                                  "bad_vector", "duplicate_voice", "repeated_audio",
                                  "repeated_conversation", "gallery_conversation"])
def test_incomplete_mismatched_and_leaked_runs_are_rejected(alter):
    manifest, recordings = report_fixture()
    if alter == "missing_label":
        del manifest[0]["speakers"]["unknown-0"]
    elif alter == "extra_label":
        manifest[0]["speakers"]["nonexistent"] = None
    elif alter == "wrong_model":
        recordings[0]["response"]["embedding_model"] = {**MODEL, "revision": "wrong"}
    elif alter == "bad_vector":
        recordings[0]["response"]["voices"][0]["embedding"] = [1, 0, 0]
    elif alter == "duplicate_voice":
        recordings[0]["response"]["voices"][1] = recordings[0]["response"]["voices"][0]
    elif alter == "repeated_audio":
        manifest[-1]["pcm_sha256"] = manifest[0]["pcm_sha256"]
    elif alter == "repeated_conversation":
        manifest[-1]["conversation_id"] = manifest[0]["conversation_id"]
    else:
        manifest[0]["conversation_id"] = "source:" + gallery()[0]["conversation_id"]
    with pytest.raises(ValueError):
        build_report(manifest, recordings, gallery(), MODEL)


def test_single_conversation_identity_blocks_ambiguous_established_identity():
    examples = gallery()[:2] + [{
        "id": "lars-1", "profile_id": "lars", "conversation_id": "lars-conversation",
        "embedding": [0.99, 0.01], "model": MODEL,
    }]
    assert rank_voice([1, 0], examples)["margin"] < 0.001
    assert match_voice([1, 0], examples, 0.9, 0.05) is None
    assert match_voice([0.99, 0.01], examples, 0.9, 0) is None


def test_unknown_only_conversations_cannot_pad_minimum_test_conversations():
    manifest, recordings = report_fixture()
    for index in range(3, len(recordings)):
        for label in manifest[index]["speakers"]:
            manifest[index]["speakers"][label] = None
        for voice in recordings[index]["response"]["voices"]:
            voice["embedding"] = [-1.0, 0.0]
    report = build_report(manifest, recordings, gallery(), MODEL)
    assert report["metrics"]["test_conversations"] == 1
    assert report["metrics"]["eligible_test_conversations"] == 5
    assert not report["passed"]


@pytest.mark.parametrize("field", ["source_ids", "source_digests"])
def test_aliased_source_meeting_and_transitive_audio_support_cannot_count_twice(field):
    examples = gallery()[:2]
    token = canonical_hash("audio") if field == "source_digests" else "same-source"
    examples[0][field] = [token]
    examples[1][field] = [token]
    assert rank_voice([1, 0], examples) is None
    extra_token = canonical_hash("extra") if field == "source_digests" else "extra-source"
    examples.append({**examples[0], "id": "third", "conversation_id": "third",
                     field: [extra_token]})
    examples[1][field].append(extra_token)
    assert rank_voice([1, 0], examples) is None
    examples.append({**examples[0], "id": "fourth", "conversation_id": "fourth",
                     "source_ids": ["independent-source"],
                     "source_digests": [canonical_hash("independent-audio")]})
    assert match_voice([1, 0], examples, 0.9, 0.1)["profile_id"] == "mads"


def test_report_rejects_bool_threshold_even_when_it_equals_numeric_threshold():
    manifest, recordings = report_fixture()
    report = build_report(manifest, recordings, gallery(), MODEL)
    assert report["threshold"] == 1.0
    report["threshold"] = True
    report["report_sha256"] = canonical_hash({k: v for k, v in report.items()
                                              if k != "report_sha256"})
    assert not validate_report(report, gallery(), MODEL)[0]


def test_independent_counts_share_matcher_provenance_and_ignore_unconfirmed_examples():
    examples = gallery()
    assert independent_conversation_counts(examples) == {"mads": 2, "alice": 2}
    examples[0]["source_ids"] = ["source-a"]
    examples[1]["source_ids"] = ["source-a", "source-b"]
    examples.append({**examples[0], "id": "third", "conversation_id": "third",
                     "source_ids": ["source-b"]})
    examples.append({**examples[0], "id": "unconfirmed", "conversation_id": "unconfirmed",
                     "source_ids": ["independent"], "confirmed": False})
    assert independent_conversation_counts(examples) == {"mads": 1, "alice": 2}
    assert rank_voice([1, 0], examples) is None
