"""Compact speaker evidence without changing canonical transcript content."""

import httpx
import pytest

from omi_local.inference import Inference
from omi_local.settings import Settings
from omi_local.summary_filter import NO_CONTENT
from omi_local.summary_speaker_evidence import (
    compact_speaker_evidence,
    source_prompt_pieces,
)


def test_prompt_evidence_whitelists_fields_and_preserves_repeated_text_and_uncertainty():
    canonical = "same same ??? same"
    spans = compact_speaker_evidence([
        {
            "text": "same", "start_offset": 0, "end_offset": 4,
            "start": 0.1, "end": 0.2, "attribution": "attributed",
            "speaker_id": "00000000-0000-4000-8000-000000000011", "speaker_label": "A",
            "speaker_name": "Reviewed Alice", "source_id": "private-source",
            "recording_id": "private-recording", "child_source_id": "private-child",
            "candidate_source_ids": ["private-candidate"],
        },
        {
            "text": " ", "start_offset": 4, "end_offset": 5,
            "attribution": "unknown",
        },
        {
            "text": "same", "start_offset": 5, "end_offset": 9,
            "start": 0.3, "end": 0.4, "attribution": "overlap",
            "speaker_label": "B", "speaker_name": "Do not identify overlap",
        },
        {
            "text": " ", "start_offset": 9, "end_offset": 10,
            "attribution": "unknown",
        },
        {
            "text": "???", "start_offset": 10, "end_offset": 13,
            "start": float("nan"), "end": 0.6, "attribution": "unknown",
            "speaker_label": "C", "speaker_name": "Do not identify unknown speech",
        },
        {
            "text": " ", "start_offset": 13, "end_offset": 14,
            "attribution": "unknown",
        },
        {
            "text": "same", "start_offset": 14, "end_offset": 18,
            "start": 0.7, "end": 0.8, "attribution": "attributed",
            "speaker_id": "00000000-0000-4000-8000-000000000012", "speaker_label": "B",
            "speaker_name": "Reviewed Bob", "kind": "boundary_word",
        },
    ])

    assert spans["speaker_names"] == {}
    assert spans["speakers"] == {
        "speaker_1": {
            "id": "00000000-0000-4000-8000-000000000011",
            "label": "A", "name": "Reviewed Alice",
        },
        "speaker_2": {
            "id": "00000000-0000-4000-8000-000000000012",
            "label": "B", "name": "Reviewed Bob",
        },
    }
    projected = spans["spans"]
    assert [item["text"] for item in projected] == ["same", "same", "???", "same"]
    assert [canonical[item["start_offset"]:item["end_offset"]] for item in projected] == [
        item["text"] for item in projected
    ]
    assert [(item["start_offset"], item["end_offset"]) for item in projected] == [
        (0, 4), (5, 9), (10, 13), (14, 18),
    ]
    assert projected[0]["start"] == 0.1 and projected[0]["owner"] == "speaker_1"
    assert projected[1]["attribution"] == "overlap"
    assert "owner" not in projected[1] and "speaker_label" not in projected[1]
    assert projected[2]["attribution"] == "unknown"
    assert "start" not in projected[2] and "end" not in projected[2]
    assert projected[3]["kind"] == "boundary_word"
    encoded = str(spans)
    for private_id in (
        "private-source", "private-recording", "private-child", "private-candidate",
    ):
        assert private_id not in encoded
    assert encoded.count("00000000-0000-4000-8000-000000000011") == 1

    source_id = "00000000-0000-4000-8000-000000000009"
    source = {
        "id": source_id,
        "recording_id": source_id,
        "reference_type": "recording_transcription",
        "canonical_text": "same same",
        "speaker_evidence": [{
            "recording_id": source_id,
            "source_id": source_id,
            "text": "same same",
            "start_offset": 0,
            "end_offset": 9,
            "start": 0.0,
            "end": 0.3,
            "speaker_id": "private-speaker-a",
            "speaker_label": "A",
            "speaker_name": "Reviewed Alice",
            "attribution": "attributed",
        }],
    }
    clipped = Inference._speaker_evidence_window(source, 5, 9)
    clipped_payload = compact_speaker_evidence(clipped)
    assert clipped_payload["spans"] == [{
        "text": "same", "start_offset": 5, "end_offset": 9,
        "attribution": "unknown",
    }]
    assert clipped_payload["speaker_names"] == {}


