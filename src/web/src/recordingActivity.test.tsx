import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecordingActivity } from "./CaptureStatus";
import type { Status } from "./types";

const now = new Date().toISOString();
const status: Status = {
  as_of: now, audio: {}, timezone: "UTC", worker_enabled: true, worker_running: true,
  capture_paused: false, meeting_max_minutes: 120,
  hint_policy: { marker_cooldown_seconds: 60, meeting_min_seconds: 60 },
  processing: { queued: 4, transcribing: 1, blocked: 0, retrying: 1,
    oldest_received_at: now, recent_errors: [], active: [
      { id: "audio", stage: "transcribing", completed_chunks: 2, total_chunks: 5, elapsed_seconds: 10 },
    ] },
  generation: { queued: 2, running: 2, stages: { summarizing: 1, organizing: 1 } },
  phones: [{ phone_id: "phone", fresh: true, observed_at: now, capture_source: "omi",
    capture_state: "capturing", cause: "resumed", pending_recordings: 3, recovery_items: 0,
    pending_seconds: 90, upload_policy: "wifi_only", upload_state: "waiting", last_decoded_at: now }],
};

function render(value = status, error?: string) {
  return renderToStaticMarkup(<RecordingActivity status={value} error={error} />);
}

describe("Today recording activity", () => {
  it("keeps upload, transcription and AI task counts separate and exposes current stages", () => {
    const html = render();
    expect(html).toContain("Recording from Omi");
    expect(html).toContain("Recordings: 4 queued · 1 processing");
    expect(html).toContain("3 recordings waiting to upload");
    expect(html).toContain("AI tasks: 2 queued · 2 processing");
    expect(html).toContain("Summarizing · 1");
    expect(html).toContain("Organizing recording topics · 1");
    expect(html).toContain("Transcribing · 2 of 5 chunks complete");
  });

  it("never treats a connection, stale report, missing audio evidence or failed refresh as recording", () => {
    const phone = status.phones![0];
    for (const phones of [[], [{ ...phone, fresh: false }], [{ ...phone, capture_state: "paused" }],
      [{ ...phone, last_decoded_at: null }], [{ ...phone, last_decoded_at: "2020-01-01T00:00:00Z" }],
      [{ ...phone, observed_at: "2020-01-01T00:00:00Z" }]]) {
      expect(render({ ...status, phones })).not.toContain("Recording from Omi");
    }
    const offline = render(status, "Offline");
    expect(offline).toContain("Last known counts; current activity is unknown.");
    expect(offline).not.toContain("Recording from Omi");
    expect(offline).not.toContain("Summarizing · 1");
    expect(offline).not.toContain("chunks complete");
  });

  it("shows phone microphone recording and avoids inventing a source for older companions", () => {
    const phone = status.phones![0];
    expect(render({ ...status, phones: [{ ...phone, capture_source: "phone" }] })).toContain("Recording from phone microphone");
    expect(render({ ...status, phones: [{ ...phone, capture_source: undefined }] })).toContain("Recording in progress");
  });

  it("explains long AI tasks with current part progress and hides it after a failed refresh", () => {
    const value: Status = { ...status, generation: { queued: 0, running: 1,
      stages: { organizing: 1 }, active: [{ id: "topics", stage: "organizing",
        completed_chunks: 2, total_chunks: 8, elapsed_seconds: 340 }] } };
    const html = render(value);
    expect(html).toContain("Organizing recording topics · 2 of 8 chunks complete · 340 sec elapsed");
    expect(html).toContain('value="2" max="8"');
    expect(render(value, "Offline")).not.toContain("2 of 8 chunks complete");
  });

  it("keeps queued work visible when the worker is stopped without presenting old transcription as active", () => {
    const html = render({ ...status, worker_running: false });
    expect(html).toContain("Recordings: 4 queued · 1 processing");
    expect(html).toContain("Processing is waiting for the worker to start.");
    expect(html).not.toContain("chunks complete");
  });
});
