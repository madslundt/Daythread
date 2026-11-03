"""Exact ordered transcript blocks for summary speaker evidence."""

import json
from uuid import uuid4

import httpx
import pytest

from omi_local.inference import Inference
from omi_local.settings import Settings
from omi_local.summary_speaker_evidence import source_prompt_pieces

MARKER = (
    "Ordered speech blocks (join each block's text in order to reconstruct this exact transcript "
    "window; speaker identifies an owner; unknown and overlap have no owner): "
)


def source_with_evidence(text, rows, **extra):
    source_id = str(uuid4())
    return {
        "id": source_id,
        "recording_id": source_id,
        "reference_type": "recording_transcription",
        "started_at": "2026-11-02T13:35:00Z",
        "source_kind": "recording_transcription",
        "text": text,
        "speaker_evidence": [
            {"recording_id": source_id, "source_id": source_id, **row} for row in rows
        ],
        **extra,
    }


def speech_payload(piece):
    _, marker, payload = piece.content.partition(MARKER)
    assert marker
    return json.loads(payload.partition("\nDiary recaps: ")[0])


def test_speech_blocks_partition_exact_text_and_keep_valid_owner_identity():
    canonical = "same same ??? same🙂"
    speaker_a, speaker_b = str(uuid4()), str(uuid4())
    rows = [
        {
            "text": "same",
            "start_offset": 0,
            "end_offset": 4,
            "attribution": "attributed",
            "speaker_id": speaker_a,
            "speaker_label": "A",
            "speaker_name": "Reviewed Alice",
            "start": 0.1,
            "end": 0.2,
        },
        {"text": " ", "start_offset": 4, "end_offset": 5, "attribution": "unknown"},
        {
            "text": "same",
            "start_offset": 5,
            "end_offset": 9,
            "attribution": "attributed",
            "speaker_id": speaker_a,
            "speaker_label": "A",
            "speaker_name": "Reviewed Alice",
            "start": 0.3,
            "end": 0.4,
        },
        {"text": " ", "start_offset": 9, "end_offset": 10, "attribution": "unknown"},
        {"text": "???", "start_offset": 10, "end_offset": 13, "attribution": "unknown"},
        {"text": " ", "start_offset": 13, "end_offset": 14, "attribution": "unknown"},
        {
            "text": "same",
            "start_offset": 14,
            "end_offset": 18,
            "attribution": "attributed",
            "speaker_id": speaker_b,
            "speaker_label": "A",
            "speaker_name": "Reviewed Bob",
            "start": 0.7,
            "end": 0.8,
        },
    ]
    source = source_with_evidence(canonical, rows)
    original_rows = json.loads(json.dumps(source["speaker_evidence"]))

    pieces = source_prompt_pieces(source, Inference._speaker_evidence_window, max_chars=12_000)

    payload = speech_payload(pieces[0])
    speech = payload["speech"]
    assert "".join(item["text"] for item in speech) == canonical
    assert [(item["text"], item.get("speaker"), item.get("attribution")) for item in speech] == [
        ("same same", "speaker_1", None),
        (" ??? ", None, "unknown"),
        ("same", "speaker_2", None),
        ("🙂", None, "unknown"),
    ]
    assert payload["speakers"] == {
        "speaker_1": {"id": speaker_a, "label": "A", "name": "Reviewed Alice"},
        "speaker_2": {"id": speaker_b, "label": "A", "name": "Reviewed Bob"},
    }
    assert pieces[0].content.count(speaker_a) == 1
    assert pieces[0].content.count(speaker_b) == 1
    assert "start_offset" not in pieces[0].content and '"start"' not in pieces[0].content
    assert source["speaker_evidence"] == original_rows


