import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CaptureStatus } from "./Today";
import { CompletenessDetails } from "./CompletenessFeedback";
import type { Status } from "./types";

const status: Status = {
  audio: { done: 100, queued: 2, transcribing: 1 },
  timezone: "UTC",
  worker_enabled: true,
  worker_running: true,
  meeting_max_minutes: 120,
  capture_paused: false,
  hint_policy: { marker_cooldown_seconds: 60, meeting_min_seconds: 60 },
  as_of: new Date().toISOString(),
  ingestion: { last_received_at: null, last_captured_at: null },
  processing: {
    queued: 2,
    retrying: 1,
    transcribing: 1,
    blocked: 1,
    oldest_received_at: null,
    recent_errors: [],
  },
};

describe("truthful capture observations", () => {
  it("shows real chunk counts and a measured queue estimate without opening diagnostics", () => {
    const html = renderToStaticMarkup(<CaptureStatus status={{ ...status, processing: {
      ...status.processing!, active: [{ id: "source", stage: "transcribing",
        completed_chunks: 1, total_chunks: 4, elapsed_seconds: 25 }],
      estimated_remaining_seconds: 125, estimate_sample_count: 3,
    } }} compact />);
    expect(html.split("</summary>")[0]).toContain("Transcribing · 1 of 4 chunks complete");
    expect(html.split("</summary>")[0]).toContain("Approx. 3 min remaining");
    expect(html).toContain('value="1" max="4"');
    expect(html).toContain("25 sec elapsed");
  });
  it("withholds processing estimates when observations are stale or the worker is stopped", () => {
    const current = { ...status, processing: { ...status.processing!,
      estimated_remaining_seconds: 120, estimate_sample_count: 2 } };
    expect(renderToStaticMarkup(<CaptureStatus status={current} error="Offline" compact />))
      .not.toContain("Approx.");
    expect(renderToStaticMarkup(<CaptureStatus status={{ ...current, worker_running: false }} />))
      .not.toContain("Approx.");
  });
  it("keeps processing problems visible while compact diagnostics are closed", () => {
    const html = renderToStaticMarkup(<CaptureStatus status={{ ...status, worker_running: false }} compact />);
    const summary = html.split("</summary>")[0];
    expect(summary).toContain("1 blocked · needs attention");
    expect(summary).toContain("2 queued · 1 processing");
    expect(summary).toContain("Transcription worker is not running");
    expect(summary).toContain("Live capture unknown");
    expect(html).not.toContain('<details class="capture-overview" open=');
  });

  it("never treats old or imported archive counts as continuous capture", () => {
    const html = renderToStaticMarkup(<CaptureStatus status={status} />);
    expect(html).toContain("phone connectivity is unknown");
    expect(html).toContain("does not establish continuous capture");
    expect(html).not.toContain("Local archive ready");
    expect(html).not.toContain("3 queued");
    expect(html).toContain("2 queued (1 retrying)");
  });
  it("shows stale status and phone observations when backend polling fails", () => {
    const html = renderToStaticMarkup(
      <CaptureStatus
        error="Backend unavailable"
        status={{
          ...status,
          phones: [
            {
              phone_id: "phone",
              fresh: true,
              observed_at: status.as_of!,
              capture_state: "capturing",
              cause: "resumed",
              pending_recordings: 2,
              recovery_items: 1,
              pending_seconds: 61,
              upload_policy: "wifi_only",
              upload_state: "waiting",
              last_decoded_at: null,
            },
          ],
        }}
      />,
    );
    expect(html).toContain("stale; current state unknown");
    expect(html).toContain("Phone state unknown");
    expect(html).toContain("wifi only");
    expect(html).toContain("Backend unavailable");
  });
  it("keeps playback, transcript, pause, and missing-file coverage separate", () => {
    const html = renderToStaticMarkup(
      <CompletenessDetails
        value={{
          start: "2026-10-15T12:00:00Z",
          end: "2026-10-15T13:00:00Z",
          seconds: {
            available_audio: 60,
            transcript: 120,
            intentional_pause: 180,
            reported_interruption: 0,
          },
          counts: {
            pending: 1,
            blocked: 0,
            transcript_only_imports: 1,
            expired_audio: 1,
            missing_audio: 2,
            silence_gate: 1,
            empty_stt: 1,
          },
          capture_gaps: [],
          capture_gap_count: 0,
          unknown_intervals: [],
          unknown_count: 2,
          truncated: false,
        }}
      />,
    );
    expect(html).toContain("1 min playable audio");
    expect(html).toContain("2 min transcript intervals");
    expect(html).toContain("3 min intentional pauses");
    expect(html).toContain("2 missing files");
    expect(html).toContain("Neither proves no speech occurred");
    expect(html).toContain("2 intervals have no known coverage");
  });
});