def test_prompt_owner_aliases_keep_duplicate_labels_distinct_and_drop_conflicting_label():
    payload = compact_speaker_evidence([
        {
            "text": "same", "start_offset": 0, "end_offset": 4,
            "attribution": "attributed", "speaker_id": "00000000-0000-4000-8000-000000000021",
            "speaker_label": "A", "speaker_name": "Reviewed Alice",
        },
        {
            "text": "same", "start_offset": 5, "end_offset": 9,
            "attribution": "attributed", "speaker_id": "00000000-0000-4000-8000-000000000022",
            "speaker_label": "A", "speaker_name": "Reviewed Bob",
        },
        {
            "text": "same", "start_offset": 10, "end_offset": 14,
            "attribution": "attributed", "speaker_id": "00000000-0000-4000-8000-000000000021",
            "speaker_label": "B", "speaker_name": "Reviewed Alice",
        },
    ])
    assert [span["owner"] for span in payload["spans"]] == [
        "speaker_1", "speaker_2", "speaker_1",
    ]
    assert payload["speakers"] == {
        "speaker_1": {
            "id": "00000000-0000-4000-8000-000000000021",
            "name": "Reviewed Alice",
        },
        "speaker_2": {
            "id": "00000000-0000-4000-8000-000000000022",
            "label": "A", "name": "Reviewed Bob",
        },
    }
    assert payload["spans"][0]["owner"] != payload["spans"][1]["owner"]


def test_overlap_prompt_runs_bridge_only_punctuation_with_same_candidate_context():
    canonical = "one, two mystery three four"
    payload = compact_speaker_evidence([
        {
            "text": "one", "start_offset": 0, "end_offset": 3,
            "start": 0.1, "end": 0.2, "attribution": "overlap",
            "candidate_source_ids": ["part-a"],
        },
        {
            "text": ", ", "start_offset": 3, "end_offset": 5,
            "attribution": "unknown",
        },
        {
            "text": "two", "start_offset": 5, "end_offset": 8,
            "start": 0.21, "end": 0.3, "attribution": "overlap",
            "candidate_source_ids": ["part-a"],
        },
        {
            "text": " ", "start_offset": 8, "end_offset": 9,
            "attribution": "unknown",
        },
        {
            "text": "mystery", "start_offset": 9, "end_offset": 16,
            "attribution": "unknown",
        },
        {
            "text": " ", "start_offset": 16, "end_offset": 17,
            "attribution": "unknown",
        },
        {
            "text": "three", "start_offset": 17, "end_offset": 22,
            "start": 0.4, "end": 0.5, "attribution": "overlap",
            "candidate_source_ids": ["part-a"],
        },
        {
            "text": " ", "start_offset": 22, "end_offset": 23,
            "attribution": "unknown",
        },
        {
            "text": "four", "start_offset": 23, "end_offset": 27,
            "start": 0.51, "end": 0.6, "attribution": "overlap",
            "candidate_source_ids": ["part-b"],
        },
    ])

    spans = payload["spans"]
    assert [(item["text"], item["attribution"]) for item in spans] == [
        ("one, two", "overlap"),
        ("mystery", "unknown"),
        ("three", "overlap"),
        ("four", "overlap"),
    ]
    assert spans[0]["word_times"] == [[0, 3, 0.1, 0.2], [5, 8, 0.21, 0.3]]
    assert "word_times" not in spans[-1]
    assert all(
        canonical[item["start_offset"]:item["end_offset"]] == item["text"]
        for item in spans
    )
    assert "".join(canonical[item["start_offset"]:item["end_offset"]] for item in spans) == (
        "one, twomysterythreefour"
    )


