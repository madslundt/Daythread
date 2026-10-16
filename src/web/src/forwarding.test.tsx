import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ForwardingDetails, type ForwardingStatus } from "./AudioForwarding";
import { SourceList } from "./SourceList";
import { CompletenessDetails } from "./CompletenessFeedback";

const status: ForwardingStatus = {
  enabled: true, configured: true, ready: true, worker_enabled: false,
  accepted_requests: 3, last_received_at: "2026-10-16T12:00:00Z",
  last_source_id: "514e61aa-9409-49eb-8d10-e45fe40b04ff",
  last_failure: { at: "2026-10-16T11:59:00Z", status: 503, detail: "Check disk space" },
};

describe("official audio status and uncertain timing", () => {
  it("shows receipt, failure and worker state without promising connected capture", () => {
    const html = renderToStaticMarkup(<ForwardingDetails status={status} />);
    expect(html).toContain("3 deliveries durably accepted");
    expect(html).toContain("does not prove continuous capture or a connected pendant");
    expect(html).toContain("Check disk space");
    expect(html).toContain("New audio has arrived since this failure");
    expect(html).toContain("Transcription worker is disabled");
    expect(html).not.toContain("Connected to Omi");
  });
  it("keeps disabled and unconfigured states distinct", () => {
    expect(renderToStaticMarkup(<ForwardingDetails status={{ ...status, ready: false, enabled: false }} />)).toContain("disabled");
    expect(renderToStaticMarkup(<ForwardingDetails status={{ ...status, ready: false, configured: false }} />)).toContain("missing server token");
  });
  it("exposes sample provenance and approximate timestamps alongside playable sources", () => {
    const html = renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[{
      id: status.last_source_id!, device_id: "omi-webhook-account-hash", state: "done", text: "Test phrase",
      source_kind: "omi_audio_webhook", timestamp_basis: "receipt_end_estimate",
      source_metadata: { input_sample_rate: 8000, archive_sample_rate: 16000 },
      started_at: "2026-10-16T11:59:59Z", ended_at: "2026-10-16T12:00:00Z",
    }]} />);
    expect(html).toContain("not actual capture time");
    expect(html).toContain("pendant identity unknown");
    expect(html).toContain("Input 8000 Hz");
    expect(html).toContain(`/v1/audio/${status.last_source_id}`);
  });
  it("does not equate estimated playback intervals to known capture coverage", () => {
    const html = renderToStaticMarkup(<CompletenessDetails value={{
      start: "2026-10-16T12:00:00Z", end: "2026-10-16T13:00:00Z",
      seconds: { available_audio: 0, transcript: 0, estimated_audio: 60, estimated_transcript: 30 },
      counts: { webhook_receipt_estimates: 2, pending: 0, blocked: 0, transcript_only_imports: 0, expired_audio: 0, missing_audio: 0, silence_gate: 0, empty_stt: 0 },
      capture_gaps: [], capture_gap_count: 0, unknown_count: 1, unknown_intervals: [], truncated: false,
    }} />);
    expect(html).toContain("2 official audio chunks use receipt estimates");
    expect(html).toContain("do not establish capture coverage or remove unknown intervals");
  });
});
