import json

from omi_local.transcript_turns import transcript_turns


def row(text, segments):
    return {"state": "done", "text": text, "segments": json.dumps(segments),
            "started_at": "2026-11-01T10:00:00Z", "ended_at": "2026-11-01T10:00:10Z"}


def test_repeated_speaker_label_and_start_only_import_timestamps():
    transcript = "Rae: Hello.\nLee: Hi.\nRae: Thanks."
    turns = transcript_turns(row(transcript, [
        {"text": "Hello.", "speaker_name": "Rae", "start": 0, "end": 0},
        {"text": "Hi.", "speaker_name": "Lee", "start": 2, "end": 2},
        {"text": "Thanks.", "speaker_name": "Rae", "start": 4, "end": 4},
    ]))
    assert [(t["speaker_name"], t["text"], t["start"]) for t in turns] == [
        ("Rae", "Hello.", 0), ("Lee", "Hi.", 2), ("Rae", "Thanks.", 4),
    ]
    assert all("end" not in turn for turn in turns)


def test_speaker_grouped_segments_keep_times_and_original_text_offsets():
    text = "Rae: Hello.\nRae: Thanks.\nLee: Hi."
    turns = transcript_turns(row(text, [
        {"text": "Hello.", "speaker_name": "Rae", "start": 0, "end": 1},
        {"text": "Thanks.", "speaker_name": "Rae", "start": 4, "end": 6},
        {"text": "Hi.", "speaker_name": "Lee", "start": 2, "end": 3},
    ]))
    assert [(t["start"], t["end"]) for t in turns] == [(0, 1), (4, 6), (2, 3)]
    assert "\n".join(text[t["start_offset"]:t["end_offset"]] for t in turns) == text


def test_unknown_speakers_keep_their_text_without_attribution_or_made_up_times():
    turns = transcript_turns(row("One. Two. One again.", [
        {"text": "One.", "speaker_id": 17, "start": 0},
        {"text": "Two.", "start": 15},
        {"text": "One again.", "speaker_id": 17, "start": 6},
    ]))
    assert [t.get("speaker_number") for t in turns] == [1, None, 1]
    assert [t.get("start") for t in turns] == [0, None, 6]
    corrected = row("Corrected words.", [{"text": "Old words.", "start": 0}])
    assert transcript_turns(corrected) == [
        {"text": "Corrected words.", "start_offset": 0, "end_offset": 16},
    ]


def test_exported_speaker_labels_do_not_appear_twice():
    text = "Speaker 1: Hello.\nSpeaker 2: Hi."
    turns = transcript_turns(row(text, [
        {"text": "Speaker 1: Hello.", "start": 0},
        {"text": "Speaker 2: Hi.", "start": 2},
    ]))
    assert [(turn["speaker_label"], turn["text"]) for turn in turns] == [
        ("Speaker 1", "Hello."), ("Speaker 2", "Hi."),
    ]


def test_import_generated_prefixes_keep_the_original_speaker_identities():
    text = "Speaker 17: Hello.\nSpeaker 2: Hi.\nSpeaker 17: Goodbye."
    turns = transcript_turns(row(text, [
        {"text": "Hello.", "speaker_id": 17, "start": 0},
        {"text": "Hi.", "speaker_id": 2, "start": 2},
        {"text": "Goodbye.", "speaker_id": 17, "start": 4},
    ]))
    assert [(turn["speaker_number"], turn["text"], turn["start"]) for turn in turns] == [
        (1, "Hello.", 0), (2, "Hi.", 2), (1, "Goodbye.", 4),
    ]


def test_only_project_verified_end_times_and_saved_identity():
    text = "One. Two. Three."
    source = row(text, [
        {"text": "One.", "speaker_id": 1, "start": 0, "end": 2},
        {"text": "Two.", "speaker_id": 2, "start": 2, "end": 12},
        {"text": "Three.", "speaker_id": 1, "start": 4, "end": 9},
    ])
    assert [turn.get("end") for turn in transcript_turns(source)] == [2, None, 9]
    saved = [{"text": text, "start_offset": 0, "end_offset": len(text), "start": 0,
              "end": 9, "speaker_id": "stable-id", "speaker_label": "Speaker A",
              "attribution": "attributed"}]
    assert transcript_turns(source, saved) == saved
