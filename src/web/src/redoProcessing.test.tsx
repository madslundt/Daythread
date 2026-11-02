import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RedoForm, RedoProcessing, RedoProgress, redoRequest, type RedoAvailability, type RedoJob } from "./RedoProcessing";
import { matchesSettings } from "./SettingsNavigation";

const availability: RedoAvailability = {
  transcripts: { start: "2026-10-20", end: "2026-10-25", count: 4 },
  summaries: { start: "2026-09-01", end: "2026-10-31", count: 12 },
  job: null,
};
const job: RedoJob = {
  id: "redo-1", kind: "transcripts", start: "2026-10-20", end: "2026-10-25", state: "transcribing",
  transcript_total: 4, transcript_completed: 2, summary_total: 2, summary_completed: 0, error: null,
};

describe("redo transcripts and summaries", () => {
  it("sends all available data without synthetic date limits", () => {
    expect(redoRequest("transcripts", "all", "", "", availability.transcripts)).toEqual({ kind: "transcripts" });
    expect(redoRequest("summaries", "all", "", "", availability.summaries)).toEqual({ kind: "summaries" });
  });

  it("keeps inclusive calendar dates unchanged and uses the chosen operation's bounds", () => {
    expect(redoRequest("summaries", "range", "2026-09-01", "2026-10-31", availability.summaries))
      .toEqual({ kind: "summaries", start: "2026-09-01", end: "2026-10-31" });
    expect(redoRequest("transcripts", "range", "2026-10-20", "2026-10-20", availability.transcripts))
      .toEqual({ kind: "transcripts", start: "2026-10-20", end: "2026-10-20" });
    expect(() => redoRequest("transcripts", "range", "2026-09-01", "2026-10-25", availability.transcripts))
      .toThrow("within the available dates");
  });

  it.each([["", "2026-10-25"], ["2026-10-20", ""], ["2026-10-25", "2026-10-20"], ["2026-10-20", "2026-10-26"]])(
    "rejects missing, reversed or unavailable bounds %s – %s", (start, end) => {
      expect(() => redoRequest("transcripts", "range", start, end, availability.transcripts)).toThrow();
    },
  );

  it("disables redo for an archive with no retained audio", () => {
    const empty = { ...availability, transcripts: { start: null, end: null, count: 0 } };
    expect(() => redoRequest("transcripts", "all", "", "", empty.transcripts)).toThrow("No available data");
    const html = renderToStaticMarkup(<RedoForm availability={empty} onStart={async () => {}} />);
    expect(html).toContain("No recordings with retained audio");
    expect(html).toContain('type="submit" class="button" disabled=""');
  });

  it("explains the transcript cascade and prevents duplicate running jobs", () => {
    const html = renderToStaticMarkup(<RedoForm availability={{ ...availability, job }} onStart={async () => {}} />);
    expect(html).toContain("then regenerates summaries");
    expect(html).toContain("Transcript corrections will be replaced");
    expect(html).toContain('<fieldset disabled="">');
    expect(html).toContain("2 of 4 transcripts redone");
    expect(html).toContain("0 of 2 days summarized");
  });

  it("shows partial progress and failures without claiming completion", () => {
    const html = renderToStaticMarkup(<RedoProgress job={{ ...job, state: "failed", error: "Model unavailable" }} />);
    expect(html).toContain("Redo failed");
    expect(html).toContain("Model unavailable");
    expect(html).not.toContain("Redo completed");
    expect(html).not.toContain("<progress");
  });

  it("requires an active profile and makes redo discoverable in settings search", () => {
    expect(renderToStaticMarkup(<RedoProcessing profileSelected={false} />)).toContain("Choose a profile");
    expect(matchesSettings("redo", "redo transcripts")).toBe(true);
    expect(matchesSettings("redo", "date range")).toBe(true);
  });
});
