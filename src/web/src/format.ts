export function localDay(timeZone: string, value = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function shiftDay(day: string, offset: number): string {
  const value = new Date(`${day}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + offset);
  return value.toISOString().slice(0, 10);
}

export const dateRangeShortcuts = [
  { id: "this_week", label: "This week" },
  { id: "this_month", label: "This month" },
  { id: "this_year", label: "This year" },
  { id: "last_week", label: "Last week" },
  { id: "last_month", label: "Last month" },
  { id: "last_year", label: "Last year" },
] as const;

export type DateRangeShortcut = (typeof dateRangeShortcuts)[number]["id"];

export function dateRangeForShortcut(
  shortcut: DateRangeShortcut,
  today: string,
): { start_day: string; end_day: string } {
  // Calendar arithmetic uses the supplied local day, independent of the browser timezone.
  const value = new Date(`${today}T12:00:00Z`);
  const previous = shortcut.startsWith("last_");
  if (shortcut.endsWith("week")) {
    const weekday = (value.getUTCDay() + 6) % 7;
    const start_day = shiftDay(today, -weekday - (previous ? 7 : 0));
    return { start_day, end_day: shiftDay(start_day, 6) };
  }
  const year = value.getUTCFullYear() - (previous && shortcut.endsWith("year") ? 1 : 0);
  if (shortcut.endsWith("year")) {
    return { start_day: `${year}-01-01`, end_day: `${year}-12-31` };
  }
  value.setUTCDate(1);
  value.setUTCMonth(value.getUTCMonth() - (previous ? 1 : 0));
  const start_day = value.toISOString().slice(0, 10);
  value.setUTCMonth(value.getUTCMonth() + 1);
  return { start_day, end_day: shiftDay(value.toISOString().slice(0, 10), -1) };
}

export function dayLabel(day: string, short = false): string {
  return new Intl.DateTimeFormat("en-US", {
    month: short ? "short" : "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${day}T12:00:00Z`));
}

export function timeLabel(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function dateTimeLabel(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function remainingCooldown(
  last: string | undefined,
  seconds: number,
  now: number,
): number {
  return last
    ? Math.max(0, Math.ceil((Date.parse(last) + seconds * 1000 - now) / 1000))
    : 0;
}

export function localDateTimeInput(value = new Date()): string {
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}
