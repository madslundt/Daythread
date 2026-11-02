"""Anonymous diarization comparisons use one global speaker permutation."""

import pytest

from scripts.compare_diarization import compare


def test_comparison_maps_labels_once_and_counts_overlapping_activity():
    result = compare(
        {"segments": [
            {"start": 0, "end": 2, "speaker": 1},
            {"start": 1, "end": 2, "speaker": 2},
            {"start": 2, "end": 4, "speaker": 2},
        ]},
        {"turns": [
            {"start": 0, "end": 2, "speaker": "X"},
            {"start": 1, "end": 2, "speaker": "Y"},
            {"start": 2, "end": 4, "speaker": "Y"},
        ]},
        duration=4,
    )
    assert result["global_mapped_activity_f1"] == pytest.approx(1.0)
    assert result["global_mapped_activity_precision"] == pytest.approx(1.0)
    assert result["global_mapped_activity_recall"] == pytest.approx(1.0)
    assert result["boundary_agreement"]["boundary_within_250ms"]["recall"] == pytest.approx(1.0)


def test_comparison_does_not_relabel_per_turn_to_hide_identity_switches():
    result = compare(
        {"segments": [
            {"start": 0, "end": 1, "speaker": "A"},
            {"start": 1, "end": 2, "speaker": "B"},
            {"start": 2, "end": 3, "speaker": "A"},
        ]},
        {"turns": [
            {"start": 0, "end": 1, "speaker": "X"},
            {"start": 1, "end": 2, "speaker": "X"},
            {"start": 2, "end": 3, "speaker": "Y"},
        ]},
        duration=3,
    )
    assert result["global_mapped_activity_f1"] == pytest.approx(2 / 3)
    assert result["longest_disagreements"]


def test_plaud_millisecond_offsets_are_normalized_and_not_der_labeled():
    result = compare(
        {"segments": [{"start": 0, "end": 2, "speaker": 1}]},
        {"plaud_transcript": [{"start_time": 0, "end_time": 2000, "speaker": "x"}]},
        duration=2,
    )
    assert result["global_mapped_activity_f1"] == pytest.approx(1.0)
    assert "not human-reference DER" in result["comparison_kind"]


@pytest.mark.parametrize("segment", [
    {"start": True, "end": 2, "speaker": 1},
    {"start": 0, "end": float("inf"), "speaker": 1},
    {"start": 0, "end": 3, "speaker": 1},
])
def test_comparison_rejects_invalid_timing(segment):
    with pytest.raises(ValueError):
        compare({"segments": [segment]}, {"turns": []}, duration=2)
