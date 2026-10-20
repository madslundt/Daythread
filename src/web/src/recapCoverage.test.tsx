import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Recap } from "./types";
import { localDay } from "./format";
import { RecapSection, Today } from "./Today";
import { allDayFilters, rememberDayFilters, type DayFilters } from "./dayFilters";

const resource = vi.hoisted(() => ({ data: undefined as Recap | undefined, useResource: vi.fn() }));
vi.mock("./useResource", () => ({
  useResource: (path: string, interval: number) => {
    resource.useResource(path, interval);
    return { data: path?.startsWith("/v1/recaps/") ? resource.data : undefined,
      loading: false, error: "", refresh: () => {}, setData: () => {} };
  },
}));
function render(day = "2026-10-15", compact = false, filters: DayFilters = allDayFilters) {
  return renderToStaticMarkup(<RecapSection day={day} timezone="Europe/Copenhagen" compact={compact} filters={filters} onChat={() => {}} />);
}
beforeEach(() => {
  resource.data = { day: "2026-10-15", summary: null };
  resource.useResource.mockClear();
  rememberDayFilters(allDayFilters);
});
describe("transcribed recording recap display", () => {
  it("offers summary translation without replacing the original topic controls", () => {
    resource.data = { ...resource.data!, output_id: "11111111-1111-4111-8111-111111111111",
      summary: "## Cocoa\nA test with dark chocolate." };
    const html = render();
    expect(html).toContain('aria-label="Summary language"');
    expect(html).toContain("Checking summary language and saved translation…");
    expect(html).toContain('aria-label="Copy summary: Day recap"');
    expect(html).toContain('data-topic-id="recap:0:Cocoa"');
    expect(html).toContain('class="recap-topic-option" aria-expanded="false"');
  });
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
  it("keeps a selective empty day quiet while offering deliberate original topic exploration", () => {
    resource.data = { ...resource.data!, no_recap_content: true, sources: [{
      id: "11111111-1111-4111-8111-111111111111", device_id: "test", started_at: "2026-10-15T10:00:00Z",
      ended_at: "2026-10-15T10:01:00Z", state: "done", text: "We compared garden tools.",
      sections: [{ title: "Garden tools", start_offset: 0, end_offset: 25 }],
    }] };
    const html = render();
    expect(html).not.toContain("Day overview");
    expect(html).not.toContain("Explore topics");
    expect(html).toContain("Browse all topics");
    expect(html).not.toContain("Garden tools");
    expect(html).not.toContain("Original passages");
    expect(html).not.toContain('class="recap-topic-option"');
    expect(html).not.toContain("The story of your day starts here.");
    expect(html.match(/No useful recap content in transcribed recordings yet/g)).toHaveLength(1);
    expect(html).not.toContain("We compared garden tools.");
    expect(html).not.toContain('aria-label="Summary language"');
    expect(html).not.toContain('aria-label="Copy summary: Day recap"');
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

  it("uses the same tag/location intersection for recap, pending/reviewed highlights and decisions", () => {
    const filters = { tagID: "tag", zoneID: "zone" };
    resource.data = { ...resource.data!, summary: "## Work\nOnly matching evidence.", scope: { tag_id: "tag", zone_id: "zone" } };
    const html = render("2026-10-15", true, filters);
    const paths = resource.useResource.mock.calls.map(([path]) => String(path));
    for (const prefix of ["/v1/recaps/", "/v1/inbox?", "/v1/decisions?"]) {
      const matching = paths.filter((path) => path.startsWith(prefix));
      expect(matching.length).toBeGreaterThan(0);
      for (const path of matching) {
        const query = new URLSearchParams(path.split("?")[1]);
        expect(query.get("tag_id")).toBe("tag");
        expect(query.get("zone_id")).toBe("zone");
      }
    }
    expect(html).toContain("Only matching evidence.");
    for (const unscoped of ["Export Markdown", "Saved versions", "Summary language", "Chat with this day", "Find highlights", "Find decisions"])
      expect(html).not.toContain(unscoped);
  });

  it("never displays a full-day narrative as the response to a filtered recap request", () => {
    resource.data = { ...resource.data!, summary: "Private excluded full-day narrative." };
    const html = render("2026-10-15", false, { tagID: "tag", zoneID: "" });
    expect(html).not.toContain("Private excluded full-day narrative.");
    expect(html).toContain("The recap does not match the selected filters.");
  });

  it("hides invalidated scoped prose while its separate recap is being prepared", () => {
    resource.data = { ...resource.data!, summary: "Previous membership narrative.", stale: true,
      scope: { tag_id: "tag", zone_id: null }, generation_state: "queued" };
    const html = render("2026-10-15", false, { tagID: "tag", zoneID: "" });
    expect(html).not.toContain("Previous membership narrative.");
    expect(html).toContain("Preparing a recap for the selected filters…");
  });

  it("places one global filter bar above recap and timeline and preserves its selection on another day", () => {
    rememberDayFilters({ tagID: "tag", zoneID: "zone" });
    resource.data = { ...resource.data!, scope: { tag_id: "tag", zone_id: "zone" }, no_matching_sources: true };
    const props = { timezone: "UTC", onUpload: () => {}, onPrepare: () => {}, onRewind: () => {}, onChat: () => {} };
    for (const day of ["2026-10-15", "2026-10-14"]) {
      const html = renderToStaticMarkup(<Today {...props} day={day} />);
      expect(html.indexOf('aria-label="Day filters"')).toBeLessThan(html.indexOf('class="recap-section"'));
      expect(html.indexOf('aria-label="Day filters"')).toBeLessThan(html.indexOf('class="timeline-section"'));
      expect(html.match(/<select/g)?.length).toBe(4); // Tag, location, highlight category and quick rewind.
      expect(html).toContain("Clear filters");
      expect(html).toContain("No recordings match the selected filters for this day.");
      expect(html).not.toContain("The day recap still covers the whole day.");
      const timelinePath = resource.useResource.mock.calls.map(([path]) => String(path)).reverse().find((path) => path.startsWith("/v1/recording-groups?"))!;
      const query = new URLSearchParams(timelinePath.split("?")[1]);
      expect(query.get("day")).toBe(day); expect(query.get("tag_id")).toBe("tag"); expect(query.get("zone_id")).toBe("zone"); expect(query.get("offset")).toBe("0");
    }
  });
});
