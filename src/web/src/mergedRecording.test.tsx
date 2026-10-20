import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MergedRecordingContent, type CombinedRecording } from "./MergedRecording";
import { RecordingGroups } from "./RecordingGroups";

const recording: CombinedRecording = {
  id: "uploaded-file", title: "Launch and garden", started_at: "2026-10-16T09:00:00Z",
  ended_at: "2026-10-16T10:06:00Z", state: "done", duration_seconds: 3960,
  audio_available: true, text: "☕ Launch continues across upload parts. Garden plans.",
  transcript_version: "current",
  sections: [
    { title: "Launch", chapter_id: "launch", start_offset: 0, end_offset: 40, start_seconds: 0 },
    { title: "Garden", chapter_id: "garden", start_offset: 40, end_offset: 53, start_seconds: 1600 },
  ],
};

describe("combined uploaded recording", () => {
  it("lists a returning topic once and counts adjacent passages as one recording", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{ ...recording, sections: [
      { ...recording.sections[0], end_offset: 10, end_seconds: 10 },
      { ...recording.sections[0], chapter_id: "launch-continuation", start_offset: 10, end_offset: 20, start_seconds: 10, end_seconds: 20 },
      { ...recording.sections[1], start_offset: 20, end_offset: 30, start_seconds: 20, end_seconds: 30 },
      { ...recording.sections[0], chapter_id: "launch-return", start_offset: 30, start_seconds: 30 },
    ] }} />);
    expect(html.match(/<li /g)).toHaveLength(2);
    expect(html).toContain("2 recordings");
    expect(html).toContain('aria-label="Recording 2 · 00:30"');
    expect(html.match(/<audio /g)).toHaveLength(1);
  });
  it("renders one continuous player, one full transcript and topics without chunk rows", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={recording} />);
    expect(html.match(/<audio /g)).toHaveLength(1);
    expect(html).toContain("01:06:00");
    expect(html).toContain("☕ Launch continues across upload parts. Garden plans.");
    expect(html).toContain("/v1/recordings/uploaded-file/audio");
    expect(html).toContain("Download archived audio");
    expect(html).not.toContain("/v1/audio/");
    expect(html).not.toContain("source-row");
    expect(html).not.toContain("Full recording &amp; tools");
    expect(html).not.toContain("Next recordings");
    expect(html).toContain('class="recording-topic-bookmarks"');
    expect(html).toContain('aria-current="true"');
    expect(html).toContain('26:40');
    expect(html).not.toContain('<select');
    expect(html.indexOf('<audio')).toBeLessThan(html.indexOf('class="recording-topic-bookmarks"'));
    expect(html.match(/class="recording-player-play"/g)).toHaveLength(1);
  });
  it("uses the combined recording view even though the upload counts as one recording", () => {
    const html = renderToStaticMarkup(<RecordingGroups groups={[{
      id: "first-part", recording_id: recording.id, title: recording.title,
      started_at: recording.started_at, ended_at: recording.ended_at,
      recording_count: 1, topic_count: 2, sources: [],
    }]} day="2026-10-16" query="" timezone="UTC" onCorrect={() => {}} />);
    expect(html).toContain("Launch and garden");
    expect(html).toContain("2 topics");
    expect(html).toContain("recording-session");
    expect(html).not.toContain("1 recordings");
  });
  it("labels chunk-based bookmarks as approximate while keeping their topics readable", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{ ...recording, sections: [
      { ...recording.sections[0], start_estimated: true }, recording.sections[1],
    ] }} />);
    expect(html).toContain("estimated times");
    expect(html).toContain('title="00:00 · Launch · estimated times"');
    expect(html).toContain('title="26:40 · Garden"');
    expect(html.match(/<audio /g)).toHaveLength(1);
  });
  it("does not present an incomplete upload's partial transcript or audio as complete", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{
      ...recording, state: "processing", text: null, sections: [], audio_available: false,
    }} />);
    expect(html).toContain("This recording is waiting for transcription.");
    expect(html).not.toContain("<audio");
    expect(html).not.toContain("Launch continues");
  });
});
