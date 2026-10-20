import { afterEach, describe, expect, it, vi } from "vitest";
import { usageDuration } from "./usageDuration";

afterEach(() => vi.unstubAllGlobals());

describe("usage durations", () => {
  it.each([
    [0, "0 sec"], [0.25, "15 sec"], [35.23, "35 min 14 sec"],
    [59.999, "1 hr"], [60, "1 hr"], [90, "1 hr 30 min"],
    [1440, "1 day"], [8334.15, "5 days 18 hr"],
    [10080, "1 wk"], [11520, "1 wk 1 day"], [525600, "1 yr"],
  ])("formats %s minutes as %s", (minutes, expected) => {
    expect(usageDuration(minutes)).toBe(expected);
  });

  it("localizes duration units", () => {
    vi.stubGlobal("navigator", { languages: ["da-DK"] });
    expect(usageDuration(90)).toBe("1 t. 30 min.");
  });
});
