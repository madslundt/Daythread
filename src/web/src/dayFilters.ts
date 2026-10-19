import { profileScope, type ProfileScope } from "./profiles";
import type { Recap } from "./types";

export type DayFilters = { tagID: string; zoneID: string };
export const allDayFilters: DayFilters = { tagID: "", zoneID: "" };
let selection: { signal: AbortSignal; filters: DayFilters } | undefined;

// App remounts Today on date navigation. Keep its selection only for this live profile.
export function dayFiltersForProfile(scope: ProfileScope = profileScope()): DayFilters {
  return selection?.signal === scope.signal && !scope.signal.aborted ? selection.filters : allDayFilters;
}

export function rememberDayFilters(filters: DayFilters, scope: ProfileScope = profileScope()): void {
  if (!scope.signal.aborted) selection = { signal: scope.signal, filters };
}

export function hasDayFilters(filters: DayFilters = allDayFilters): boolean {
  return Boolean(filters.tagID || filters.zoneID);
}

export function dayFilterParams(filters: DayFilters = allDayFilters): Record<string, string> {
  return { ...(filters.tagID ? { tag_id: filters.tagID } : {}), ...(filters.zoneID ? { zone_id: filters.zoneID } : {}) };
}

export function dayFilterKey(filters: DayFilters = allDayFilters): string {
  return JSON.stringify([filters.tagID, filters.zoneID]);
}

export function recapMatchesDayFilters(recap: Recap, filters: DayFilters = allDayFilters): boolean {
  return (recap.scope?.tag_id || "") === filters.tagID && (recap.scope?.zone_id || "") === filters.zoneID;
}
