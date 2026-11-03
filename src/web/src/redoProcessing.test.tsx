import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RedoForm, RedoProcessing, RedoProgress, redoRequest, type RedoAvailability, type RedoJob } from "./RedoProcessing";
import { matchesSettings } from "./SettingsNavigation";
import { LiveSummaryProgress, type SummaryGenerationProgress } from "./LiveSummaryProgress";

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

describe("live summary generation", () => {
  const progress: SummaryGenerationProgress = {
    day: "2026-10-31", stage: "daily_summary", status: "reasoning", request_id: "request-1",
    request_sequence: 3, active_requests: 1, request_started_at: "2026-11-03T06:55:00Z",
    elapsed_seconds: 120, last_token_at: "2026-11-03T06:56:59Z", seconds_since_token: 1,
    quiet: false, output_tokens: 1250, token_count_source: "estimate", max_tokens: 65536,
    reasoning_characters: 5000, content_characters: 0, events: 85, thinking: true,
  };

  it("distinguishes reasoning from visible output and marks approximate tokens", () => {
    const html = renderToStaticMarkup(<LiveSummaryProgress progress={progress} />);
    expect(html).toContain("Model is thinking");
    expect(html).toContain("1,250 output tokens (estimated)");
    expect(html).toContain("Reasoning: 5,000 characters · Summary: 0 characters");
    expect(html).toContain("65,536 token limit");
    expect(html).toContain("Thinking enabled for this request");
    expect(html).not.toContain("tokens reported");
  });

  it("shows provider counts and the request's effective thinking setting", () => {
    const html = renderToStaticMarkup(<LiveSummaryProgress progress={{ ...progress,
      status: "generating", token_count_source: "provider", output_tokens: 2345, thinking: false,
      content_characters: 9000,
    }} />);
    expect(html).toContain("Writing summary");
    expect(html).toContain("2,345 output tokens reported");
    expect(html).toContain("Thinking disabled for this request");
    expect(html).not.toContain("(estimated)");
  });

  it("reports silence as uncertain rather than declaring the request failed", () => {
    const html = renderToStaticMarkup(<LiveSummaryProgress progress={{ ...progress,
      status: "waiting", last_token_at: null, output_tokens: 0, quiet: true, seconds_since_token: 90,
    }} />);
    expect(html).toContain("Waiting for the first model output");
    expect(html).toContain("may be queued or stalled; this does not confirm a failure");
    expect(html).not.toContain('role="alert"');
  });

  it("clears request counters between phases and hides stale progress on terminal jobs", () => {
    const preparing = renderToStaticMarkup(<LiveSummaryProgress progress={{ ...progress, request_id: null }} />);
    expect(preparing).toContain("Preparing the next summary request");
    expect(preparing).not.toContain("output tokens");
    for (const state of ["completed", "failed"] as const) {
      const html = renderToStaticMarkup(<RedoProgress job={{ ...job, state, progress }} />);
      expect(html).not.toContain("Live summary progress");
    }
  });
});