def test_unanchored_stale_and_wrong_source_text_stays_unknown():
    canonical = "repeat repeat"
    source = source_with_evidence(
        canonical,
        [
            {
                "text": "repeat",
                "start_offset": 0,
                "end_offset": 6,
                "attribution": "attributed",
                "speaker_id": str(uuid4()),
                "speaker_label": "A",
            },
            {"text": " ", "start_offset": 6, "end_offset": 7, "attribution": "unknown"},
            # Repeated text does not make a bad anchor valid.
            {
                "text": "repeet",
                "start_offset": 7,
                "end_offset": 13,
                "attribution": "attributed",
                "speaker_id": str(uuid4()),
                "speaker_label": "B",
            },
            # Correct text at the wrong owner is rejected by the shared speaker window.
            {
                "text": "repeat",
                "start_offset": 7,
                "end_offset": 13,
                "attribution": "attributed",
                "speaker_id": str(uuid4()),
                "speaker_label": "C",
                "source_id": str(uuid4()),
                "recording_id": str(uuid4()),
            },
        ],
    )
    pieces = source_prompt_pieces(source, Inference._speaker_evidence_window, max_chars=12_000)
    payload = speech_payload(pieces[0])
    assert "".join(item["text"] for item in payload["speech"]) == canonical
    assert payload["speech"] == [
        {"text": "repeat", "speaker": "speaker_1"},
        {"text": " repeat", "attribution": "unknown"},
    ]
    assert payload["speakers"] == {
        "speaker_1": {"id": source["speaker_evidence"][0]["speaker_id"], "label": "A"},
    }


def test_conflicting_overlap_anchors_fail_closed_and_unknown_words_break_owner_runs():
    conflict_text = "same"
    conflict_source = source_with_evidence(
        conflict_text,
        [
            {
                "text": conflict_text,
                "start_offset": 0,
                "end_offset": 4,
                "attribution": "attributed",
                "speaker_id": str(uuid4()),
                "speaker_label": "A",
            },
            {
                "text": conflict_text,
                "start_offset": 0,
                "end_offset": 4,
                "attribution": "attributed",
                "speaker_id": str(uuid4()),
                "speaker_label": "B",
            },
        ],
    )
    conflict = speech_payload(
        source_prompt_pieces(
            conflict_source,
            Inference._speaker_evidence_window,
            max_chars=12_000,
        )[0]
    )
    assert conflict["speech"] == [{"text": "same", "attribution": "unknown"}]
    assert conflict["speakers"] == {}

    canonical = "left mystery right"
    speaker = str(uuid4())
    rows = [
        {
            "text": "left",
            "start_offset": 0,
            "end_offset": 4,
            "attribution": "attributed",
            "speaker_id": speaker,
            "speaker_label": "A",
        },
        {"text": " ", "start_offset": 4, "end_offset": 5, "attribution": "unknown"},
        {"text": "mystery", "start_offset": 5, "end_offset": 12, "attribution": "unknown"},
        {"text": " ", "start_offset": 12, "end_offset": 13, "attribution": "unknown"},
        {
            "text": "right",
            "start_offset": 13,
            "end_offset": 18,
            "attribution": "attributed",
            "speaker_id": speaker,
            "speaker_label": "A",
        },
    ]
    payload = speech_payload(
        source_prompt_pieces(
            source_with_evidence(canonical, rows),
            Inference._speaker_evidence_window,
            max_chars=12_000,
        )[0]
    )
    assert [
        (block["text"], block.get("speaker"), block.get("attribution"))
        for block in payload["speech"]
    ] == [
        ("left", "speaker_1", None),
        (" mystery ", None, "unknown"),
        ("right", "speaker_1", None),
    ]

    same_owner = str(uuid4())
    conflicting_names = source_with_evidence(
        "left right",
        [
            {
                "text": "left",
                "start_offset": 0,
                "end_offset": 4,
                "attribution": "attributed",
                "speaker_id": same_owner,
                "speaker_label": "A",
                "speaker_name": "Reviewed Alice",
            },
            {"text": " ", "start_offset": 4, "end_offset": 5, "attribution": "unknown"},
            {
                "text": "right",
                "start_offset": 5,
                "end_offset": 10,
                "attribution": "attributed",
                "speaker_id": same_owner,
                "speaker_label": "B",
                "speaker_name": "Different reviewed name",
            },
        ],
    )
    conflict_payload = speech_payload(
        source_prompt_pieces(
            conflicting_names,
            Inference._speaker_evidence_window,
            max_chars=12_000,
        )[0]
    )
    assert conflict_payload["speech"] == [{"text": "left right", "speaker": "speaker_1"}]
    assert conflict_payload["speakers"] == {"speaker_1": {"id": same_owner}}


