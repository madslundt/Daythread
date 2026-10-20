import { displayLocale } from "./i18n";

/** Fixed elapsed time, using at most two units (a year is 365 days). */
export function usageDuration(minutes: number): string {
  let seconds = Math.max(0, Math.round(minutes * 60));
  const units = [
    ["year", 365 * 86400], ["week", 7 * 86400], ["day", 86400],
    ["hour", 3600], ["minute", 60], ["second", 1],
  ] as const;
  const parts: string[] = [];
  for (const [unit, size] of units) {
    const count = Math.floor(seconds / size);
    if (count > 0 || unit === "second" && parts.length === 0) {
      parts.push(new Intl.NumberFormat(displayLocale(), {
        style: "unit", unit, unitDisplay: "short", maximumFractionDigits: 0,
      }).format(count));
      seconds %= size;
      if (parts.length === 2) break;
    }
  }
  return parts.join(" ");
}
