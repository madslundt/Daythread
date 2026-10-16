// Date-only arithmetic stays in UTC so the month grid is independent of DST
// and the browser's timezone. The selected day comes from the home timezone.
export function shiftMonth(month: string, offset: number): string {
  const date = new Date(`${month}-01T12:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + offset);
  return date.toISOString().slice(0, 7);
}

export function monthDays(month: string): (string | null)[] {
  const first = new Date(`${month}-01T12:00:00Z`);
  const last = new Date(first);
  last.setUTCMonth(last.getUTCMonth() + 1, 0);
  const leading = (first.getUTCDay() + 6) % 7;
  const days: (string | null)[] = Array(leading).fill(null);
  for (let day = 1; day <= last.getUTCDate(); day++)
    days.push(`${month}-${String(day).padStart(2, "0")}`);
  while (days.length % 7) days.push(null);
  return days;
}

export function monthLabel(month: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "long", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${month}-01T12:00:00Z`));
}
