import { describe, expect, it } from "vitest";
import { deadlineLabel } from "./inboxTypes";

describe("inbox deadlines", () => {
  it("keeps unresolved phrases visible without inventing a date", () => {
    expect(deadlineLabel(null)).toBe("Deadline unknown");
    expect(deadlineLabel({ precision: "unknown", phrase: "next Friday" })).toBe(
      "Unresolved: next Friday",
    );
  });
  it("preserves civil date precision without a time", () => {
    expect(
      deadlineLabel({
        precision: "date",
        day: "2026-10-25",
        timezone: "Europe/Copenhagen",
      }),
    ).toBe("2026-10-25");
    expect(
      deadlineLabel({
        precision: "datetime",
        instant: "2026-10-25T01:30:00Z",
        timezone: "Europe/Copenhagen",
      }),
    ).toContain("Europe/Copenhagen");
  });
});
