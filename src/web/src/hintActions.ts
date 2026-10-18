import { post } from "./api";
import type { Status, Timeline } from "./types";

// An explicit end always refers to the meeting the user saw. If it has already
// ended automatically, a late click must never start a different meeting.
export function sendHintEvent(action: "mark" | "meeting_toggle", eventId: string, activeMeetingId?: string, captureDeviceId?: string) {
  return post<Timeline>("/v1/events", {
    event_id: eventId,
    device_id: "web",
    occurred_at: new Date().toISOString(),
    action,
    ...(action === "meeting_toggle" ? activeMeetingId
      ? { meeting_intent: "end", meeting_id: activeMeetingId }
      : { meeting_intent: "start", ...(captureDeviceId ? { meeting_capture_device_id: captureDeviceId } : {}) } : {}),
  });
}

// Only continuous Omi audio provides uploaded evidence of quiet. Phone/browser
// microphone files are uploaded after stopping and cannot establish live quiet.
export function meetingCaptureDevice(status?: Pick<Status, "as_of" | "phones">, error?: string, now = Date.now()): string | undefined {
  const current = (value: string | null | undefined) => {
    const age = now - Date.parse(value || "");
    return Number.isFinite(age) && age >= 0 && age <= 120_000;
  };
  if (error || !current(status?.as_of)) return undefined;
  const capturing = (status?.phones || []).filter((phone) => phone.fresh && current(phone.observed_at) && phone.capture_state === "capturing");
  if (capturing.length !== 1) return undefined;
  const phone = capturing[0];
  return phone.capture_source === "omi" && current(phone.last_decoded_at) && /^[\w-]{1,80}$/.test(phone.device_id || "")
    ? phone.device_id : undefined;
}
