import { useContext, type ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecapSection } from "./RecapSection";
import { SummaryTranslation } from "./SummaryTranslation";
import { DayRecapOverview } from "./DayRecapOverview";
import { ActionPoints } from "./ActionPoints";
import { DisplayTranslationLanguage } from "./displayTranslations";
import type { TranscriptTranslationData } from "./transcriptTranslationRequests";

const state = vi.hoisted(() => ({ translated: true }));
const item = { id: "action", version: 3, title: "Send the launch brief", kind: "commitment", status: "pending", health: "current",
  owner: null, deadline: null, reviewed_at: null, created_at: "2026-10-19T10:00:00Z" };
const decision = { id: "decision", version: 2, content: { choice: { text: "Start with a smaller pilot", evidence: [] } },
  started_at: "2026-10-19T10:00:00Z", display_state: "tentative", review_status: "proposed", evidence_condition: "current" };
const originalSource = { id: "source", source_version: "source-version", transcript_version: "transcript-version",
  device_id: "fixture", started_at: "2026-10-19T10:00:00Z", ended_at: "2026-10-19T10:01:00Z",
  state: "done", text: "Plan a weekend walk.", sections: [{ title: "Weekend plans", start_offset: 0, end_offset: 20, chapter_id: "chapter" }] };
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
vi.mock("./displayTranslations", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./displayTranslations")>();
  return { ...actual, useDisplayTranslations: () => {
    const language = useContext(actual.DisplayTranslationLanguage);
    return { language, loading: false, error: "", retry: () => {},
      title: (input: { original_title: string }) => language === "da"
        ? ({ "Send the launch brief": "Send lanceringsoplægget", "Start with a smaller pilot": "Begynd med et mindre pilotprojekt", "Weekend plans": "Weekendplaner" }[input.original_title] || input.original_title)
        : input.original_title };
  } };
});
vi.mock("./useResource", () => ({ useResource: (path: string | null) => ({
  data: path?.endsWith("/overview") ? { day: "2026-10-19", summary: "We discussed [cocoa](topic:Cocoa).",
    output_id: "overview-output", can_generate: false, topics: [{ id: "Cocoa", title: "Cocoa" }] }
    : path?.startsWith("/v1/recaps/") ? { day: "2026-10-19", summary: original,
    output_id: "11111111-1111-4111-8111-111111111111", sources: [originalSource] }
    : path?.startsWith("/v1/inbox?") ? { items: path.includes("status=pending") ? [item] : [], total: path.includes("status=pending") ? 1 : 0 }
    : path?.startsWith("/v1/decisions?") ? { items: [decision], total: 1 } : undefined,
  loading: false, error: "", refresh: () => {}, setData: () => {},
}) }));
beforeEach(() => { state.translated = true; });

describe("recap summary translation composition", () => {
  it("offers translation for the saved day recap without generating another recap", () => {
    const html = renderToStaticMarkup(<DayRecapOverview day="2026-10-19" timezone="UTC" onTopic={() => false} />);
    expect(html).toContain("Medbring den mørke chokolade.");
    expect(html).toContain('role="tablist"');
    expect(html).not.toContain(">Recap</button>");
    expect(html).not.toContain(">Update recap</button>");
  });
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
    expect(html).toContain("Send the launch brief");
    expect(html).toContain("Start with a smaller pilot");
    expect(html).not.toContain("Send lanceringsoplægget");
  });

  it("shares the selected recap language with original overview records and keeps their controls", () => {
    const html = renderToStaticMarkup(<RecapSection day="2026-10-19" timezone="UTC" onChat={() => {}} />);
    expect(html).toContain("Send lanceringsoplægget");
    expect(html).toContain("Begynd med et mindre pilotprojekt");
    expect(html).toContain("Browse all topics");
    expect(html).not.toContain("Weekendplaner");
    expect(html).not.toContain('data-topic-id="original:chapter"');
    expect(html).toContain('aria-label="Mark complete: Send lanceringsoplægget"');
    expect(html).toContain("Review action");
    expect(html).toContain("Review decision");
    expect(html).not.toContain("Send the launch brief");
    expect(html).not.toContain("Start with a smaller pilot");
    expect(item.title).toBe("Send the launch brief");
    expect(decision.content.choice.text).toBe("Start with a smaller pilot");
  });

  it("uses a translated focus caption while evidence filtering retains canonical passages", () => {
    const focus = { title: "Cocoa", displayTitle: "Kakao", passages: [{ source_id: "source", start: 0, end: 20 }] };
    const html = renderToStaticMarkup(<DisplayTranslationLanguage value="da"><ActionPoints day="2026-10-19" timezone="UTC" focus={focus} /></DisplayTranslationLanguage>);
    expect(html).toContain("Highlights with evidence in Kakao.");
    expect(focus.title).toBe("Cocoa");
    expect(focus.passages).toEqual([{ source_id: "source", start: 0, end: 20 }]);
  });

  it("continues to render plain saved summaries for rewind and history callers", () => {
    const html = renderToStaticMarkup(<SummaryTranslation outputId="output" original={original} />);
    expect(html).toContain("Medbring den mørke chokolade.");
    expect(html).toContain("Copy summary");
    expect(html).not.toContain('class="recap-workspace');
  });
});
