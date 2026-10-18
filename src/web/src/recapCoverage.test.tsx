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
describe("transcribed recording recap display", () => {
  it("has no manual generation button and explains automatic updates", () => {
    resource.data = { ...resource.data!, stale: true };
    const html = render();
    expect(html).not.toContain("Generate recap");
    expect(html).not.toContain("Regenerate");
    expect(html).toContain("This recap will update automatically");
  });
  it("shows pending recording status before any summary exists", () => {
    resource.data = { ...resource.data!, included_through: null, deferred_conversations: 1, deferred_sources: 4,
      boundary_note: "Capture coverage is uncertain." };
    const html = render();
    expect(html).toContain("No transcribed recordings included yet");
    expect(html).toContain("Some recordings are awaiting upload or transcription.");
    expect(html).toContain("Capture coverage is uncertain");
    expect(html).not.toContain("no transcribed speech");
  });
  it("keeps the saved cutoff visible beside newly pending recordings", () => {
    resource.data = { ...resource.data!, included_through: "2026-10-15T12:35:00Z", deferred_conversations: 2, deferred_sources: 2 };
    const html = render();
    expect(html).toContain("Includes transcribed recordings through Oct 15, 2026, 2:35 PM");
    expect(html).toContain("Some recordings are awaiting upload or transcription.");
  });
  it("explains irrelevant fragments without implying their recordings were deleted", () => {
    resource.data = { ...resource.data!, no_recap_content: true, deferred_conversations: 1, deferred_sources: 1 };
    const html = render("2026-10-15", true);
    expect(html).toContain("No useful recap content in transcribed recordings yet");
    expect(html).toContain("Your recordings remain available");
    expect(html).toContain("Some recordings are awaiting upload or transcription.");
  });
  it("explains immediate file summaries and pause-based live recaps", () => {
    const html = render();
    expect(html).toContain("Uploaded recordings are summarized after transcription");
    expect(html).toContain("Live conversations wait for a longer pause");
    expect(html).not.toContain("waiting to finish");
  });
  it("distinguishes transcribed live speech waiting for a pause from pending uploads", () => {
    resource.data = { ...resource.data!, deferred_sources: 4, waiting_for_pause: 1,
      pending_upload_sources: 0, pending_chunks: 0 };
    const html = render();
    expect(html).toContain("Live conversations are waiting for a longer pause before being summarized");
    expect(html).not.toContain("Some recordings are awaiting upload or transcription");
  });
  it("can show a live pause and an unfinished upload at the same time", () => {
    resource.data = { ...resource.data!, waiting_for_pause: 1, pending_upload_sources: 2 };
    const html = render();
    expect(html).toContain("Live conversations are waiting for a longer pause before being summarized");
    expect(html).toContain("Some recordings are awaiting upload or transcription");
  });
  it("polls current and historical recaps automatically through the read-only resource hook", () => {
    const today = localDay("Europe/Copenhagen");
    render(today);
    expect(resource.useResource).toHaveBeenCalledWith(`/v1/recaps/${today}`, 15_000);
    resource.useResource.mockClear();
    render("2020-01-01");
    expect(resource.useResource).toHaveBeenCalledWith("/v1/recaps/2020-01-01", 15_000);
  });
});
