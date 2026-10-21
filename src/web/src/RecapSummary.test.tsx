import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { RecapSummaryView, recapPreview } from "./RecapSummary";
import { DayOverviewText } from "./DayRecapOverview";

const topic = { id: "2026-10-20:Garden ☘️", title: "Garden ☘️", day: "2026-10-20" };
const text = `We discussed [garden plans](topic:${encodeURIComponent(topic.id)}). ` + "Useful context. ".repeat(40) + "The final detail.";
const renderContent = (value: string) => <DayOverviewText text={value} topics={[topic]} onTopic={() => {}} />;

describe("recap disclosure", () => {
  it("defaults to a short plain preview with no hidden interactive links or full recap tail", () => {
    const html = renderToStaticMarkup(<RecapSummaryView text={text} renderContent={renderContent}
      expanded={false} id="recap-text" onToggle={() => {}} />);
    expect(html).toContain("We discussed garden plans.");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-controls="recap-text"');
    expect(html).toContain(">Expand recap</button>");
    expect(html).not.toContain("<a");
    expect(html).not.toContain("topic:");
    expect(html).not.toContain("The final detail.");
    expect(recapPreview(text).length).toBeLessThanOrEqual(241);
  });
  it("expands the complete recap including canonical links and optional topic detail", () => {
    const html = renderToStaticMarkup(<RecapSummaryView text={text} renderContent={renderContent}
      expanded id="recap-text" onToggle={() => {}}><p>Saved topic detail</p></RecapSummaryView>);
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain(">Collapse recap</button>");
    expect(html).toContain("The final detail.");
    expect(html).toContain("Saved topic detail");
    expect(html).toContain('href="/diary/2026-10-20?topic=Garden+%E2%98%98%EF%B8%8F"');
  });
  it("uses one explicit toggle without invoking generation", () => {
    const toggle = vi.fn();
    const node = RecapSummaryView({ text, renderContent, expanded: false, id: "recap-text", onToggle: toggle });
    type Props = { children?: ReactNode; onClick?: () => void };
    function button(value: ReactNode): ReactElement<Props> | undefined {
      if (Array.isArray(value)) return value.map(button).find(Boolean);
      if (!isValidElement<Props>(value)) return;
      return value.type === "button" ? value : button(value.props.children);
    }
    expect(toggle).not.toHaveBeenCalled();
    button(node)!.props.onClick!();
    expect(toggle).toHaveBeenCalledOnce();
  });
  it("keeps concise translated previews readable without Markdown targets", () => {
    expect(recapPreview("## Ugen\nVi drøftede **planer** og [haven](topic:Garden)."))
      .toBe("Ugen Vi drøftede planer og haven.");
  });
});