def test_overlap_blocks_bridge_only_safe_gaps_and_matching_candidate_context():
    canonical = "left, right unknown final"
    rows = [
        {
            "text": "left",
            "start_offset": 0,
            "end_offset": 4,
            "attribution": "overlap",
            "candidate_source_ids": ["part-a"],
        },
        {"text": ", ", "start_offset": 4, "end_offset": 6, "attribution": "unknown"},
        {
            "text": "right",
            "start_offset": 6,
            "end_offset": 11,
            "attribution": "overlap",
            "candidate_source_ids": ["part-a"],
        },
        {"text": " ", "start_offset": 11, "end_offset": 12, "attribution": "unknown"},
        {"text": "unknown", "start_offset": 12, "end_offset": 19, "attribution": "unknown"},
        {"text": " ", "start_offset": 19, "end_offset": 20, "attribution": "unknown"},
        {
            "text": "final",
            "start_offset": 20,
            "end_offset": 25,
            "attribution": "overlap",
            "candidate_source_ids": ["part-b"],
        },
    ]
    payload = speech_payload(
        source_prompt_pieces(
            source_with_evidence(canonical, rows),
            Inference._speaker_evidence_window,
            max_chars=12_000,
        )[0]
    )
    assert payload["speech"] == [
        {"text": "left, right", "attribution": "overlap"},
        {"text": " unknown ", "attribution": "unknown"},
        {"text": "final", "attribution": "overlap"},
    ]
    assert "".join(item["text"] for item in payload["speech"]) == canonical

    changed_candidates = source_with_evidence(
        "left, right",
        [
            {
                "text": "left",
                "start_offset": 0,
                "end_offset": 4,
                "attribution": "overlap",
                "candidate_source_ids": ["part-a"],
            },
            {"text": ", ", "start_offset": 4, "end_offset": 6, "attribution": "unknown"},
            {
                "text": "right",
                "start_offset": 6,
                "end_offset": 11,
                "attribution": "overlap",
                "candidate_source_ids": ["part-b"],
            },
        ],
    )
    changed = speech_payload(
        source_prompt_pieces(
            changed_candidates,
            Inference._speaker_evidence_window,
            max_chars=12_000,
        )[0]
    )
    assert changed["speech"] == [
        {"text": "left", "attribution": "overlap"},
        {"text": ", ", "attribution": "unknown"},
        {"text": "right", "attribution": "overlap"},
    ]


def test_safe_separator_pass_consumes_full_owner_run_but_stops_at_unknown_word():
    canonical = "one, two; three— four mystery five, six"
    speaker = str(uuid4())
    pieces = [
        ("one", "attributed", speaker),
        (", ", "unknown", None),
        ("two", "attributed", speaker),
        ("; ", "unknown", None),
        ("three", "attributed", speaker),
        ("— ", "unknown", None),
        ("four", "attributed", speaker),
        (" ", "unknown", None),
        ("mystery", "unknown", None),
        (" ", "unknown", None),
        ("five", "attributed", speaker),
        (", ", "unknown", None),
        ("six", "attributed", speaker),
    ]
    rows, cursor = [], 0
    for text, attribution, speaker_id in pieces:
        row = {
            "text": text,
            "start_offset": cursor,
            "end_offset": cursor + len(text),
            "attribution": attribution,
        }
        if speaker_id:
            row.update(speaker_id=speaker_id, speaker_label="A")
        rows.append(row)
        cursor += len(text)
    assert cursor == len(canonical)
    payload = speech_payload(
        source_prompt_pieces(
            source_with_evidence(canonical, rows),
            Inference._speaker_evidence_window,
            max_chars=12_000,
        )[0]
    )
    assert payload["speech"] == [
        {"text": "one, two; three— four", "speaker": "speaker_1"},
        {"text": " mystery ", "attribution": "unknown"},
        {"text": "five, six", "speaker": "speaker_1"},
    ]
    assert "".join(block["text"] for block in payload["speech"]) == canonical


