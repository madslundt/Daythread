import { t } from "./i18n";
import { useEffect, useState } from "react";
import { WeeklyReview } from "./WeeklyReview";
import { dayLabel, localDay } from "./format";
import { Decisions } from "./Decisions";
import { MonthCalendar } from "./Calendar";
import { Icon } from "./Icon";
import { RecapSection } from "./Today";
import type { ChatScope, Recap } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading } from "./UI";

export function Diary({
  day,
  timezone,
  onDay,
  onChat,
}: {
  day: string;
  timezone: string;
  onDay: (day: string) => void;
  onChat: (scope: ChatScope, question?: string) => void;
}) {
  const [mode, setMode] = useState<"day" | "week">("day");
  const diaries = useResource<{ items: Recap[] }>("/v1/diary", 15_000);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", diaries.refresh);
    return () =>
      window.removeEventListener("omi:archive-changed", diaries.refresh);
  }, [diaries.refresh]);
  return (
    <>
      <div className="button-row" aria-label={t("Diary view")}>
        <button className={`button ${mode === "day" ? "dark" : ""}`} aria-pressed={mode === "day"} onClick={() => setMode("day")}>{t("Days")}</button>
        <button className={`button ${mode === "week" ? "dark" : ""}`} aria-pressed={mode === "week"} onClick={() => setMode("week")}>{t("Weeks")}</button>
      </div>
      {mode === "week" ? <WeeklyReview key={timezone} timezone={timezone} onChat={onChat} /> : <div className="diary-layout">
      <aside className="diary-index">
        <div className="section-heading">
          <h2>{t("Your diary")}</h2>
          <button
            className="icon-button"
            onClick={diaries.refresh}
            aria-label={t("Refresh diary")}
          >
            <Icon name="refresh" size={17} />
          </button>
        </div>
        <button className="button" onClick={() => onChat({ kind: "all" })}>
          <Icon name="chat" size={17} />
          {t("Chat across all days")}{" "}</button>
        <MonthCalendar key={day} day={day} today={localDay(timezone)} onDay={onDay} />
        {diaries.error ? (
          <ErrorState message={diaries.error} retry={diaries.refresh} />
        ) : null}
        {diaries.loading && !diaries.data ? (
          <Loading />
        ) : diaries.data?.items.length ? (
          <nav aria-label={t("Saved diary entries")}>
            {diaries.data.items.map((entry) => (
              <button
                key={entry.day}
                className={`diary-entry ${day === entry.day ? "selected" : ""}`}
                onClick={() => onDay(entry.day)}
              >
                <span>{dayLabel(entry.day, true)}</span>
                <p>
                  {entry.summary?.replace(/[#*]/g, "").slice(0, 100) ||
                    "Day recap"}
                </p>
              </button>
            ))}
          </nav>
        ) : (
          <EmptyState title={t("The start of your diary")}>
            {t("Generated recaps are saved here as Markdown, ready to return to later.")}{" "}</EmptyState>
        )}
      </aside>
      <div className="diary-detail">
        <div className="section-heading">
          <div className="diary-date">{dayLabel(day)}</div>
          <button className="button" onClick={() => onChat({ kind: "day", day })}>
            <Icon name="chat" size={17} />
            {t("Chat with this day")}{" "}</button>
        </div>
        <RecapSection
          key={day}
          day={day}
          timezone={timezone}
          onChat={onChat}
        />
        <Decisions key={`decisions-${day}`} day={day} timezone={timezone} />
      </div>
    </div>}
    </>
  );
}
