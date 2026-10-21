import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DayOverviewText, DayRecapOverview, type DayOverview } from "./DayRecapOverview";
import { RecapSection } from "./RecapSection";

const state = vi.hoisted(() => ({ overview: undefined as DayOverview | undefined, paths: vi.fn(), post: vi.fn() }));
vi.mock("./api", async (importOriginal) => ({ ...await importOriginal<typeof import("./api")>(), post: state.post }));
vi.mock("./useResource", () => ({ useResource: (path: string | null, interval: number) => {
  state.paths(path, interval);
  return { data: path?.endsWith("/overview") ? state.overview
    : path === "/v1/recaps/2026-10-15" ? { day: "2026-10-15", summary: "## Garden\nDetailed planting plans." } : undefined,
  loading: false, error: "", refresh: () => {}, setData: () => {} };
} }));

const topic = { id: "Garden plans ☘️", title: "Garden plans ☘️", summary: "Detailed planting plans." };
function overview(overrides: Partial<DayOverview> = {}): DayOverview {
  return { day: "2026-10-15", summary: null, generated_at: null, stale: false,
    can_generate: false, topics: [], reading_minutes: 0, ...overrides };
}
function render() {
  return renderToStaticMarkup(<DayRecapOverview day="2026-10-15" timezone="UTC" onTopic={() => false} />);
}
type NodeProps = { children?: ReactNode; onClick?: (event: { preventDefault: () => void }) => void };
function anchors(node: ReactNode): ReactElement<NodeProps>[] {
  if (Array.isArray(node)) return node.flatMap(anchors);
  if (!isValidElement<NodeProps>(node)) return [];
  return node.type === "a" ? [node] : anchors(node.props.children);
}
beforeEach(() => { state.overview = overview(); state.paths.mockClear(); state.post.mockClear(); });

describe("on-demand saved day recap", () => {
  it("offers no generation action when the day has nothing to recap", () => {
    const html = render();
    expect(html).toContain("when there is something to recap");
    expect(html).not.toContain("<button");
    expect(state.post).not.toHaveBeenCalled();
  });
  it("offers Recap only for eligible content and reads the selected day without generating", () => {
    state.overview = overview({ can_generate: true });
    const html = render();
    expect(html).toContain(">Recap</button>");
    expect(html).toContain("It will be saved here for later");
    expect(state.paths).toHaveBeenCalledWith("/v1/recaps/2026-10-15/overview", 15_000);
    expect(state.post).not.toHaveBeenCalled();
  });
  it("loads the saved recap without another generation action while it is current", () => {
    state.overview = overview({ can_generate: true, summary: "A quiet day in the garden.",
      generated_at: "2026-10-15T18:00:00Z", reading_minutes: 1 });
    const html = render();
    expect(html).toContain("A quiet day in the garden.");
    expect(html).toContain("1 min read");
    expect(html).not.toContain("<button");
    expect(state.post).not.toHaveBeenCalled();
  });
  it("keeps saved prose readable and offers Update recap after new recordings arrive", () => {
    state.overview = overview({ can_generate: true, stale: true, summary: "The saved morning recap.",
      generated_at: "2026-10-15T12:00:00Z", reading_minutes: 2 });
    const html = render();
    expect(html).toContain("The saved morning recap.");
    expect(html).toContain(">Update recap</button>");
    expect(html).toContain("New recordings are available");
    expect(state.post).not.toHaveBeenCalled();
  });
  it("places the saved day overview before the detailed daily topics", () => {
    state.overview = overview({ summary: "An overview of the whole day.", generated_at: "2026-10-15T18:00:00Z" });
    const html = renderToStaticMarkup(<RecapSection day="2026-10-15" timezone="UTC" onChat={() => {}} />);
    expect(html).toContain("Detailed planting plans.");
    expect(html.indexOf("An overview of the whole day.")).toBeLessThan(html.indexOf('data-topic-id="recap:0:Garden"'));
  });
  it("does not load or expose an unfiltered overview inside a filtered day", () => {
    state.overview = overview({ summary: "Private full-day recap." });
    const html = renderToStaticMarkup(<RecapSection day="2026-10-15" timezone="UTC" onChat={() => {}}
      filters={{ tagID: "tag", zoneID: "" }} />);
    expect(html).not.toContain("Private full-day recap.");
    expect(state.paths.mock.calls.some(([path]) => String(path).endsWith("/overview"))).toBe(false);
  });
});

describe("day recap topic links", () => {
  it("opens the canonical saved topic from an encoded link including Unicode", () => {
    const onTopic = vi.fn();
    const rendered = DayOverviewText({ text: `We discussed [the garden](topic:${encodeURIComponent(topic.id)}).`, topics: [topic], onTopic });
    const links = anchors(rendered);
    expect(links).toHaveLength(1);
    const event = { preventDefault: vi.fn() };
    links[0].props.onClick!(event);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(onTopic).toHaveBeenCalledWith(topic);
  });
  it("leaves unknown and malformed topic references noninteractive", () => {
    const html = renderToStaticMarkup(<DayOverviewText text="See [missing](topic:Unknown) or [broken](topic:%ZZ)."
      topics={[topic]} onTopic={() => {}} />);
    expect(html).toContain("missing");
    expect(html).toContain("broken");
    expect(html).not.toContain("<a");
    expect(html).not.toContain("topic:");
  });
  it("escapes markup and never turns model-provided external or script URLs into links", () => {
    const html = renderToStaticMarkup(<DayOverviewText
      text={'<script>alert(1)</script> [external](https://example.com) [script](javascript:alert(1))'} topics={[topic]} onTopic={() => {}} />);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<a");
  });
});