@pytest.mark.anyio
async def test_prompt_piece_windows_preserve_canonical_text_and_fit_including_evidence(tmp_path):
    words = [f"turn{index:04d}" for index in range(600)]
    canonical = " ".join(words)
    source_id = "00000000-0000-4000-8000-000000000002"
    evidence = []
    cursor = 0
    for index, word in enumerate(words):
        end = cursor + len(word)
        evidence.append({
            "text": word, "start_offset": cursor, "end_offset": end,
            "start": index / 10, "end": index / 10 + 0.05,
            "attribution": "attributed", "speaker_label": "A",
            "speaker_name": "A reviewed name",
        })
        if end < len(canonical):
            evidence.append({
                "text": " ", "start_offset": end, "end_offset": end + 1,
                "attribution": "unknown",
            })
            cursor = end + 1
        else:
            cursor = end
    source = {
        "id": source_id,
        "started_at": "2026-11-02T13:35:00Z",
        "text": canonical,
        "speaker_evidence": evidence,
    }

    async with httpx.AsyncClient() as client:
        model = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        pieces = source_prompt_pieces(
            source, model._speaker_evidence_window, max_chars=12_000,
        )
    assert all(len(piece.content) <= 12_000 for piece in pieces)
    assert [(piece.start_offset, piece.end_offset) for piece in pieces][0][0] == 0
    assert pieces[-1].end_offset == len(canonical)
    assert "".join(canonical[p.start_offset:p.end_offset] for p in pieces) == canonical
    assert all(
        canonical[piece.end_offset - 1].isspace()
        or canonical[piece.end_offset].isspace()
        for piece in pieces[:-1]
    )


@pytest.mark.anyio
async def test_summary_source_batches_bound_large_speaker_evidence_without_losing_text(tmp_path):
    words = [f"spokenword{index:04d}" for index in range(600)]
    canonical = " ".join(words)
    evidence = []
    cursor = 0
    source_id = "00000000-0000-4000-8000-000000000001"
    name_a, name_b = "Reviewed Alice " + "a" * 180, "Reviewed Bob " + "b" * 180
    for index, word in enumerate(words):
        end = cursor + len(word)
        evidence.append({
            "recording_id": source_id,
            "source_id": source_id,
            "child_source_id": f"child-{index % 10}",
            "candidate_source_ids": [f"child-{part}" for part in range(10)],
            "text": word,
            "start_offset": cursor,
            "end_offset": end,
            "start": index / 10,
            "end": index / 10 + 0.05,
            "speaker_id": f"speaker-{index % 2}",
            "speaker_label": "A" if index % 2 == 0 else "B",
                "speaker_name": name_a if index % 2 == 0 else name_b,
            "attribution": "attributed",
            "kind": "word",
        })
        if end < len(canonical):
            evidence.append({
                "recording_id": source_id,
                "source_id": source_id,
                "text": " ",
                "start_offset": end,
                "end_offset": end + 1,
                "start": None,
                "end": None,
                "speaker_id": None,
                "speaker_label": None,
                "speaker_name": None,
                "attribution": "unknown",
                "kind": "unknown",
            })
            cursor = end + 1
        else:
            cursor = end

    source = {
        "id": source_id,
        "recording_id": source_id,
        "reference_type": "recording_transcription",
        "started_at": "2026-11-02T13:35:00Z",
        "text": canonical,
        "speaker_evidence": evidence,
    }
    received = []

    async def capture_batch(batch, *_args, **_kwargs):
        received.append(batch)
        return NO_CONTENT

    async with httpx.AsyncClient() as client:
        inference = Inference(Settings(data_dir=tmp_path, _env_file=None), client)
        prompt_pieces = source_prompt_pieces(
            source, inference._speaker_evidence_window, max_chars=12_000,
        )
        inference._summary_notes = capture_batch
        assert await inference._reduce([source], "meeting notes", None) == NO_CONTENT

    assert received
    assert all(len(batch) <= 12_000 for batch in received)
    joined = "\n".join(received)
    for word in words:
        assert word in joined
    assert name_a in joined and name_b in joined
    assert all(piece.content.count(name_a) <= 1 for piece in prompt_pieces)
    assert all(piece.content.count(name_b) <= 1 for piece in prompt_pieces)
    assert "candidate_source_ids" not in joined
    assert "child_source_id" not in joined
    assert "speaker_id" not in joined
    assert '"text":" "' not in joined
