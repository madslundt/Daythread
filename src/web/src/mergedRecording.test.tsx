import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { compactSpeakerEvidence, MergedRecordingContent, type CombinedRecording } from "./MergedRecording";
import { RecordingGroups } from "./RecordingGroups";
import { createRecordingSeekController } from "./useRecordingSeek";

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
  it("marks interruptions in joined session audio and keeps finished text while processing", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{ ...recording,
      state: "processing", sections: [], interruptions: [{ position_seconds: 4, gap_seconds: 2,
        previous_end: "2026-10-16T09:00:04Z", next_start: "2026-10-16T09:00:06Z" }],
    }} timezone="UTC" />);
    expect(html).toContain("Playback joins available audio; gaps and overlaps are marked here.");
    expect(html).toContain("09:00:04");
    expect(html).toContain("09:00:06");
    expect(html).toContain("Processing audio");
    expect(html).toContain(recording.text);
    expect(html.match(/<audio /g)).toHaveLength(1);
  });
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
  it("shows the selected full transcript and preserves the original transcript access", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{ ...recording,
      recording_transcription: { state: "ready", version: "variant-v1", text: "Whole selected transcript." },
      transcript_selection: { state: "selected", selection_version: 3, result_version: "variant-v1",
        transcript: { text: "Whole selected transcript.", version: "variant-v1" } },
    }} />);
    expect(html).toContain("Using selected full-recording transcript");
    expect(html).toContain("Whole selected transcript.");
    expect(html).toContain("Original part transcripts");
    expect(html).toContain("Launch continues across upload parts.");
    expect(html).not.toContain('class="recording-topic-bookmarks"');
    expect(html).not.toContain("Use full-recording transcript");
  });
  it("shows readable selected text by default and suppresses old part bookmarks", () => {
    const canonical = "Hello there.\nUnknown word.";
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{ ...recording,
      recording_transcription: { state: "ready", version: "variant-v1", text: canonical },
      transcript_selection: { state: "selected", selection_version: 3, result_version: "variant-v1",
        transcript: {
          text: canonical, version: "variant-v1",
          speaker_evidence: {
            state: "ready",
            utterances: [
            { text: "Hello", start_offset: 0, end_offset: 5, start: 0, end: 0.4,
              attribution: "attributed", speaker_id: "a", speaker_label: "A", speaker_name: "Rae" },
            { text: " ", start_offset: 5, end_offset: 6,
              start: null, end: null, attribution: "unknown" },
            { text: "there.", start_offset: 6, end_offset: 12,
              start: 0.4, end: 0.8, attribution: "overlap" },
            { text: "\nUnknown word.", start_offset: 12, end_offset: canonical.length,
              start: null, end: null, attribution: "unknown" },
            ],
          },
        },
    }}} />);
    expect(html).toContain("Show speakers and timestamps");
    expect(html).toContain(canonical);
    expect(html.match(/Hello there\./g)).toHaveLength(1);
    expect(html).not.toContain("Rae");
    expect(html).not.toContain("recording-speaker-label");
    expect(html).not.toContain("recording-speaker-time");
    expect(html).not.toContain('class="recording-topic-bookmarks"');
    expect(html).toContain("Original part transcripts");
  });
  it("coalesces same-speaker whitespace but never absorbs unknown spoken words", () => {
    const canonical = "Hello there. Missing words.";
    const pieces = [
      { text: "Hello", start_offset: 0, end_offset: 5, start: 2.5, end: 2.8,
        attribution: "attributed", speaker_id: "a", speaker_label: "A" },
      { text: " ", start_offset: 5, end_offset: 6, attribution: "unknown" },
      { text: "there.", start_offset: 6, end_offset: 12, start: 2.8, end: 3.2,
        attribution: "attributed", speaker_id: "a", speaker_label: "A" },
      { text: " ", start_offset: 12, end_offset: 13, attribution: "unknown" },
      { text: "Missing", start_offset: 13, end_offset: 20, attribution: "unknown" },
      { text: " ", start_offset: 20, end_offset: 21, attribution: "unknown" },
      { text: "words.", start_offset: 21, end_offset: 27, start: 4, end: 4.5,
        attribution: "attributed", speaker_id: "a", speaker_label: "A" },
    ];
    const runs = compactSpeakerEvidence(pieces);
    expect(runs.map((run) => run.text).join("")).toBe(canonical);
    expect(runs.map((run) => run.attribution)).toEqual(["attributed", "unknown", "attributed"]);
    expect(runs[0]).toMatchObject({ text: "Hello there.", start: 2.5, start_offset: 0, end_offset: 12 });
    expect(runs[1]).toMatchObject({ text: " Missing ", attribution: "unknown",
      start_offset: 12, end_offset: 21 });
    expect(runs[2]).toMatchObject({ text: "words.", start: 4, start_offset: 21, end_offset: 27 });

    const pause = vi.fn();
    const audio = { pause, currentTime: 0, readyState: 1 } as unknown as HTMLAudioElement;
    const playback = createRecordingSeekController();
    playback.audioRef(audio);
    playback.seekTo(2.5);
    expect(audio.currentTime).toBe(2.5);
    expect(pause).toHaveBeenCalled();
    playback.seekTo(undefined);
    expect(audio.currentTime).toBe(2.5);

    playback.audioRef(null);
    playback.seekTo(4);
    playback.audioRef(audio);
    expect(audio.currentTime).toBe(2.5);
    playback.onReady();
    expect(audio.currentTime).toBe(4);
  });
  it("coalesces adjacent unknown speech while keeping the entire run unknown", () => {
    const runs = compactSpeakerEvidence([
      { text: "unclear", start_offset: 0, end_offset: 7, attribution: "unknown" },
      { text: " ", start_offset: 7, end_offset: 8, attribution: "unknown" },
      { text: "words.", start_offset: 8, end_offset: 14, attribution: "unknown" },
    ]);
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({ text: "unclear words.", attribution: "unknown",
      start_offset: 0, end_offset: 14 });
  });
  it("falls back to original transcript when the selection is stale", () => {
    const html = renderToStaticMarkup(<MergedRecordingContent recording={{ ...recording,
      transcript_selection: { state: "stale", selection_version: 2, result_version: "old" },
    }} />);
    expect(html).toContain("The selected transcript is out of date.");
    expect(html).toContain(recording.text!);
    expect(html).not.toContain("old transcript text");
  });
});
