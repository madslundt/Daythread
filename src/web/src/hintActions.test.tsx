import { afterEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { sendHintEvent, meetingCaptureDevice } from "./hintActions";
import { ActiveMeeting } from "./App";

afterEach(() => vi.unstubAllGlobals());

it("sends explicit start/end intent and the observed meeting ID so late end clicks cannot reopen a meeting", async () => {
  const fetch = vi.fn(async (_url: string, _options?: RequestInit) => new Response(JSON.stringify({ markers: [], meetings: [], active_meeting: null })));
  vi.stubGlobal("fetch", fetch);
  await sendHintEvent("meeting_toggle", "start-event", undefined, "omi-capture-device");
  await sendHintEvent("meeting_toggle", "end-event", "already-ended-meeting");
  await sendHintEvent("mark", "mark-event", "meeting");
  const bodies = fetch.mock.calls.map((call) => JSON.parse(String(call[1]?.body)));
  expect(bodies[0]).toMatchObject({ action: "meeting_toggle", meeting_intent: "start", meeting_capture_device_id: "omi-capture-device" });
  expect(bodies[0]).not.toHaveProperty("meeting_id");
  expect(bodies[1]).toMatchObject({ action: "meeting_toggle", meeting_intent: "end", meeting_id: "already-ended-meeting" });
  expect(bodies[2]).toMatchObject({ action: "mark" });
  expect(bodies[2]).not.toHaveProperty("meeting_intent");
  expect(bodies[2]).not.toHaveProperty("meeting_id");
});

it("explains observed quiet without a fixed meeting deadline", () => {
  const html = renderToStaticMarkup(<ActiveMeeting captureDeviceID="omi-device" />);
  expect(html).toContain("After the first hour, recorded quiet can end it automatically");
  expect(html).toContain("Recording continues");
  expect(html).not.toContain("auto stops at");
});


it("only binds quiet to one fresh live Omi device, never a stale report or a file microphone", () => {
  const now = Date.parse("2026-10-18T18:00:00Z");
  const phone = { phone_id: "phone", device_id: "omi-device", fresh: true, observed_at: "2026-10-18T18:00:00Z",
    capture_state: "capturing", capture_source: "omi" as const, last_decoded_at: "2026-10-18T17:59:59Z",
    cause: "unknown", pending_recordings: 0, recovery_items: 0, pending_seconds: 0, upload_policy: "any_network", upload_state: "idle" };
  const status = { as_of: "2026-10-18T18:00:00Z", phones: [phone] };
  expect(meetingCaptureDevice(status, undefined, now)).toBe("omi-device");
  expect(meetingCaptureDevice(status, "refresh failed", now)).toBeUndefined();
  expect(meetingCaptureDevice(status, undefined, now + 121_000)).toBeUndefined();
  for (const phones of [[phone, { ...phone, phone_id: "other", device_id: "different" }],
    [{ ...phone, fresh: false }], [{ ...phone, capture_source: "phone" as const }],
    [{ ...phone, last_decoded_at: "2026-10-18T17:55:00Z" }], [{ ...phone, device_id: undefined }],
    [{ ...phone, device_id: "a".repeat(81) }], [{ ...phone, device_id: "omi device" }],
    [{ ...phone, device_id: "omi.device" }]]) {
    expect(meetingCaptureDevice({ ...status, phones }, undefined, now)).toBeUndefined();
  }
  expect(renderToStaticMarkup(<ActiveMeeting />)).toContain("End this meeting manually");
});
