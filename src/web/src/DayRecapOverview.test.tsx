import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DayOverviewText, DayRecapOverview, WeeklyRecapOverview, type DayOverview } from "./DayRecapOverview";
import { RecapSection } from "./RecapSection";

const state = vi.hoisted(() => ({ overview: undefined as DayOverview | undefined, paths: vi.fn(), post: vi.fn() }));
vi.mock("./api", async (importOriginal) => ({ ...await importOriginal<typeof import("./api")>(), post: state.post }));
vi.mock("./useResource", () => ({ useResource: (path: string | null, interval: number) => {
  state.paths(path, interval);
  return { data: path?.endsWith("/overview") ? state.overview
    : path === "/v1/recaps/2026-10-15" ? { day: "2026-10-15", summary: "## Garden\nDetailed planting plans." } : undefined,
  loading: false, error: "", refresh: () => {}, setData: () => {} };
} }));

const topic = { id: "Garden plans ☘️", title: "Garden plans ☘️", summary: "Detailed planting plans.", day: "2026-10-15" };
function overview(overrides: Partial<DayOverview> = {}): DayOverview {
  return { day: "2026-10-15", summary: null, generated_at: null, stale: false,
    can_generate: false, topics: [], reading_minutes: 0, ...overrides };
}
function render() {
  return renderToStaticMarkup(<DayRecapOverview day="2026-10-15" timezone="UTC" onTopic={() => false} />);
}
type NodeProps = { children?: ReactNode; href?: string; onClick?: (event: { preventDefault: () => void; ctrlKey?: boolean }) => void };
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
    state.overview = overview({ can_generate: false, summary: "A quiet day in the garden.",
      generated_at: "2026-10-15T18:00:00Z", reading_minutes: 1 });
    const html = render();
    expect(html).toContain("A quiet day in the garden.");
    expect(html).toContain("1 min read");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain(">Expand recap</button>");
    expect(html).not.toContain(">Recap</button>");
    expect(html).not.toContain(">Update recap</button>");
    expect(state.post).not.toHaveBeenCalled();
  });
  it("does not offer generation or announce new recordings for a settings-only stale recap", () => {
    state.overview = overview({ can_generate: false, stale: true, summary: "The saved recap." });
    const html = render();
    expect(html).toContain("The saved recap.");
    expect(html).not.toContain(">Recap</button>");
    expect(html).not.toContain(">Update recap</button>");
    expect(html).not.toContain("New recordings are available");
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

describe("saved weekly recap", () => {
  const renderWeek = () => renderToStaticMarkup(<WeeklyRecapOverview week="2026-10-12" timezone="UTC" />);
  it("reads the selected week without generating and offers no action for an empty week", () => {
    const html = renderWeek();
    expect(state.paths).toHaveBeenCalledWith("/v1/weekly-reviews/2026-10-12/overview", 15_000);
    expect(html).toContain("Your weekly recap will be available");
    expect(html).not.toContain("<button");
    expect(state.post).not.toHaveBeenCalled();
  });
  it("offers on-demand generation only for new eligible weekly recordings", () => {
    state.overview = overview({ can_generate: true });
    expect(renderWeek()).toContain(">Recap</button>");
    state.overview = overview({ summary: "A short week with family plans.", stale: true });
    const html = renderWeek();
    expect(html).toContain("A short week with family plans.");
    expect(html).toContain(">Expand recap</button>");
    expect(html).not.toContain(">Recap</button>");
    expect(html).not.toContain(">Update recap</button>");
  });
  it("distinguishes two days with the same topic title", () => {
    const topics = ["2026-10-12", "2026-10-13"].map((day) => ({ ...topic, day, id: `${day}:${topic.id}` }));
    const onTopic = vi.fn();
    const node = DayOverviewText({ topics, onTopic, text: topics.map((item) => `[Garden](topic:${encodeURIComponent(item.id)})`).join(" and ") });
    const links = anchors(node);
    expect(links).toHaveLength(2);
    links[1].props.onClick!({ preventDefault: () => {} });
    expect(onTopic).toHaveBeenCalledWith(topics[1]);
    expect(links[0].props.href).toContain("/diary/2026-10-12?topic=");
    expect(links[1].props.href).toContain("/diary/2026-10-13?topic=");
  });
  it("navigates weekly links to the existing day recap without a copied topic section", () => {
    const html = renderToStaticMarkup(<DayOverviewText topics={[topic]}
      text={`We discussed [the garden](topic:${encodeURIComponent(topic.id)}).`} />);
    expect(html).toContain('href="/diary/2026-10-15?topic=Garden+plans+%E2%98%98%EF%B8%8F"');
    expect(html).not.toContain("Detailed planting plans.");
    expect(html).not.toContain("day-recap-saved-topic");
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
  it("retains the canonical topic when only the inline label is translated", () => {
    const onTopic = vi.fn();
    const rendered = DayOverviewText({ text: `Vi talte om [haveplanerne](topic:${encodeURIComponent(topic.id)}).`, topics: [topic], onTopic });
    const links = anchors(rendered);
    expect(links).toHaveLength(1);
    links[0].props.onClick!({ preventDefault: () => {} });
    expect(onTopic).toHaveBeenCalledWith(topic);
    expect(renderToStaticMarkup(rendered)).toContain("haveplanerne");
  });
  it("preserves new-tab clicks for the canonical day and topic destination", () => {
    const onTopic = vi.fn();
    const link = anchors(DayOverviewText({ text: `[Garden](topic:${encodeURIComponent(topic.id)})`, topics: [topic], onTopic }))[0];
    const event = { preventDefault: vi.fn(), ctrlKey: true };
    link.props.onClick!(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(onTopic).not.toHaveBeenCalled();
    expect(link.props.href).toContain("/diary/2026-10-15?topic=");
  });
  it("escapes markup and never turns model-provided external or script URLs into links", () => {
    const html = renderToStaticMarkup(<DayOverviewText
      text={'<script>alert(1)</script> [external](https://example.com) [script](javascript:alert(1))'} topics={[topic]} onTopic={() => {}} />);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<a");
  });
});
