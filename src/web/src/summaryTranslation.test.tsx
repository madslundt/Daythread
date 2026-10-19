import type { ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecapSection } from "./RecapSection";
import { SummaryTranslation } from "./SummaryTranslation";
import type { TranscriptTranslationData } from "./transcriptTranslationRequests";

const state = vi.hoisted(() => ({ translated: true }));
const original = "## Cocoa\nBring the dark chocolate.\n\n## Garden\nConfirm the planting plan.";
const translated = "## Kakao\nMedbring den mørke chokolade.\n\n## Haven\nBekræft planteplanen.";
let offset = 0;
const translatedLines = translated.split("\n");
const blocks = original.split("\n").flatMap((line, index) => {
  const start = offset; offset += line.length + 1;
  return line ? [{ index: index > 2 ? index - 1 : index, kind: line.startsWith("#") ? "heading" : "text",
    start_offset: start, end_offset: start + line.length, original_text: line, text: translatedLines[index] }] : [];
});
const data: TranscriptTranslationData = {
  language: "da", available_languages: ["da"], already_in_target_language: false,
  translation: { language: "da", text: translated, summary_version: "version", created_at: "2026-10-19T10:00:00Z",
    structure: { version: 1, summary_version: "version", blocks } },
};

vi.mock("./TranscriptTranslation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./TranscriptTranslation")>();
  return { ...actual, TranscriptTranslation: (props: ComponentProps<typeof actual.TranscriptTranslation>) =>
    <actual.TranscriptTranslationView {...props} data={data} translated={state.translated}
      loading={false} generating={false} error="" onTranslate={() => {}} onSelect={() => {}}
      onRetry={() => {}} onLanguage={() => {}} /> };
});
vi.mock("./useResource", () => ({ useResource: (path: string | null) => ({
  data: path?.startsWith("/v1/recaps/") ? { day: "2026-10-19", summary: original,
    output_id: "11111111-1111-4111-8111-111111111111", sources: [] } : undefined,
  loading: false, error: "", refresh: () => {}, setData: () => {},
}) }));
beforeEach(() => { state.translated = true; });

describe("recap summary translation composition", () => {
  it("renders translated recap through the same interactive topic component", () => {
    const html = renderToStaticMarkup(<RecapSection day="2026-10-19" timezone="UTC" onChat={() => {}} />);
    expect(html).toContain('class="recap-workspace');
    expect(html).toContain("Kakao");
    expect(html).toContain("Haven");
    expect(html).toContain("Medbring den mørke chokolade.");
    expect(html).not.toContain("Bring the dark chocolate.");
  });

  it("keeps original content available through the same composition", () => {
    state.translated = false;
    const html = renderToStaticMarkup(<RecapSection day="2026-10-19" timezone="UTC" onChat={() => {}} />);
    expect(html).toContain('class="recap-workspace');
    expect(html).toContain("Cocoa");
    expect(html).not.toContain("Medbring den mørke chokolade.");
  });

  it("continues to render plain saved summaries for rewind and history callers", () => {
    const html = renderToStaticMarkup(<SummaryTranslation outputId="output" original={original} />);
    expect(html).toContain("Medbring den mørke chokolade.");
    expect(html).toContain("Copy summary");
    expect(html).not.toContain('class="recap-workspace');
  });
});
