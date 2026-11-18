import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SourceList } from "./SourceList";
import { RecordingSections } from "./RecordingSections";
import { RecordingGroups } from "./RecordingGroups";
import { Icon } from "./Icon";
import type { Source } from "./types";

describe("recording topic sections", () => {
  const source: Source = {
    id: "recording", device_id: "web", started_at: "2026-10-16T21:10:00Z",
    ended_at: "2026-10-16T21:12:00Z", state: "done",
    text: "☕️ Cocoa. Omi recorder. 🚗 Tesla Roadster.",
    sections: [
      { title: "Cocoa", start_offset: 0, end_offset: 10, summary: "The group preferred dark chocolate." },
      { title: "Omi recorder", start_offset: 10, end_offset: 24, summary: "The recorder arrives on Friday." },
      { title: "Tesla Roadster", start_offset: 24, end_offset: 41, summary: "No test drive date was agreed." },
    ],
  };
  it("renders the selected topic summary without opening its transcript", () => {
    const html = renderToStaticMarkup(<RecordingSections source={source} compact selectedOffset={10} onSelectOffset={() => {}} />);
    expect(html).not.toContain("The group preferred dark chocolate.");
    expect(html).toContain("The recorder arrives on Friday.");
    expect(html).not.toContain("No test drive date was agreed.");
    expect(html).not.toContain("☕️ Cocoa.");
    expect(html).toContain("Omi recorder");
    expect(html).toContain('role="tablist" aria-label="Topic view" class="translation-tabs"');
    expect(html).toMatch(/aria-selected="true" tabindex="0">Topic summary/);
    expect(html).toMatch(/aria-selected="false" tabindex="-1">Topic transcript/);
  });
  it("shows a play bookmark on a collapsed topic only when it has playable audio", () => {
    const timed = { ...source, sections: [{ ...source.sections![0], start_seconds: 0 }, ...source.sections!.slice(1)] };
    const html = renderToStaticMarkup(<RecordingSections source={timed} compact onPlay={() => {}} />);
    expect(html).toContain('aria-expanded="false"');
    expect(html.match(/class="icon-button recording-topic-play"/g)).toHaveLength(1);
    expect(html).toContain('aria-label="Listen from this topic · Cocoa"');
    expect(html).not.toContain('role="tablist"');
    expect(renderToStaticMarkup(<RecordingSections source={{ ...timed, audio_available: false }} compact onPlay={() => {}} />))
      .not.toContain('recording-topic-play');
  });
  it("switches an already-open topic between its summary and transcript without nested reading tabs", () => {
    const props = { source, compact: true, selectedOffset: 10, onSelectOffset: () => {} };
    const summary = renderToStaticMarkup(<RecordingSections {...props} showingTranscript={false} />);
    const transcript = renderToStaticMarkup(<RecordingSections {...props} showingTranscript />);
    expect(summary).toContain("The recorder arrives on Friday.");
    expect(summary).not.toContain("Omi recorder. ");
    expect(transcript).not.toContain("The recorder arrives on Friday.");
    expect(transcript).toContain('<p class="transcript-text">Omi recorder. </p>');
    expect(transcript).not.toContain("Tesla Roadster.</p>");
    expect(transcript).not.toContain('aria-label="Topic view"');
  });
  it("opens on Summary with the topic list below the player", () => {
    const html = renderToStaticMarkup(<SourceList compact expanded timezone="UTC" sources={[source]} />);
    expect(html).toContain("2 min · 3 topics");
    expect(html.indexOf("<audio")).toBeLessThan(html.indexOf('class="recording-sections"'));
    expect(html.indexOf('aria-label="Recording" class="translation-tabs"')).toBeLessThan(html.indexOf('class="recording-sections"'));
    expect(html).toMatch(/aria-selected="true" tabindex="0">Summary/);
    expect(html).toMatch(/aria-selected="false" tabindex="-1">Transcript/);
    expect(html).not.toContain('class="transcript-translation"');
    expect(html).toContain('<details class="recording-disclosure recording-utilities">');
    expect(html).not.toContain("Correct transcript");
    expect(html).toContain("Delete recording");
    expect(html).not.toContain('aria-label="Speakers"');
  });
  it("offers the transcript even for recordings without topics and for search matches", () => {
    for (const value of [{ ...source, sections: [] }, { ...source, matches: [{ text: "Cocoa", start_offset: 0, end_offset: 10, timing: "recording" }] }]) {
      const html = renderToStaticMarkup(<SourceList compact expanded timezone="UTC" sources={[value]} />);
      expect(html).toMatch(/aria-selected="false" tabindex="-1">Transcript/);
      expect(html).not.toContain('class="transcript-translation"');
    }
  });
  it("also offers topics separately from the full transcript outside the timeline", () => {
    const html = renderToStaticMarkup(<SourceList expanded timezone="UTC" sources={[source]} />);
    expect(html).toContain("Omi recorder");
    expect(html).not.toContain("The recorder arrives on Friday.");
    expect(html).toMatch(/aria-selected="false" tabindex="-1">Transcript/);
  });
  it("shows repeated uploads once with access to each original time and transcript", () => {
    const alternative: Source = {
      ...source, id: "reupload", started_at: "2026-10-16T21:17:00Z",
      ended_at: "2026-10-16T21:19:00Z", text: "Another transcript reading", title: "Original reupload", sections: [],
    };
    const html = renderToStaticMarkup(<SourceList compact expanded timezone="UTC"
      sources={[{ ...source, alternate_sources: [alternative] }]} />);
    expect(html.match(/class="source-row source-row-compact"/g)).toHaveLength(1);
    expect(html).toContain("2 uploads");
    expect(html).toContain("Other uploads of this recording (1)");
    expect(html).toContain("Original reupload");
    expect(html).toContain("21:17");
    expect(html).toContain('id="source-alternate-reupload"');
  });
  it("keeps pending or corrected recordings free of stale sections", () => {
    expect(renderToStaticMarkup(<RecordingSections source={{ ...source, sections: [] }} />)).toBe("");
    expect(renderToStaticMarkup(<RecordingSections source={{ ...source, text: null }} />)).toBe("");
  });
  it("marks only the imported meeting's own recording when another recording overlaps", () => {
    const sources = [source, { ...source, id: "unrelated" }];
    const groups = sources.map((item) => ({
      id: item.id, started_at: item.started_at, ended_at: item.ended_at,
      title: "Recording", recording_count: 1, sources: [item],
    }));
    const html = renderToStaticMarkup(<RecordingGroups groups={groups} day="2026-10-16"
      query="" timezone="UTC" onCorrect={() => {}} timeline={{ markers: [], active_meeting: null,
        meetings: [{ id: "imported-meeting", source_ids: [source.id], started_at: source.started_at,
          ended_at: source.ended_at, context_start: source.started_at, context_end: source.ended_at,
          auto_stop_at: null, qualifies_at: source.started_at, status: "closed" }],
      }} />);
    const meetingIcon = renderToStaticMarkup(<Icon name="mic" size={15} />);
    expect(html.split(meetingIcon)).toHaveLength(2);
  });
});
