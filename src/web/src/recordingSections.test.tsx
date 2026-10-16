import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SourceList } from "./SourceList";
import { RecordingSections } from "./RecordingSections";
import type { Source } from "./types";

describe("recording topic sections", () => {
  const source: Source = {
    id: "recording", device_id: "web", started_at: "2026-10-16T21:10:00Z",
    ended_at: "2026-10-16T21:12:00Z", state: "done",
    text: "☕️ Cocoa. Omi recorder. 🚗 Tesla Roadster.",
    sections: [
      { title: "Cocoa", start_offset: 0, end_offset: 10 },
      { title: "Omi recorder", start_offset: 10, end_offset: 24 },
      { title: "Tesla Roadster", start_offset: 24, end_offset: 41 },
    ],
  };
  it("renders three titled sections with exact original text, including emoji", () => {
    const html = renderToStaticMarkup(<RecordingSections source={source} />);
    expect(html).toContain("3 topic sections");
    expect(html.match(/<details>/g)).toHaveLength(3);
    expect(html).toContain("☕️ Cocoa.");
    expect(html).toContain("🚗 Tesla Roadster.");
    expect(html).toContain("Omi recorder");
  });
  it("keeps topic recordings compact with playback before topics and a closed full transcript", () => {
    const html = renderToStaticMarkup(<SourceList compact expanded timezone="UTC" sources={[source]} />);
    expect(html).toContain("2 min · 3 topics");
    expect(html.indexOf("<audio")).toBeLessThan(html.indexOf('class="recording-sections"'));
    expect(html).toContain('<details class="recording-disclosure recording-transcript">');
    expect(html).toContain('<details class="recording-disclosure recording-utilities">');
    expect(html).toContain("Correct transcript");
    expect(html).toContain("Delete recording");
    expect(html).not.toContain('aria-label="Speakers"');
  });
  it("opens the transcript for recordings without topics and for search matches", () => {
    for (const value of [{ ...source, sections: [] }, { ...source, matches: [{ text: "Cocoa", start_offset: 0, end_offset: 10, timing: "recording" }] }]) {
      const html = renderToStaticMarkup(<SourceList compact expanded timezone="UTC" sources={[value]} />);
      expect(html).toContain('<details class="recording-disclosure recording-transcript" open="">');
    }
  });
  it("preserves the existing source presentation outside the timeline", () => {
    const html = renderToStaticMarkup(<SourceList expanded timezone="UTC" sources={[source]} />);
    expect(html).not.toContain("recording-disclosure");
    expect(html).toContain("3 topic sections");
  });
  it("keeps pending or corrected recordings free of stale sections", () => {
    expect(renderToStaticMarkup(<RecordingSections source={{ ...source, sections: [] }} />)).toBe("");
    expect(renderToStaticMarkup(<RecordingSections source={{ ...source, text: null }} />)).toBe("");
  });
});
