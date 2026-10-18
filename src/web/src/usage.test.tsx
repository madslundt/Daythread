import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Usage, UsageDetails, type UsageMetrics, type UsageReport } from "./Usage";
import { matchesSettings, sectionFromPath } from "./SettingsNavigation";

const resource = vi.hoisted(() => ({ data: undefined as UsageReport | undefined, useResource: vi.fn() }));
vi.mock("./useResource", () => ({ useResource: (path: string, interval: number) => {
  resource.useResource(path, interval);
  return { data: resource.data, loading: false, error: "", refresh: () => {} };
} }));

const totals: UsageMetrics = {
  recordings: 4, captured_minutes: 960, retained_minutes: 240,
  submitted_minutes: 480, filtered_percent: 75, transcription_credits: 720,
  summary_credits: 45.5, other_credits: 2, unknown_credit_attempts: 0,
  provider_attempts: 0, filter_unavailable_recordings: 0,
  summary_original_characters: 10_000, summary_retained_characters: 6000,
  summary_submitted_characters: 14_000, summary_removed_fillers: 12,
  summary_omitted_sources: 2, summary_filter_runs: 3, summary_skipped_runs: 1,
  summary_requests: 4, summary_input_tokens: 3500, summary_output_tokens: 650,
  summary_unknown_token_requests: 0, summary_unknown_character_requests: 0,
  summary_filtered_percent: 40,
  summary_source_minutes: 150.25, summary_unknown_duration_runs: 0,
};
const report: UsageReport = {
  month: "2026-10",
  since: "2026-10-18T12:00:00Z", timezone: "Europe/Copenhagen",
  credits_complete: true, totals, days: [{ date: "2026-10-18", ...totals }],
};
afterEach(() => vi.unstubAllGlobals());
beforeEach(() => { resource.data = report; resource.useResource.mockClear(); });

