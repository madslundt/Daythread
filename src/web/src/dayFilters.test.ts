import { describe, expect, it } from "vitest";
import { allDayFilters, dayFilterParams, dayFiltersForProfile, hasDayFilters, recapMatchesDayFilters, rememberDayFilters } from "./dayFilters";

describe("day filter scope", () => {
  it("keeps the selection across day remounts and resets it for a different profile lifecycle", () => {
    const first = new AbortController(), second = new AbortController();
    const scope = { id: "first", signal: first.signal };
    const filters = { tagID: "tag", zoneID: "zone" };
    rememberDayFilters(filters, scope);
    expect(dayFiltersForProfile(scope)).toEqual(filters);
    expect(dayFiltersForProfile(scope)).toEqual(filters);
    expect(dayFiltersForProfile({ id: "second", signal: second.signal })).toEqual(allDayFilters);
    first.abort();
    expect(dayFiltersForProfile(scope)).toEqual(allDayFilters);
    expect(dayFiltersForProfile({ id: "first", signal: new AbortController().signal })).toEqual(allDayFilters);
  });

  it("clears both filters together and sends only selected server scope IDs", () => {
    const scope = { id: "first", signal: new AbortController().signal };
    rememberDayFilters({ tagID: "tag", zoneID: "zone" }, scope);
    expect(dayFilterParams(dayFiltersForProfile(scope))).toEqual({ tag_id: "tag", zone_id: "zone" });
    rememberDayFilters(allDayFilters, scope);
    expect(dayFilterParams(dayFiltersForProfile(scope))).toEqual({});
    expect(hasDayFilters(dayFiltersForProfile(scope))).toBe(false);
  });

  it("rejects full-day and previous-scope recap responses while filters are active", () => {
    const recap = { day: "2026-10-19", summary: "Whole day" };
    const filters = { tagID: "tag", zoneID: "zone" };
    expect(recapMatchesDayFilters(recap, filters)).toBe(false);
    expect(recapMatchesDayFilters({ ...recap, scope: { tag_id: "other", zone_id: "zone" } }, filters)).toBe(false);
    expect(recapMatchesDayFilters({ ...recap, scope: { tag_id: "tag", zone_id: "zone" } }, filters)).toBe(true);
    expect(recapMatchesDayFilters({ ...recap, scope: { tag_id: "tag", zone_id: null } }, allDayFilters)).toBe(false);
  });
});
