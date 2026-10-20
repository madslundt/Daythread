import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RecordingGroups, SessionSources, type RecordingGroup } from "./RecordingGroups";
import { RecordingChapters, RecordingChapterContent } from "./RecordingChapters";
import { useResource } from "./useResource";

vi.mock("./useResource", () => ({ useResource: vi.fn() }));

const group: RecordingGroup = {
  id: "group", title: "Launch planning", started_at: "2026-10-16T09:00:00Z",
  ended_at: "2026-10-16T09:40:00Z", recording_count: 40, sources: [],
};
function render(groups: RecordingGroup[]) {
  return renderToStaticMarkup(<RecordingGroups groups={groups} day="2026-10-16" query="" timezone="UTC" onCorrect={() => {}} />);
}
describe("compact recording sessions", () => {
  it("shows forty recordings as one collapsed session without loading member bodies", () => {
    const html = render([group]);
    expect(html.match(/class="source-row source-row-compact recording-session"/g)).toHaveLength(1);
    expect(html).toContain("Launch planning");
    expect(html).toContain("40 recordings");
    expect(html).toContain("09:00");
    expect(html).toContain("40 min");
    expect(html).not.toContain("<audio");
    expect(html).not.toContain("<details open");
  });
  it("keeps a singleton recording directly accessible without a session wrapper", () => {
    const html = render([{ ...group, recording_count: 1, sources: [{
      id: "original", device_id: "omi", started_at: group.started_at,
      ended_at: group.ended_at, state: "done", title: "A single recording", text: "Original words.",
    }] }]);
    expect(html).toContain("A single recording");
    expect(html).not.toContain("recording-session");
    expect(html).toContain('id="source-original"');
  });
  it("identifies forwarded audio times as estimates", () => {
    expect(render([{ ...group, estimated_times: true }])).toContain("estimated times");
  });
  it("counts subjects in the session preview instead of upload parts", () => {
    const html = render([{ ...group, recording_count: 34, topic_count: 12 }]);
    expect(html).toContain("12 topics");
    expect(html).not.toContain("34 recordings");
  });
  it("offers one subject entry spanning multiple audio parts without loading every transcript", () => {
    const html = renderToStaticMarkup(<RecordingChapters chapters={[{
      id: "chapter", title: "Launch review", started_at: group.started_at,
      parts: [0, 1].map((index) => ({ source_id: `source-${index}`, source_index: index,
        transcript_version: "version", start_offset: 0, end_offset: 100 })),
    }]} timezone="UTC" onCorrect={() => {}} />);
    expect(html.match(/<summary>/g)).toHaveLength(1);
    expect(html).toContain("Launch review");
    expect(html).toContain("1 recording");
    expect(html).not.toContain("<audio");
    expect(html).not.toContain("Loading recordings");
  });
  it("opens a topic with one player and previous/next navigation across all its parts", () => {
    const text = "First passage in the same subject.";
    const chapter = { id: "chapter", title: "Launch review", started_at: group.started_at,
      parts: Array.from({ length: 21 }, (_, index) => ({ source_id: `part-${index}`,
        source_index: index, transcript_version: "version", start_offset: 0, end_offset: text.length })) };
    vi.mocked(useResource).mockClear();
    vi.mocked(useResource).mockReturnValue({ data: {
      id: "part-0", device_id: "omi", started_at: group.started_at, ended_at: group.ended_at,
      state: "done", text, transcript_version: "version", sections: [{
        chapter_id: chapter.id, title: chapter.title, start_offset: 0, end_offset: text.length,
      }],
    }, loading: false, error: "", refresh: vi.fn(), setData: vi.fn() });
    const html = renderToStaticMarkup(<RecordingChapterContent chapter={chapter} timezone="UTC" onCorrect={() => {}} />);
    expect(html.match(/<audio /g)).toHaveLength(1);
    expect(html).toContain("First passage in the same subject.");
    expect(html).toContain(">Previous</button>");
    expect(html).toContain(">Next</button>");
    expect(useResource).toHaveBeenCalledExactlyOnceWith("/v1/transcripts/part-0");
    expect(html).not.toContain("part-1?");
    expect(html).not.toContain("Next recordings");
  });
  it("browses an hour-long upload by subjects instead of thirty-one two-minute recordings", () => {
    const items = Array.from({ length: 30 }, (_, index) => ({
      id: `part-${index}`, device_id: "manual-web", started_at: group.started_at,
      ended_at: "2026-10-16T09:02:00Z", state: "done" as const,
      text: `Upload part ${index}`, source_metadata: { recording_id: "upload", part_index: index, part_count: 31 },
    }));
    vi.mocked(useResource).mockReturnValue({ data: { items, total: 31, chapters: [
      { id: "launch", title: "Launch review", started_at: group.started_at, parts: [] },
      { id: "garden", title: "Garden plans", started_at: group.started_at, parts: [] },
    ] }, loading: false, error: "", refresh: vi.fn(), setData: vi.fn() });
    const html = renderToStaticMarkup(<SessionSources group={{ ...group, recording_count: 31 }}
      day="2026-10-16" query="" timezone="UTC" onCorrect={() => {}} />);
    expect(html).toContain("Launch review");
    expect(html).toContain("Garden plans");
    expect(html).toContain("Full recording &amp; tools");
    expect(html).not.toContain('id="source-part-');
    expect(html).not.toContain("Next recordings");
    expect(html).not.toContain("Nearby recordings, grouped for browsing");
  });
});
