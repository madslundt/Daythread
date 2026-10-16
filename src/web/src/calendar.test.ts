import { describe, expect, it } from "vitest";
import { monthDays, monthLabel, shiftMonth } from "./calendarDates";

describe("month calendar", () => {
  it("aligns the first day to a Monday-first week and pads complete weeks", () => {
    const days = monthDays("2026-10");
    expect(days.slice(0, 4)).toEqual([null, null, null, "2026-10-01"]);
    expect(days[33]).toBe("2026-10-31");
    expect(days[34]).toBeNull();
    expect(days.length).toBe(35);
    expect(monthDays("2026-02").length).toBe(35);
    expect(monthDays("2026-03").length).toBe(42);
  });
  it("includes leap days only in leap years", () => {
    expect(monthDays("2024-02").filter(Boolean)).toHaveLength(29);
    expect(monthDays("2026-02").filter(Boolean)).toHaveLength(28);
    expect(monthDays("2100-02").filter(Boolean)).toHaveLength(28);
    expect(monthDays("2000-02").filter(Boolean)).toHaveLength(29);
  });
  it("moves between months without overflowing from the selected day", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2024-01", 1)).toBe("2024-02");
    expect(shiftMonth("0001-02", -1)).toBe("0001-01");
  });
  it("keeps civil dates and headings consistent across DST months", () => {
    expect(monthDays("2026-03").filter(Boolean)).toHaveLength(31);
    expect(monthDays("2026-10").filter(Boolean)).toHaveLength(31);
    expect(monthLabel("2026-10")).toBe("October 2026");
  });
});
