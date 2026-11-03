
import pytest
from recording_transcription_helpers import make_ready_variant, pinned_settings

from omi_local.domain import EvidenceChanged, utc_now


def test_versioned_selection_is_explicit_and_invalidates_after_correction(app_recording):
    client, store, rid, ids, texts, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Full selected text.")
    before = [store.get_audio(source_id)["text"] for source_id in ids]
    selected = client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    )
    assert selected.status_code == 200, selected.text
    body = client.get(f"/v1/recordings/{rid}").json()
    assert body["text"] == "\n".join(texts)
    assert body["transcript_selection"]["state"] == "selected"
    assert body["transcript_selection"]["transcript"]["text"] == "Full selected text."
    with store.connect() as db:
        assert db.execute(
            "SELECT count(*) FROM transcript_search WHERE id IN (?,?)", ids,
        ).fetchone()[0] == 2
    store.correct_transcript(ids[0], "Human correction remains canonical.")
    stale = client.get(f"/v1/recordings/{rid}").json()
    assert stale["transcript_selection"]["state"] == "stale"
    assert stale["transcript_selection"]["transcript"] is None
    exact = client.get(f"/v1/recordings/{rid}/transcriptions/{version}")
    assert exact.status_code == 200
    assert exact.json()["id"] == rid
    assert exact.json()["reference_type"] == "recording_transcription"
    assert exact.json()["state"] == "stale"
    assert exact.json()["text"] == "Full selected text."
    assert exact.json()["audio_available"] is False
    assert [store.get_audio(source_id)["text"] for source_id in ids] != before


def test_exact_historical_transcript_withholds_current_audio_after_pipeline_change(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Historical selected text.")

    current = client.get(f"/v1/recordings/{rid}/transcriptions/{version}")
    assert current.status_code == 200
    assert current.json()["state"] == "ready"
    assert current.json()["audio_available"] is True

    memory.settings.recording_transcription_alignment_enabled = True
    stale = client.get(f"/v1/recordings/{rid}/transcriptions/{version}")
    assert stale.status_code == 200
    assert stale.json()["state"] == "stale"
    assert stale.json()["text"] == "Historical selected text."
    assert stale.json()["audio_available"] is False

def test_selection_uses_compare_and_set_and_retention_purges_typed_history(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Keep exact text.")
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    conflict = client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": None},
    )
    assert conflict.status_code == 409
    cleared = client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 1, "result_version": None},
    )
    assert cleared.status_code == 200
    assert client.get(f"/v1/recordings/{rid}").json()["transcript_selection"]["state"] == (
        "unselected"
    )
    store.delete_audio(ids[0])
    assert client.get(f"/v1/recordings/{rid}/transcriptions/{version}").status_code == 404

def test_recap_uses_one_selected_full_recording_source_with_exact_members(app_recording):
    client, store, rid, ids, texts, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    text = "Whole file says both parts once."
    _, version = make_ready_variant(store, memory, rid, ids, text)
    initial_group = next(group for group in memory.recaps.groups(utc_now())
                         if {row["id"] for row in group.sources} == set(ids))
    initial_key = memory.recaps.group_key(initial_group)
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    groups = memory.recaps.groups(utc_now())
    group = next(group for group in groups if {row["id"] for row in group.sources} == set(ids))
    assert memory.recaps.group_key(group) != initial_key
    sources = memory.recaps.sources(group)
    assert len(sources) == 1
    assert sources[0]["reference_type"] == "recording_transcription"
    assert sources[0]["id"] == rid
    assert sources[0]["source_ids"] == ids
    assert sources[0]["text"] == text
    prompt_sources = memory.recaps.note_sources(sources)
    assert len(prompt_sources) == 1
    assert prompt_sources[0]["text"] == text
    assert all(source_id not in {source["id"] for source in sources} for source_id in ids)
    assert "source_version" not in sources[0] and "transcript_version" not in sources[0]
    with store.connect() as db:
        store.verify_sources(db, sources)
    assert [store.get_audio(source_id)["text"] for source_id in ids] == texts

