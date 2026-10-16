import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Recap } from "./types";
import { localDay } from "./format";
import { RecapSection } from "./Today";

const resource = vi.hoisted(() => ({ data: undefined as Recap | undefined, useResource: vi.fn() }));
vi.mock("./useResource", () => ({
  useResource: (path: string, interval: number) => {
    resource.useResource(path, interval);
    return { data: resource.data, loading: false, error: "", refresh: () => {}, setData: () => {} };
  },
}));
function render(day = "2026-10-15", compact = false) {
  return renderToStaticMarkup(<RecapSection day={day} timezone="Europe/Copenhagen" compact={compact} onChat={() => {}} />);
}
beforeEach(() => {
  resource.data = { day: "2026-10-15", summary: null };
  resource.useResource.mockClear();
});
describe("completed conversation recap display", () => {
  it("shows active conversation status before any summary exists", () => {
    resource.data = { ...resource.data!, included_through: null, deferred_conversations: 1, deferred_sources: 4,
      boundary_note: "Capture coverage is uncertain." };
    const html = render();
    expect(html).toContain("No completed conversations included yet");
    expect(html).toContain("1 conversation is waiting to finish");
    expect(html).toContain("Capture coverage is uncertain");
    expect(html).not.toContain("no transcribed speech");
  });
  it("keeps the saved cutoff visible beside newly pending conversations", () => {
    resource.data = { ...resource.data!, included_through: "2026-10-15T12:35:00Z", deferred_conversations: 2 };
    const html = render();
    expect(html).toContain("Includes completed conversations through Oct 15, 2026, 2:35 PM");
    expect(html).toContain("2 conversations are waiting to finish");
  });
  it("explains irrelevant fragments without implying their recordings were deleted", () => {
    resource.data = { ...resource.data!, no_recap_content: true, deferred_conversations: 1 };
    const html = render("2026-10-15", true);
    expect(html).toContain("No useful recap content in completed conversations yet");
    expect(html).toContain("recordings and transcripts remain in the archive under your retention settings");
    expect(html).toContain("1 conversation is waiting to finish");
  });
  it("polls only today's recap through the existing read-only resource hook", () => {
    const today = localDay("Europe/Copenhagen");
    render(today);
    expect(resource.useResource).toHaveBeenCalledWith(`/v1/recaps/${today}`, 60_000);
    resource.useResource.mockClear();
    render("2020-01-01");
    expect(resource.useResource).toHaveBeenCalledWith("/v1/recaps/2020-01-01", 0);
  });
});