describe("trial usage measurements", () => {
  it("distinguishes captured, retained and submitted audio, with measured credits", () => {
    const html = renderToStaticMarkup(<UsageDetails report={report} />);
    expect(html).toContain("960 min");
    expect(html).toContain("240 min");
    expect(html).toContain("480 min");
    expect(html).toContain("75%");
    expect(html).toContain("Known Daythread credits");
    expect(html).toContain("767.5");
    expect(html).toContain("extra transcription passes and retries");
    expect(html).toContain("Original audio follows your retention settings");
    expect(html).not.toContain("Credit totals are incomplete");
  });

  it.each([1, 2])("reports %i unknown charges without adding direct provider costs", (count) => {
    const day = { ...totals, unknown_credit_attempts: count };
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, credits_complete: false,
      totals: { ...day, provider_attempts: count }, days: [{ date: "2026-10-18", ...day }] }} />);
    expect(html).toContain(`Requests with unknown charges: ${count}.`);
    expect(html).toContain(`Requests using your own provider or local models: ${count}.`);
    expect(html).toContain("(incomplete)");
    expect(html).toContain("767.5");
  });

  it("keeps the smallest charged credit unit visible", () => {
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report,
      totals: { ...totals, other_credits: .001 } }} />);
    expect(html).toContain("0.001");
  });

  it("shows summary text size, filtering, requests and reported tokens separately from audio", () => {
    const html = renderToStaticMarkup(<UsageDetails report={report} />);
    expect(html).toContain("Original text (characters)</dt><dd>10,000");
    expect(html).toContain("Retained text (characters)</dt><dd>6,000");
    expect(html).toContain("Submitted text (characters)</dt><dd>14,000");
    expect(html).toContain("40%");
    expect(html).toContain("Fillers removed</dt><dd>12");
    expect(html).toContain("Summary passes skipped</dt><dd>1");
    expect(html).toContain("Summary requests</dt><dd>4");
    expect(html).toContain("Known input tokens</dt><dd>3,500");
    expect(html).toContain("Known output tokens</dt><dd>650");
    expect(html).toContain("3,500 / 650");
    expect(html).toContain("reduction passes and retries");
    expect(html).toContain("Each summary pass counts its selected source text again");
    expect(html).not.toContain("Summary measurements are incomplete");
  });

  it("shows measured summary source minutes alongside known summary credits", () => {
    const html = renderToStaticMarkup(<UsageDetails report={report} />);
    expect(html).toContain("Source audio considered for summaries</dt><dd>150.25 min");
    expect(html).toContain("Known summary credits</dt><dd>45.5");
    expect(html).toContain("Summary source minutes");
    expect(html).toContain("overlaps count once per pass");
    expect(html).toContain("including skipped content");
    expect(html).toContain("summaries are charged for text and tokens");
    expect(html).toContain("Retries and reduction add no minutes");
    expect(html).not.toContain("Summary source minutes are incomplete");
  });

  it("keeps known source minutes visible while flagging missing source durations", () => {
    const mixed = { ...totals, summary_unknown_duration_runs: 1 };
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, totals: mixed,
      days: [{ date: "2026-10-18", ...mixed }] }} />);
    expect(html).toContain("Source audio considered for summaries</dt><dd>150.25 min");
    expect(html).toContain("Passes with unknown recording duration: 1.");
    expect(html).toContain("(incomplete)");
  });

  it("keeps summary duration unknown rather than deriving it from credits or text", () => {
    const unknown = { ...totals, summary_source_minutes: 0,
      summary_unknown_duration_runs: totals.summary_filter_runs };
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, totals: unknown }} />);
    expect(html).toContain("Source audio considered for summaries</dt><dd>Not measured");
    expect(html).toContain("Known summary credits</dt><dd>45.5");
    expect(html).not.toContain("Source audio considered for summaries</dt><dd>0 min");
  });

  it("supports summary responses from before duration tracking was added", () => {
    const prior = { ...totals, summary_source_minutes: undefined,
      summary_unknown_duration_runs: undefined };
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, totals: prior,
      days: [{ date: "2026-10-18", ...prior }] }} />);
    expect(html).toContain("Source audio considered for summaries</dt><dd>Not measured");
    expect(html).toContain("Original text (characters)</dt><dd>10,000");
    expect(html).not.toContain("NaN");
  });

  it("marks historical summary sizes and token counts unknown without estimating them", () => {
    const historical: UsageMetrics = { ...totals, summary_original_characters: 0,
      summary_retained_characters: 0, summary_submitted_characters: 0,
      summary_filter_runs: 0, summary_filtered_percent: null, summary_requests: 2,
      summary_input_tokens: 0, summary_output_tokens: 0,
      summary_unknown_token_requests: 2, summary_unknown_character_requests: 2,
    };
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, totals: historical,
      days: [{ date: "2026-10-18", ...historical }] }} />);
    expect(html).toContain("Original text (characters)</dt><dd>Not measured");
    expect(html).toContain("Submitted text (characters)</dt><dd>Not measured");
    expect(html).toContain("Known input tokens</dt><dd>Not measured");
    expect(html).toContain("Requests with unknown token usage: 2.");
    expect(html).toContain("Requests with unknown submitted text size: 2.");
    expect(html).toContain("(incomplete)");
    expect(html).not.toContain("NaN");
  });

  it("supports older usage responses without summary metrics", () => {
    const legacy = Object.fromEntries(Object.entries(totals).filter(([key]) => !key.startsWith("summary_") || key === "summary_credits")) as UsageMetrics;
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, totals: legacy }} />);
    expect(html).toContain("Summary measurements are unavailable in this usage history.");
    expect(html).not.toContain("NaN");
  });

  it("explains filtering fallback and handles an empty trial without a fake percentage", () => {
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, days: [],
      totals: { ...totals, captured_minutes: 0, filtered_percent: null, filter_unavailable_recordings: 2 } }} />);
    expect(html).toContain("Speech filtering was unavailable for 2 recordings");
    expect(html).toContain("Their original audio was submitted instead");
    expect(html).toContain("No audio yet");
    expect(html).toContain("No usage recorded for this month.");
    expect(html).not.toContain("NaN");
  });

  it("makes usage discoverable through navigation and settings search", () => {
    vi.stubGlobal("window", { location: { pathname: "/settings/usage" } });
    expect(sectionFromPath()).toBe("usage");
    expect(sectionFromPath(true)).toBe("usage");
    expect(matchesSettings("usage", "processed minutes")).toBe(true);
    expect(matchesSettings("usage", "credits")).toBe(true);
  });

  it("uses the server's current month instead of inferring it from the browser clock", () => {
    resource.data = { ...report, month: "2027-01", timezone: "Pacific/Auckland" };
    const html = renderToStaticMarkup(<Usage />);
    expect(resource.useResource).toHaveBeenCalledWith("/v1/usage", 30_000);
    expect(html).toContain('type="month"');
    expect(html).toContain('min="0001-01"');
    expect(html).toContain('value="2027-01"');
    expect(html).toContain("Choose month");
    expect(html).toContain("Grouped by processing date in Pacific/Auckland for the selected month.");
  });

  it("renders all 31 days of a month without truncating daily usage", () => {
    const days = Array.from({ length: 31 }, (_, index) => ({ ...totals,
      date: `2026-10-${String(31 - index).padStart(2, "0")}` }));
    const html = renderToStaticMarkup(<UsageDetails report={{ ...report, days }} />);
    expect(html.match(/scope="row"/g)).toHaveLength(31);
    expect(html).toContain("Oct 1, 2026");
    expect(html).toContain("Oct 31, 2026");
    expect(html).not.toContain("Most recent 30 days");
  });
});
