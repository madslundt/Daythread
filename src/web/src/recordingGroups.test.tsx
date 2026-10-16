import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecordingGroups, type RecordingGroup } from "./RecordingGroups";

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
    expect(html).toContain("09:40");
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
});
