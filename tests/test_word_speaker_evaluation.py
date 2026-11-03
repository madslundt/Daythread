"""Word speaker joins preserve transcript and reference token identities."""

import json
import re

import pytest

from scripts.compare_word_speakers import evaluate_word_speakers, main

TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def anchors(text, *, omit=()):
    return [
        {"start": index, "end": index + 0.8, "start_offset": match.start(),
         "end_offset": match.end(), "text": match.group(), "kind": "word"}
        for index, match in enumerate(TOKEN.finditer(text)) if index not in omit
    ]


def utterance(start, end, speaker, text):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def test_long_reference_utterance_does_not_index_labels_by_utterance_id():
    text = "one two three four five six seven eight"
    result = evaluate_word_speakers(
        text, anchors(text),
        [{"start": 0, "end": 7, "speaker": "A"},
         {"start": 7, "end": 8, "speaker": "B"}],
        [utterance(0, 7, "R1", "one two three four five six seven"),
         utterance(7, 8, "R2", "eight")],
        duration_seconds=8, reference_time_unit="seconds",
    )
    assert result["global_label_mapping"] == {"A": "R1", "B": "R2"}
    assert result["matched_reference_token_count"] == 8
    assert result["compared_word_count"] == 8
    assert result["label_agreement_count"] == 8


def test_repeated_phrase_matches_keep_both_occurrence_indices_and_labels():
    phrase = "red blue green tree flower"
    text = f"{phrase} {phrase}"
    result = evaluate_word_speakers(
        text, anchors(text),
        [{"start": 0, "end": 5, "speaker": "A"},
         {"start": 5, "end": 10, "speaker": "B"}],
        [utterance(0, 5, "R1", phrase), utterance(5, 10, "R2", phrase)],
        duration_seconds=10, reference_time_unit="seconds",
    )
    assert result["matched_reference_token_count"] == 10
    assert result["compared_word_count"] == 10
    assert result["label_agreement_count"] == 10


def test_matching_block_crossing_utterance_boundary_uses_each_token_owner():
    text = "alpha beta gamma delta epsilon zeta"
    result = evaluate_word_speakers(
        text, anchors(text),
        [{"start": 0, "end": 3, "speaker": "A"},
         {"start": 3, "end": 6, "speaker": "B"}],
        [utterance(0, 3, "R1", "alpha beta gamma"),
         utterance(3, 6, "R2", "delta epsilon zeta")],
        duration_seconds=6, reference_time_unit="seconds",
    )
    assert result["matched_reference_token_count"] == 6
    assert result["compared_word_count"] == 6
    assert result["label_agreement_count"] == 6


def test_untimed_canonical_word_stays_in_match_sequence_and_unknown_count():
    text = "alpha beta gamma delta epsilon zeta"
    result = evaluate_word_speakers(
        text, anchors(text, omit={1}),
        [{"start": 0, "end": 6, "speaker": "A"}],
        [utterance(0, 6, "R1", text)],
        duration_seconds=6, reference_time_unit="seconds",
    )
    assert result["canonical_word_count"] == 6
    assert result["anchored_word_count"] == 5
    assert result["matched_reference_token_count"] == 6
    assert result["unknown_word_counts"]["untimed"] == 1
    assert result["compared_word_count"] == 5


def test_one_exact_fragment_spanning_multiple_lexical_tokens_is_untimed():
    text = "alpha beta-gamma delta epsilon zeta"
    matches = list(TOKEN.finditer(text))
    word_anchors = [
        {"start": index * 0.5, "end": index * 0.5 + 0.4,
         "start_offset": token.start(),
         "end_offset": token.end(), "text": token.group(), "kind": "word"}
        for index, token in enumerate(matches) if index not in {1, 2}
    ]
    word_anchors.insert(1, {
        "start": 0.5, "end": 1.4, "start_offset": matches[1].start(),
        "end_offset": matches[2].end(), "text": "beta-gamma", "kind": "word",
    })
    result = evaluate_word_speakers(
        text, word_anchors, [{"start": 0, "end": 6, "speaker": "A"}],
        [utterance(0, 6, "R1", text)],
        duration_seconds=6, reference_time_unit="seconds",
    )
    assert result["canonical_word_count"] == 6
    assert result["unsupported_multiword_anchor_count"] == 1
    assert result["unsupported_multiword_word_count"] == 2
    assert result["untimed_word_count"] == 2
    assert result["matched_reference_token_count"] == 6
    assert result["compared_word_count"] == 4