@pytest.mark.anyio
async def test_ordered_blocks_fit_full_context_and_legacy_sources_keep_raw_format(tmp_path):
    words = [f"café{index:04d}" for index in range(600)]
    canonical = "🙂— " + " ".join(words)
    speaker_a, speaker_b = str(uuid4()), str(uuid4())
    rows = []
    cursor = len("🙂— ")
    for index, word in enumerate(words):
        end = cursor + len(word)
        speaker_id, label, name = (
            (speaker_a, "A", "Reviewed Alice")
            if index % 2 == 0
            else (speaker_b, "B", "Reviewed Bob")
        )
        rows.append(
            {
                "text": word,
                "start_offset": cursor,
                "end_offset": end,
                "attribution": "attributed",
                "speaker_id": speaker_id,
                "speaker_label": label,
                "speaker_name": name,
                "start": index / 10,
                "end": index / 10 + 0.05,
            }
        )
        if end < len(canonical):
            rows.append(
                {"text": " ", "start_offset": end, "end_offset": end + 1, "attribution": "unknown"}
            )
            cursor = end + 1
        else:
            cursor = end
    source = source_with_evidence(
        canonical,
        rows,
        diary_summaries=[{"id": str(uuid4()), "text": "Earlier note"}],
        subject_hint="Private metadata label",
    )
    pieces = source_prompt_pieces(source, Inference._speaker_evidence_window, max_chars=12_000)
    assert len(pieces) > 1
    assert all(len(piece.content) <= 12_000 for piece in pieces)
    assert pieces[0].start_offset == 0 and pieces[-1].end_offset == len(canonical)
    assert all(
        canonical[piece.end_offset - 1].isspace() or canonical[piece.end_offset].isspace()
        for piece in pieces[:-1]
    )
    assert "Private metadata label" in pieces[0].content
    assert "Diary recaps:" in pieces[0].content

    reconstructed = []
    expected_owners = {
        word: "speaker_1" if index % 2 == 0 else "speaker_2" for index, word in enumerate(words)
    }
    observed_owners = {}
    for piece in pieces:
        payload = speech_payload(piece)
        block_text = "".join(block["text"] for block in payload["speech"])
        assert block_text == canonical[piece.start_offset : piece.end_offset]
        reconstructed.append(block_text)
        cursor = piece.start_offset
        for block in payload["speech"]:
            end = cursor + len(block["text"])
            if block.get("speaker"):
                for word in words:
                    local = block["text"].find(word)
                    if local >= 0:
                        observed_owners[word] = block["speaker"]
            cursor = end
    assert "".join(reconstructed) == canonical
    assert observed_owners == expected_owners
    assert "word_times" not in "".join(piece.content for piece in pieces)
    assert "start_offset" not in "".join(piece.content for piece in pieces)

    legacy = {
        "id": str(uuid4()),
        "started_at": "2026-11-02T13:35:00Z",
        "text": "A plain legacy transcript.",
    }
    legacy_piece = source_prompt_pieces(
        legacy,
        Inference._speaker_evidence_window,
        max_chars=12_000,
    )[0]
    assert "A plain legacy transcript." in legacy_piece.content
    assert MARKER not in legacy_piece.content


def test_recording_excerpt_uses_absolute_canonical_anchors():
    canonical = "Before. Exact repeated word. After."
    excerpt = "Exact repeated word."
    origin = canonical.index(excerpt)
    source = source_with_evidence(
        excerpt,
        [
            {
                "text": excerpt,
                "start_offset": origin,
                "end_offset": origin + len(excerpt),
                "attribution": "attributed",
                "speaker_id": str(uuid4()),
                "speaker_label": "A",
            }
        ],
        canonical_text=canonical,
        speaker_evidence_origin=origin,
    )
    pieces = source_prompt_pieces(source, Inference._speaker_evidence_window, max_chars=12_000)
    assert len(pieces) == 1
    payload = speech_payload(pieces[0])
    assert "".join(item["text"] for item in payload["speech"]) == excerpt
    assert payload["speech"] == [{"text": excerpt, "speaker": "speaker_1"}]


@pytest.mark.anyio
async def test_reduce_sends_one_canonical_text_representation(tmp_path):
    canonical = "A synthetic speech span with exact words."
    speaker = str(uuid4())
    source = source_with_evidence(
        canonical,
        [
            {
                "text": canonical,
                "start_offset": 0,
                "end_offset": len(canonical),
                "attribution": "attributed",
                "speaker_id": speaker,
                "speaker_label": "A",
                "speaker_name": "Reviewed Alice",
            }
        ],
    )
    source_id = source["id"]
    requests = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": f"A short recap [{source_id}]"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        inference = Inference(
            Settings(data_dir=tmp_path, summary_language="en", _env_file=None),
            http,
        )
        assert await inference.summarize([source], "a recap") == f"A short recap [{source_id}]"

    assert len(requests) == 1
    evidence = requests[0]["evidence"]
    payload = json.loads(evidence.partition(MARKER)[2])
    assert "".join(block["text"] for block in payload["speech"]) == canonical
    assert evidence.count(canonical) == 1
    assert source_id in evidence and speaker in evidence and "Reviewed Alice" in evidence
