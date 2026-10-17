import { t } from "./i18n";
import { useEffect, useId, useRef, useState } from "react";
import { monthDays, monthLabel, shiftMonth } from "./calendarDates";
import { dayLabel, shiftDay } from "./format";
import { Icon } from "./Icon";
import type { CalendarMonth } from "./types";
import { ErrorState } from "./UI";
import { useResource } from "./useResource";

type CalendarProps = {
  day: string;
  today: string;
  onDay: (day: string) => void;
};

export function MonthCalendar({ day, today, onDay }: CalendarProps) {
  const [month, setMonth] = useState(day.slice(0, 7));
  const [activeDay, setActiveDay] = useState(day);
  const resource = useResource<CalendarMonth>(`/v1/calendar?month=${month}`, 30_000);
  const grid = useRef<HTMLTableElement>(null);
  const focusDay = useRef<string | null>(null);
  const heading = useId();
  const keyboardHint = useId();
  const days = monthDays(month);
  const tabDay = activeDay.startsWith(month) && activeDay <= today
    ? activeDay
    : day.startsWith(month) && day <= today ? day : days.find((value) => value !== null);
  const summaries = new Set(
    resource.data?.month === month
      ? resource.data.days.filter((entry) => entry.has_summary).map((entry) => entry.day)
      : [],
  );
  useEffect(() => {
    window.addEventListener("omi:archive-changed", resource.refresh);
    return () => window.removeEventListener("omi:archive-changed", resource.refresh);
  }, [resource.refresh]);
  useEffect(() => {
    setMonth(day.slice(0, 7));
    setActiveDay(day);
  }, [day]);
  useEffect(() => {
    if (focusDay.current) {
      grid.current?.querySelector<HTMLButtonElement>(`[data-day="${focusDay.current}"]`)?.focus();
      focusDay.current = null;
    }
  }, [month]);
  function moveFocus(value: string, offset: number) {
    const target = shiftDay(value, offset);
    if (target < "0001-01-01" || target > today) return;
    setActiveDay(target);
    if (target.slice(0, 7) !== month) {
      focusDay.current = target;
      setMonth(target.slice(0, 7));
    } else {
      grid.current?.querySelector<HTMLButtonElement>(`[data-day="${target}"]`)?.focus();
    }
  }
  return (
    <section className="month-calendar" aria-labelledby={heading}>
      <div className="calendar-heading">
        <button type="button" className="icon-button" aria-label={t("Previous month")} disabled={month <= "0001-01"}
          onClick={() => setMonth(shiftMonth(month, -1))}>
          <Icon name="chevron-left" size={18} />
        </button>
        <label className="calendar-month-picker">
          <span id={heading}>{monthLabel(month)}</span>
          <input type="month" aria-label={t("Choose month")} value={month} min="0001-01" max={today.slice(0, 7)}
            onChange={(event) => {
              const value = event.target.value;
              if (/^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value) && value <= today.slice(0, 7)) setMonth(value);
            }} />
        </label>
        <button type="button" className="icon-button" aria-label={t("Next month")} disabled={month >= today.slice(0, 7)}
          onClick={() => setMonth(shiftMonth(month, 1))}>
          <Icon name="chevron-right" size={18} />
        </button>
      </div>
      <p id={keyboardHint} className="sr-only">{t("Use arrow keys to move between days. Home and End move to the start and end of a week. Press Enter to choose a day.")}</p>
      <table ref={grid} className="calendar-grid" aria-label={monthLabel(month)} aria-describedby={keyboardHint}>
        <thead><tr>{[t("Mon"), t("Tue"), t("Wed"), t("Thu"), t("Fri"), t("Sat"), t("Sun")].map((weekday) => (
          <th key={weekday} scope="col">{t(weekday)}</th>
        ))}</tr></thead>
        <tbody>{Array.from({ length: days.length / 7 }, (_, week) => (
          <tr key={week}>{days.slice(week * 7, week * 7 + 7).map((value, index) => (
            <td key={value || index}>{value ? (
              <button type="button" className={`calendar-day ${value === day ? "selected" : ""} ${value === today ? "is-today" : ""}`}
                data-day={value} disabled={value > today} aria-pressed={value === day}
                tabIndex={value === tabDay ? 0 : -1}
                aria-current={value === today ? "date" : undefined}
                aria-label={`${dayLabel(value, true)}${summaries.has(value) ? t(", summary available") : ""}`}
                onFocus={() => setActiveDay(value)}
                onClick={() => onDay(value)}
                onKeyDown={(event) => {
                  const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
                  if (offset !== undefined) { event.preventDefault(); moveFocus(value, offset); }
                  else if (event.key === "Home" || event.key === "End") {
                    event.preventDefault();
                    const weekday = days.indexOf(value) % 7;
                    const weekOffset = event.key === "Home" ? -weekday : 6 - weekday;
                    const target = shiftDay(value, weekOffset);
                    if (target > today) {
                      grid.current?.querySelector<HTMLButtonElement>(`[data-day="${today}"]`)?.focus();
                    } else moveFocus(value, weekOffset);
                  }
                }}>
                <span>{Number(value.slice(-2))}</span>
                <span className="calendar-indicators" aria-hidden="true">
                  {summaries.has(value) ? <span className="summary-dot" /> : null}
                </span>
              </button>
            ) : null}</td>
          ))}</tr>
        ))}</tbody>
      </table>
      <div className="calendar-footer">
        <span className="calendar-legend"><span className="summary-dot" /> {t("Saved summary")}</span>
        <button type="button" className="text-button" onClick={() => { setMonth(today.slice(0, 7)); onDay(today); }}>{t("Today")}</button>
      </div>
      <div className="calendar-status" aria-live="polite">
        {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} />
          : resource.loading ? t("Checking summaries…")
          : resource.data && !summaries.size ? t("No saved summaries this month.") : null}
      </div>
    </section>
  );
}

export function DateNavigator({ day, today, onDay }: CalendarProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className="date-navigator">
      <button type="button" className="icon-button" onClick={() => onDay(shiftDay(day, -1))}
        disabled={day <= "0001-01-01"} aria-label={t("Previous day")}>
        <Icon name="chevron-left" size={19} />
      </button>
      <button type="button" className="date-picker calendar-trigger" aria-label={t("Choose day, {value0}", { value0: dayLabel(day) })}
        aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
        <span>{dayLabel(day)}</span>
      </button>
      <button type="button" className="icon-button" onClick={() => onDay(shiftDay(day, 1))}
        disabled={day >= today} aria-label={t("Next day")}>
        <Icon name="chevron-right" size={19} />
      </button>
      {open ? <CalendarDialog day={day} today={today} onDay={onDay} onClose={() => setOpen(false)} /> : null}
    </div>
  );
}

function CalendarDialog({ day, today, onDay, onClose }: CalendarProps & { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement;
    element?.showModal();
    element?.querySelector<HTMLButtonElement>('.calendar-day[aria-pressed="true"]')?.focus();
    return () => {
      element?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);
  return (
    <dialog ref={dialog} className="calendar-dialog" aria-labelledby={heading} onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === dialog.current) onClose(); }}>
      <div className="calendar-dialog-content">
        <div className="section-heading">
          <h2 id={heading}>{t("Choose a day")}</h2>
          <button type="button" className="icon-button" aria-label={t("Close calendar")} onClick={onClose}><Icon name="close" /></button>
        </div>
        <MonthCalendar day={day} today={today} onDay={(value) => { onDay(value); onClose(); }} />
      </div>
    </dialog>
  );
}