def test_overlapping_turns_make_word_unknown_instead_of_choosing_one():
    text = "alpha beta gamma delta epsilon zeta"
    result = evaluate_word_speakers(
        text, anchors(text),
        [{"start": 0, "end": 6, "speaker": "A"},
         {"start": 2, "end": 3, "speaker": "B"}],
        [utterance(0, 6, "R1", text)],
        duration_seconds=6, reference_time_unit="seconds",
    )
    assert result["unknown_word_counts"]["turn_or_overlap"] == 1
    assert result["compared_word_count"] == 5


def test_same_speaker_parts_touching_within_one_sample_merge_for_cross_edge_word():
    text = "alpha beta gamma delta epsilon zeta"
    result = evaluate_word_speakers(
        text, anchors(text),
        [{"start": 0, "end": 2.4, "speaker": "A"},
         {"start": 2.4 + 1 / 16000, "end": 6, "speaker": "A"}],
        [utterance(0, 6, "R1", text)],
        duration_seconds=6, reference_time_unit="seconds",
    )
    assert result["unknown_word_counts"]["turn_or_overlap"] == 0
    assert result["compared_word_count"] == 6


def test_overlapping_reference_speakers_make_matched_word_reference_ambiguous():
    text = "alpha beta gamma delta epsilon zeta"
    result = evaluate_word_speakers(
        text, anchors(text),
        [{"start": 0, "end": 6, "speaker": "A"}],
        [utterance(0, 6, "R1", text), utterance(2, 3, "R2", "gamma")],
        duration_seconds=6, reference_time_unit="seconds",
    )
    assert result["unknown_word_counts"]["reference_ambiguous"] == 1
    assert result["compared_word_count"] == 5


def test_reference_time_unit_is_explicit_and_milliseconds_convert_exactly():
    text = "alpha beta gamma delta epsilon zeta"
    turns = [{"start": 0, "end": 6, "speaker": "A"}]
    refs = [utterance(0, 6000, "R1", text)]
    result = evaluate_word_speakers(
        text, anchors(text), turns, refs,
        duration_seconds=6, reference_time_unit="milliseconds",
    )
    assert result["compared_word_count"] == 6
    with pytest.raises(ValueError):
        evaluate_word_speakers(
            text, anchors(text), turns, refs,
            duration_seconds=6, reference_time_unit="seconds",
        )


def test_cli_requires_explicit_units_and_emits_no_transcript_text(tmp_path, monkeypatch):
    text = "alpha beta gamma delta epsilon zeta"
    payload = {
        "canonical_text": text,
        "word_anchors": anchors(text),
        "turns": [{"start": 0, "end": 6, "speaker": "A"}],
        "reference_utterances": [utterance(0, 6, "R1", text)],
        "duration_seconds": 6,
        "provenance": {"source_revision": "fixture-revision"},
    }
    input_path, output_path = tmp_path / "input.json", tmp_path / "out.json"
    input_path.write_text(json.dumps(payload))
    monkeypatch.setattr("sys.argv", ["compare-word-speakers", "--input", str(input_path),
                                      "--output", str(output_path),
                                      "--reference-time-unit", "seconds"])
    assert main() == 0
    rendered = output_path.read_text()
    assert text not in rendered
    output = json.loads(rendered)
    assert output["compared_word_count"] == 6
    assert output["provenance"] == payload["provenance"]


@pytest.mark.parametrize("bad_anchor", [
    {"start": 0.0, "end": 0.8, "start_offset": 0, "end_offset": 5,
     "text": "wrong", "kind": "word"},
    {"start": 0, "end": float("inf"), "start_offset": 0, "end_offset": 5,
     "text": "alpha", "kind": "word"},
])
def test_invalid_exact_word_anchor_is_refused(bad_anchor):
    with pytest.raises(ValueError):
        evaluate_word_speakers(
            "alpha beta gamma delta epsilon zeta", [bad_anchor], [], [],
            duration_seconds=6, reference_time_unit="seconds",
        )
