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


def test_unknown_speakers_keep_their_text_without_attribution_or_made_up_times():
    turns = transcript_turns(row("One. Two. One again.", [
        {"text": "One.", "speaker_id": 17, "start": 0},
        {"text": "Two.", "start": 15},
        {"text": "One again.", "speaker_id": 17, "start": 6},
    ]))
    assert [t.get("speaker_number") for t in turns] == [1, None, 1]
    assert [t.get("start") for t in turns] == [0, None, 6]
    assert not transcript_turns(row("Corrected words.", [{"text": "Old words.", "start": 0}]))


def test_exported_speaker_labels_do_not_appear_twice():
    text = "Speaker 1: Hello.\nSpeaker 2: Hi."
    turns = transcript_turns(row(text, [
        {"text": "Speaker 1: Hello.", "start": 0},
        {"text": "Speaker 2: Hi.", "start": 2},
    ]))
    assert [(turn["speaker_name"], turn["text"]) for turn in turns] == [
        ("Speaker 1", "Hello."), ("Speaker 2", "Hi."),
    ]