def test_typed_recap_verification_rejects_in_flight_member_correction(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Exact full-file text.")
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    group = next(group for group in memory.recaps.groups(utc_now())
                 if {row["id"] for row in group.sources} == set(ids))
    sources = memory.recaps.sources(group)
    store.correct_transcript(ids[0], "New human correction.")
    with store.connect() as db, pytest.raises(EvidenceChanged):
        store.verify_sources(db, sources)

def test_typed_recap_verification_rejects_in_flight_member_deletion(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Exact full-file text.")
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    group = next(group for group in memory.recaps.groups(utc_now())
                 if {row["id"] for row in group.sources} == set(ids))
    sources = memory.recaps.sources(group)
    store.delete_audio(ids[0])
    with store.connect() as db, pytest.raises(EvidenceChanged):
        store.verify_sources(db, sources)

def test_filtered_recap_scope_does_not_widen_to_full_selected_recording(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Whole file selected.")
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    full = next(group for group in memory.recaps.groups(utc_now())
                if {row["id"] for row in group.sources} == set(ids))
    from omi_local.conversation_state import Conversation
    partial = Conversation([full.sources[0]], full.closed_at, full.basis, full.proof, full.hints)
    sources = memory.recaps.sources(partial)
    assert [source["id"] for source in sources] == [ids[0]]
    assert sources[0].get("reference_type") is None

def test_recording_speaker_projection_bridges_only_same_speaker_at_part_boundary(app_recording):
    client, _, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    group = next(group for group in memory.recaps.groups(utc_now())
                 if {row["id"] for row in group.sources} == set(ids))
    scope = {
        "state": "ready", "evidence_version": "speaker-v1",
        "speakers": [
            {"id": "a", "label": "A", "name": None},
            {"id": "b", "label": "B", "name": None},
        ],
        "turns": [
            {"source_id": ids[0], "start": 0.1, "end": 1.0, "speaker_id": "a"},
            {"source_id": ids[1], "start": 0.0, "end": 1.0, "speaker_id": "a"},
            {"source_id": ids[1], "start": 0.4, "end": 0.6, "speaker_id": "b"},
        ],
    }
    memory.recaps.speaker_projection = lambda _group, db=None: {
        source_id: scope for source_id in ids
    }
    text = "cross word unknown"
    variant = {
        "text": text,
        "source_map": [
            {"source_id": ids[0], "start": 0.0, "end": 1.0},
            {"source_id": ids[1], "start": 1.0, "end": 2.0},
        ],
        "word_segments": [
            {"text": "cross", "start_offset": 0, "end_offset": 5, "start": 0.9, "end": 1.1},
            {"text": "word", "start_offset": 6, "end_offset": 10, "start": 1.45, "end": 1.55},
        ],
    }
    evidence, evidence_version, state = memory.recaps.recording_speaker_evidence(
        group, rid, variant,
    )
    assert state == "ready" and evidence_version
    assert "".join(item["text"] for item in evidence) == text
    assert evidence[0]["attribution"] == "attributed"
    assert evidence[0]["speaker_label"] == "A"
    assert evidence[0]["source_id"] == rid
    assert evidence[0]["child_source_id"] is None
    assert evidence[1]["text"] == " " and evidence[1]["attribution"] == "unknown"
    assert evidence[2]["attribution"] == "overlap"
    assert evidence[3]["text"] == " unknown" and evidence[3]["attribution"] == "unknown"

def _project_synthetic_speaker_words(client, recording_id, ids, turns, text, word_segments):
    memory = client.app.state.memory
    group = next(
        group for group in memory.recaps.groups(utc_now())
        if {row["id"] for row in group.sources} == set(ids)
    )
    scope = {
        "state": "ready",
        "evidence_version": "synthetic-speaker-turns-v1",
        "speakers": [
            {"id": "a", "label": "A", "name": None},
            {"id": "b", "label": "B", "name": None},
        ],
        "turns": turns,
    }
    memory.recaps.speaker_projection = lambda _group, db=None: {
        source_id: scope for source_id in ids
    }
    variant = {
        "text": text,
        "source_map": [
            {"source_id": ids[0], "start": 0.0, "end": 1.0},
            {"source_id": ids[1], "start": 1.0, "end": 2.0},
        ],
        "word_segments": word_segments,
    }
    return memory.recaps.recording_speaker_evidence(group, recording_id, variant)


def test_sequential_turn_transition_word_stays_unknown_and_text_provenance_is_exact(
    app_recording,
):
    client, _, recording_id, ids, _, _ = app_recording
    turns = [
        {"source_id": ids[0], "start": 0.0, "end": 0.5, "speaker_id": "a"},
        {"source_id": ids[0], "start": 0.5, "end": 1.0, "speaker_id": "b"},
    ]
    text = "before crossing after"
    evidence, evidence_version, state = _project_synthetic_speaker_words(
        client, recording_id, ids, turns, text,
        [
            {"text": "before", "start_offset": 0, "end_offset": 6,
             "start": 0.2, "end": 0.4},
            {"text": "crossing", "start_offset": 7, "end_offset": 15,
             "start": 0.4, "end": 0.6},
            {"text": "after", "start_offset": 16, "end_offset": 21,
             "start": 0.6, "end": 0.8},
        ],
    )

    assert state == "ready" and evidence_version
    assert "".join(item["text"] for item in evidence) == text
    assert [item["attribution"] for item in evidence] == [
        "attributed", "unknown", "attributed",
    ]
    before, crossing, after = evidence
    assert before["speaker_id"] == "a" and before["speaker_label"] == "A"
    assert crossing["text"] == " crossing "
    assert crossing["speaker_id"] is None and crossing["speaker_label"] is None
    assert (crossing["start"], crossing["end"]) == (0.4, 0.6)
    assert after["speaker_id"] == "b" and after["speaker_label"] == "B"
    assert {item["source_id"] for item in evidence} == {recording_id}
    assert before["child_source_id"] == ids[0]
    assert crossing["child_source_id"] is None
    assert after["child_source_id"] == ids[0]
    assert all(item["recording_id"] == recording_id for item in evidence)


def test_half_open_turn_boundaries_do_not_create_overlap(app_recording):
    client, _, recording_id, ids, _, _ = app_recording
    turns = [
        {"source_id": ids[0], "start": 0.0, "end": 0.5, "speaker_id": "a"},
        {"source_id": ids[0], "start": 0.5, "end": 1.0, "speaker_id": "b"},
    ]
    text = "left right"
    evidence, _, state = _project_synthetic_speaker_words(
        client, recording_id, ids, turns, text,
        [
            {"text": "left", "start_offset": 0, "end_offset": 4,
             "start": 0.3, "end": 0.5},
            {"text": "right", "start_offset": 5, "end_offset": 10,
             "start": 0.5, "end": 0.7},
        ],
    )

    assert state == "ready"
    assert "".join(item["text"] for item in evidence) == text
    assert [item["attribution"] for item in evidence] == [
        "attributed", "unknown", "attributed",
    ]
    assert [item["speaker_id"] for item in evidence] == ["a", None, "b"]
    assert [(item["start"], item["end"]) for item in evidence] == [
        (0.3, 0.5), (None, None), (0.5, 0.7),
    ]
    assert [item["child_source_id"] for item in evidence] == [ids[0], None, ids[0]]


def test_concurrent_distinct_speaker_intervals_mark_timed_word_overlap(app_recording):
    client, _, recording_id, ids, _, _ = app_recording
    turns = [
        {"source_id": ids[0], "start": 0.0, "end": 0.55, "speaker_id": "a"},
        {"source_id": ids[0], "start": 0.45, "end": 1.0, "speaker_id": "b"},
    ]
    text = "crossing"
    evidence, _, state = _project_synthetic_speaker_words(
        client, recording_id, ids, turns, text,
        [{"text": text, "start_offset": 0, "end_offset": len(text),
          "start": 0.4, "end": 0.6}],
    )

    assert state == "ready"
    assert len(evidence) == 1
    assert evidence[0]["text"] == text
    assert evidence[0]["attribution"] == "overlap"
    assert evidence[0]["speaker_id"] is None
    assert (evidence[0]["start"], evidence[0]["end"]) == (0.4, 0.6)
    assert evidence[0]["source_id"] == recording_id
    assert evidence[0]["child_source_id"] == ids[0]
    assert evidence[0]["recording_id"] == recording_id


def test_recording_detail_exposes_single_copy_bridged_speaker_evidence(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "hello world")
    with store.connect() as db:
        db.execute(
            "UPDATE recording_transcription_jobs SET word_segments=? WHERE result_version=?",
            ('[{"text":"hello","start_offset":0,"end_offset":5,"start":0.1,"end":0.4},'
             '{"text":"world","start_offset":6,"end_offset":11,"start":0.5,"end":0.9}]',
             version),
        )
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    scope = {
        "state": "ready", "evidence_version": "speaker-v1",
        "speakers": [{"id": "a", "label": "A", "name": "Reviewed name"}],
        "turns": [{"source_id": ids[0], "start": 0.0, "end": 1.0, "speaker_id": "a"}],
    }
    memory.recaps.speaker_projection = lambda _group, db=None: {
        source_id: scope for source_id in ids
    }
    response = client.get(f"/v1/recordings/{rid}")
    assert response.status_code == 200
    transcript = response.json()["transcript_selection"]["transcript"]
    assert transcript["speaker_evidence"]["state"] == "ready"
    pieces = transcript["speaker_evidence"]["utterances"]
    assert "".join(piece["text"] for piece in pieces) == "hello world"
    assert len(pieces) == 1
    assert pieces[0]["text"] == "hello world"
    assert pieces[0]["start_offset"] == 0 and pieces[0]["end_offset"] == 11
    assert pieces[0]["speaker_name"] == "Reviewed name"
    assert pieces[0]["child_source_id"] == ids[0]

def test_same_speaker_words_coalesce_across_parts_without_false_child_owner(app_recording):
    client, _, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    group = next(group for group in memory.recaps.groups(utc_now())
                 if {row["id"] for row in group.sources} == set(ids))
    scope = {
        "state": "ready", "evidence_version": "speaker-v1",
        "speakers": [{"id": "a", "label": "A", "name": None}],
        "turns": [
            {"source_id": ids[0], "start": 0.0, "end": 1.0, "speaker_id": "a"},
            {"source_id": ids[1], "start": 0.0, "end": 1.0, "speaker_id": "a"},
        ],
    }
    memory.recaps.speaker_projection = lambda _group, db=None: {
        source_id: scope for source_id in ids
    }
    text = "first,\nsecond"
    variant = {
        "text": text,
        "source_map": [
            {"source_id": ids[0], "start": 0.0, "end": 1.0},
            {"source_id": ids[1], "start": 1.0, "end": 2.0},
        ],
        "word_segments": [
            {"text": "first", "start_offset": 0, "end_offset": 5, "start": 0.7, "end": 0.9},
            {"text": "second", "start_offset": 7, "end_offset": 13, "start": 1.1, "end": 1.3},
        ],
    }
    evidence, _, _ = memory.recaps.recording_speaker_evidence(group, rid, variant)
    assert len(evidence) == 1
    assert evidence[0]["text"] == text
    assert (evidence[0]["start_offset"], evidence[0]["end_offset"]) == (0, len(text))
    assert evidence[0]["source_id"] == rid
    assert evidence[0]["child_source_id"] is None
    assert evidence[0]["candidate_source_ids"] == ids

def test_unknown_spoken_word_breaks_same_speaker_utterance_coalescing(app_recording):
    client, _, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    group = next(group for group in memory.recaps.groups(utc_now())
                 if {row["id"] for row in group.sources} == set(ids))
    scope = {
        "state": "ready", "evidence_version": "speaker-v1",
        "speakers": [{"id": "a", "label": "A", "name": None}],
        "turns": [
            {"source_id": ids[0], "start": 0.0, "end": 0.25, "speaker_id": "a"},
            {"source_id": ids[0], "start": 0.45, "end": 0.8, "speaker_id": "a"},
        ],
    }
    memory.recaps.speaker_projection = lambda _group, db=None: {
        source_id: scope for source_id in ids
    }
    text = "yes maybe later"
    variant = {
        "text": text,
        "source_map": [
            {"source_id": ids[0], "start": 0.0, "end": 1.0},
            {"source_id": ids[1], "start": 1.0, "end": 2.0},
        ],
        "word_segments": [
            {"text": "yes", "start_offset": 0, "end_offset": 3, "start": 0.1, "end": 0.2},
            {"text": "maybe", "start_offset": 4, "end_offset": 9, "start": 0.3, "end": 0.4},
            {"text": "later", "start_offset": 10, "end_offset": 15, "start": 0.5, "end": 0.6},
        ],
    }
    evidence, _, _ = memory.recaps.recording_speaker_evidence(group, rid, variant)
    assert "".join(item["text"] for item in evidence) == text
    assert [item["attribution"] for item in evidence] == ["attributed", "unknown", "attributed"]
    assert [item["text"] for item in evidence] == ["yes", " maybe ", "later"]


def test_partial_unknown_speaker_runs_keep_text_without_half_null_times(app_recording):
    client, _, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    group = next(group for group in memory.recaps.groups(utc_now())
                 if {row["id"] for row in group.sources} == set(ids))
    scope = {"state": "ready", "evidence_version": "speaker-v1", "speakers": [], "turns": []}
    memory.recaps.speaker_projection = lambda _group, db=None: {
        source_id: scope for source_id in ids
    }
    source_map = [
        {"source_id": ids[0], "start": 0.0, "end": 1.0},
        {"source_id": ids[1], "start": 1.0, "end": 2.0},
    ]
    cases = [
        ("timed missing", [{"text": "timed", "start_offset": 0, "end_offset": 5,
                            "start": 0.1, "end": 0.2}]),
        ("missing timed", [{"text": "timed", "start_offset": 8, "end_offset": 13,
                            "start": 0.4, "end": 0.6}]),
        ("timed, timed", [
            {"text": "timed", "start_offset": 0, "end_offset": 5,
             "start": 0.1, "end": 0.2},
            {"text": "timed", "start_offset": 7, "end_offset": 12,
             "start": 0.4, "end": 0.6},
        ]),
    ]
    for text, word_segments in cases:
        evidence, _, state = memory.recaps.recording_speaker_evidence(
            group, rid, {"text": text, "source_map": source_map, "word_segments": word_segments},
        )
        assert state == "ready"
        assert "".join(item["text"] for item in evidence) == text
        assert all((item["start"] is None) == (item["end"] is None) for item in evidence)
        if text != "timed, timed":
            assert len(evidence) == 1
            assert evidence[0]["text"] == text
            assert (evidence[0]["start_offset"], evidence[0]["end_offset"]) == (0, len(text))
            assert (evidence[0]["start"], evidence[0]["end"]) == (None, None)
        else:
            assert len(evidence) == 1
            assert (evidence[0]["start"], evidence[0]["end"]) == (0.1, 0.6)

def test_compacted_recording_evidence_keeps_exact_window_anchors(app_recording):
    _, _, rid, ids, _, _ = app_recording
    from omi_local.inference import Inference

    canonical = "first\nsecond"
    utterance = {
        "recording_id": rid, "source_id": rid, "child_source_id": None,
        "text": canonical, "start_offset": 0, "end_offset": len(canonical),
        "start": 0.7, "end": 1.3, "speaker_id": "a", "speaker_label": "A",
        "attribution": "attributed", "candidate_source_ids": ids,
    }
    source = {
        "id": rid, "recording_id": rid, "reference_type": "recording_transcription",
        "canonical_text": canonical, "speaker_evidence": [utterance],
    }
    evidence = Inference._speaker_evidence_window(source, 0, len(canonical))
    assert evidence == [utterance]
    cropped = Inference._speaker_evidence_window(source, 6, len(canonical))
    assert cropped == [{
        "source_id": rid, "text": "second", "start_offset": 6,
        "end_offset": len(canonical), "start": None, "end": None,
        "speaker_id": None, "speaker_label": None, "speaker_name": None,
        "attribution": "unknown",
    }]

def test_recap_publication_rejects_pipeline_change_during_generation(app_recording):
    client, store, rid, ids, _, _ = app_recording
    memory = client.app.state.memory
    pinned_settings(memory)
    _, version = make_ready_variant(store, memory, rid, ids, "Selected exact text.")
    assert client.put(
        f"/v1/recordings/{rid}/transcript-selection",
        json={"expected_version": 0, "result_version": version},
    ).status_code == 200
    now = utc_now()
    group = next(group for group in memory.recaps.groups(now)
                 if {row["id"] for row in group.sources} == set(ids))
    key = memory.recaps.group_key(group)
    sources = memory.recaps.sources(group)

    def production_group_guard(db):
        current = memory.recaps.groups(now, db)
        if not any(candidate.ready and memory.recaps.group_key(candidate, db) == key
                   for candidate in current):
            raise EvidenceChanged("Conversation changed during preparation")

    memory.settings.recording_transcription_timeout_seconds += 1
    with pytest.raises(EvidenceChanged):
        store.save_recap_summary(key, "[source] summary", sources, production_group_guard)
