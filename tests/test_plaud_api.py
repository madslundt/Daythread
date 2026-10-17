from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from omi_local.plaud_api import PlaudExport, normalize_plaud_export

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
SRT = (
    "1\n00:00:01,250 --> 00:00:02,500\nAda: The kitchen arrives Friday\n\n"
    "2\n00:00:03,000 --> 00:00:05,100\nSpeaker 2: I will be home\nThanks\n"
)


def export(**changes):
    return PlaudExport.model_validate({
        "content": SRT, "source_id": "original-recording-id",
        "started_at": AT, "ended_at": AT + timedelta(minutes=5), **changes,
    })


def test_srt_preserves_capture_time_speech_speaker_labels_and_subtitle_offsets():
    source = normalize_plaud_export(export())
    assert source.source_id == "original-recording-id"
    assert source.started_at == AT and source.ended_at == AT + timedelta(minutes=5)
    assert source.text == "Ada: The kitchen arrives Friday\nSpeaker 2: I will be home\nThanks"
    assert [(s.start, s.end) for s in source.segments] == [(1.25, 2.5), (3, 5.1)]
    assert source.segments[0].speaker_name is None  # No undocumented label parsing.


def test_bom_windows_line_endings_and_unicode_are_supported():
    source = normalize_plaud_export(export(content="\ufeff" + SRT.replace("\n", "\r\n")))
    assert len(source.segments) == 2
    plain = normalize_plaud_export(export(format="txt", content="  Æble 🍎\r\nSpeech  "))
    assert plain.text == "Æble 🍎\nSpeech" and plain.segments == []


def test_export_identity_is_stable_distinguishes_capture_dates_and_preserves_upstream_id():
    first = normalize_plaud_export(export(source_id=None))
    assert first.source_id.startswith("export-")
    assert normalize_plaud_export(export(source_id=None)).source_id == first.source_id
    later = normalize_plaud_export(export(
        source_id=None, started_at=AT + timedelta(days=1),
        ended_at=AT + timedelta(days=1, minutes=5),
    ))
    assert later.source_id != first.source_id
    changed = normalize_plaud_export(export(content=SRT.replace("Friday", "Thursday")))
    assert changed.source_id == "original-recording-id"


def test_fallback_identity_uses_capture_instant_independent_of_timezone_representation():
    first = normalize_plaud_export(export(source_id=None))
    another_timezone = normalize_plaud_export(export(
        source_id=None, started_at="2026-10-15T14:00:00+02:00",
        ended_at="2026-10-15T14:05:00+02:00",
    ))
    assert first.source_id == another_timezone.source_id


@pytest.mark.parametrize("content", [
    "Not SRT", "1\n00:00:00,000 --> 00:00:01,000", "0\n00:00:00,000 --> 00:00:01,000\nText",
    "1\n00:00:00.000 --> 00:00:01.000\nText",
    "1\n00:60:00,000 --> 00:61:00,000\nText",
    "1\n00:00:02,000 --> 00:00:01,000\nText",
    SRT + "\n2\n00:00:06,000 --> 00:00:07,000\nRepeated index",
    SRT + "\n3\n00:00:02,000 --> 00:00:03,000\nOut of order",
    "1\n00:00:00,000 --> 00:06:00,000\nBeyond capture",
])
def test_invalid_subtitles_are_rejected_without_guessing_offsets(content):
    with pytest.raises(ValueError):
        normalize_plaud_export(export(content=content))


@pytest.mark.parametrize("changes", [
    {"content": ""}, {"content": " \n\t"}, {"content": "\ufeff"}, {"content": "Text\x00"},
    {"source_id": ""}, {"source_id": "  "}, {"source_id": "x" * 201},
    {"format": "pdf"}, {"started_at": "2026-10-15T12:00:00"},
    {"summary": "A summary is not an accepted field"}, {"content": "a" * 1_000_001},
    {"content": "🍎" * 250_001},
])
def test_export_boundary_rejects_invalid_fields(changes):
    with pytest.raises(ValidationError):
        export(**changes)


@pytest.mark.parametrize("changes", [
    {"ended_at": AT}, {"ended_at": AT - timedelta(seconds=1)},
    {"started_at": AT - timedelta(days=8)},
    {"ended_at": "2999-01-01T00:00:00Z"},
    {"format": "txt", "content": "a" * 200_001},
    {"content": "1\n00:00:00,000 --> 00:00:01,000\n" + "a" * 20001},
])
def test_normalization_enforces_common_recording_bounds(changes):
    with pytest.raises(ValidationError):
        normalize_plaud_export(export(**changes))


def test_segment_count_is_bounded():
    content = "\n\n".join(
        f"{index}\n00:00:00,000 --> 00:00:01,000\nText" for index in range(1, 10002)
    )
    with pytest.raises(ValueError, match="too many"):
        normalize_plaud_export(export(content=content))


def test_overlapping_speech_does_not_require_disjoint_subtitles():
    content = "1\n00:00:00,000 --> 00:00:02,000\nOne\n\n2\n00:00:01,000 --> 00:00:03,000\nTwo"
    assert len(normalize_plaud_export(export(content=content)).segments) == 2
