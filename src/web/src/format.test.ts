import { describe, expect, it } from "vitest";
import { dateRangeForShortcut, localDay, remainingCooldown, shiftDay } from "./format";
import { rewindAudioUrl } from "./api";
import { encodeWav } from "./audioUpload";

describe("capture windows", () => {
  it("uses the home timezone at a date boundary", () => {
    expect(
      localDay("Europe/Copenhagen", new Date("2026-10-15T23:10:00Z")),
    ).toBe("2026-10-16");
    expect(
      localDay("America/Los_Angeles", new Date("2026-10-16T02:00:00Z")),
    ).toBe("2026-10-15");
  });
  it("moves by calendar day across daylight saving changes", () => {
    expect(shiftDay("2026-10-25", 1)).toBe("2026-10-26");
    expect(shiftDay("2027-01-01", -1)).toBe("2026-12-31");
  });
  it("expires cooldown from the accepted flag rather than repeated clicks", () => {
    const start = "2026-10-16T12:00:00Z";
    expect(remainingCooldown(start, 60, Date.parse(start) + 10_500)).toBe(50);
    expect(remainingCooldown(start, 60, Date.parse(start) + 60_000)).toBe(0);
    expect(remainingCooldown(undefined, 60, 0)).toBe(0);
  });
  it("preserves the exact rewind end timestamp for audio playback", () => {
    const end = "2026-10-16T12:00:00.123Z";
    const url = new URL(rewindAudioUrl(10, end), "http://localhost");
    expect(url.searchParams.get("end")).toBe(end);
    expect(url.searchParams.get("minutes")).toBe("10");
  });
});

describe("chat date range shortcuts", () => {
  it.each([
    ["this_week", "2026-10-12", "2026-10-18"],
    ["this_month", "2026-10-01", "2026-10-31"],
    ["this_year", "2026-01-01", "2026-12-31"],
    ["last_week", "2026-10-05", "2026-10-11"],
    ["last_month", "2026-09-01", "2026-09-30"],
    ["last_year", "2025-01-01", "2025-12-31"],
  ] as const)("resolves %s as an inclusive calendar period", (preset, start_day, end_day) => {
    expect(dateRangeForShortcut(preset, "2026-10-16")).toEqual({ start_day, end_day });
  });
  it("handles year rollover, leap years, and Sunday week boundaries", () => {
    expect(dateRangeForShortcut("last_month", "2026-01-01")).toEqual({ start_day: "2025-12-01", end_day: "2025-12-31" });
    expect(dateRangeForShortcut("last_month", "2024-03-31")).toEqual({ start_day: "2024-02-01", end_day: "2024-02-29" });
    expect(dateRangeForShortcut("this_week", "2027-01-03")).toEqual({ start_day: "2026-12-28", end_day: "2027-01-03" });
  });
  it("uses the diary day at a timezone boundary and across daylight saving", () => {
    const today = localDay("Europe/Copenhagen", new Date("2026-10-25T23:30:00Z"));
    expect(dateRangeForShortcut("last_week", today)).toEqual({ start_day: "2026-10-19", end_day: "2026-10-25" });
  });
});

describe("audio preparation", () => {
  it("writes mono PCM16 WAV at 16 kHz and clips out of range samples", () => {
    const output = encodeWav(new Float32Array([-2, 0, 2]));
    const view = new DataView(output);
    expect(output.byteLength).toBe(50);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getInt16(44, true)).toBe(-32768);
    expect(view.getInt16(46, true)).toBe(0);
    expect(view.getInt16(48, true)).toBe(32767);
  });
});
