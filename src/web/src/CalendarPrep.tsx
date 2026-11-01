import { useEffect, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import { dateTimeLabel } from "./format";
import { t } from "./i18n";
import { ErrorState } from "./UI";

export type PrepCalendarEvent = {
  id: string;
  title: string;
  started_at: string;
  ended_at: string | null;
  all_day: boolean;
  attendee_candidates: string[];
  recurrence_not_expanded: boolean;
};

export function calendarContext(event: PrepCalendarEvent): string {
  return `${event.title}\n${event.started_at}${event.ended_at ? ` – ${event.ended_at}` : ""}`;
}

export function CalendarPrep({ timezone, onChoose }: {
  timezone: string;
  onChoose: (subject: string, kind: "person" | "topic", context: string) => void;
}) {
  const [events, setEvents] = useState<PrepCalendarEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [imported, setImported] = useState(false);
  const [partial, setPartial] = useState(false);
  const generation = useRef(0);
  const active = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => { generation.current++; active.current?.abort(); }, []);

  async function load(file: File) {
    const current = ++generation.current;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setError(""); setBusy(true); setEvents([]); setImported(false);
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error(t("Calendar file exceeds 2 MiB"));
      const content = await file.text();
      if (generation.current !== current) return;
      const result = await request<{
        events: PrepCalendarEvent[]; omitted: number; skipped: number;
      }>("/v1/conversation-prep/calendar/import", {
        method: "POST", signal: controller.signal, body: JSON.stringify({ content }),
      });
      if (generation.current !== current) return;
      setEvents(result.events); setPartial(result.omitted > 0 || result.skipped > 0);
      setImported(true);
    } catch (failure) {
      if (generation.current === current) setError(errorMessage(failure));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  return <details className="label-panel">
    <summary>{t("Prepare from a calendar event")}</summary>
    <p className="caption">{t("Choose an exported .ics file, then a meeting topic or attendee to review. Calendar details stay in this preparation and do not verify attendance.")}</p>
    <label>{t("Calendar file")}{" "}<input type="file" accept=".ics,text/calendar"
      disabled={busy} onChange={(event) => {
        const file = event.currentTarget.files?.[0];
        event.currentTarget.value = "";
        if (file) void load(file);
      }} /></label>
    {busy ? <p role="status">{t("Reading calendar…")}</p> : null}
    {error ? <ErrorState message={error} /> : null}
    {partial ? <p className="caption">{t("Some events were skipped or omitted. Export a smaller date range with explicit event times.")}</p> : null}
    {imported && !events.length ? <p>{t("No usable calendar events in this file.")}</p> : null}
    {events.map((event, index) => <article key={`${event.id}|${event.started_at}|${index}`}>
      <h3>{event.title}</h3>
      <p className="caption">{dateTimeLabel(event.started_at, timezone)}</p>
      {event.recurrence_not_expanded ? <p className="caption">{t("Recurring event: only the exported instance is shown.")}</p> : null}
      <div className="button-row">
        <button className="button" onClick={() => onChoose(event.title, "topic", calendarContext(event))}>
          {t("Use meeting topic")}
        </button>
        {event.attendee_candidates.map((name) => <button className="button" key={name}
          onClick={() => onChoose(name, "person", calendarContext(event))}>
          {name}
        </button>)}
      </div>
    </article>)}
  </details>;
}
