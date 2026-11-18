import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { archiveHitPath } from "./ArchiveSearch";
import { rewindAnchor } from "./Rewind";
import { QuickRewind } from "./Today";

describe("archive browsing", () => {
  it("links to versioned evidence on the capture day in the archive timezone", () => {
    expect(archiveHitPath({ id: "source-1", started_at: "2026-10-15T22:30:00Z",
      transcript_version: "v1", match: { text: "Found it", start_offset: 15, end_offset: 23 },
    }, "Europe/Copenhagen")).toBe("/diary/2026-10-16?source=source-1&version=v1&start=15&end=23");
  });

  it("requires a time on historical days and sends it without the browser timezone", () => {
    const html = renderToStaticMarkup(<QuickRewind historical onRewind={() => {}} />);
    expect(html).toContain('type="time"');
    expect(html).toContain('required=""');
    expect(rewindAnchor({ id: 1, minutes: 10, day: "2026-09-13", localTime: "14:30" }))
      .toEqual({ day: "2026-09-13", local_time: "14:30" });
  });

  it("preserves an exact recording-end anchor rather than substituting now", () => {
    expect(rewindAnchor({ id: 1, minutes: 10, end: "2026-09-13T14:30:42Z" }))
      .toEqual({ end: "2026-09-13T14:30:42Z" });
  });
});
