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
    expect(html).toContain(">Previous</button>");
    expect(html).toContain(">Next</button>");
    expect(html).toContain('<select aria-label="Topics"');
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
  it("does not present an incomplete upload's partial transcript or audio as complete", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{
      ...recording, state: "processing", text: null, sections: [], audio_available: false,
    }} />);
    expect(html).toContain("This recording is waiting for transcription.");
    expect(html).not.toContain("<audio");
    expect(html).not.toContain("Launch continues");
  });
});
