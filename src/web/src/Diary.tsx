import { t } from "./i18n";
import { useEffect, useState } from "react";
import { WeeklyReview } from "./WeeklyReview";
import { dayLabel, localDay } from "./format";
import { MonthCalendar } from "./Calendar";
import { Icon } from "./Icon";
import { RecapSection, QuickRewind, TranscriptTimeline } from "./Today";
import { ArchiveSearch, ArchivePassage } from "./ArchiveSearch";
import type { ChatScope } from "./types";
import { requestedRecapTopic, subscribeNavigation } from "./routing";

export function Diary({ day, timezone, onDay, onChat, onUpload, onRewind, onRewindAt }: {
  day: string;
  timezone: string;
  onDay: (day: string) => void;
  onChat: (scope: ChatScope, question?: string) => void;
  onUpload: () => void;
  onRewind: (minutes: number, localTime?: string) => void;
  onRewindAt: (end: string) => void;
}) {
  const [mode, setMode] = useState<"day" | "week">("day");
  useEffect(() => {
    const openDayEvidence = () => {
      if (requestedRecapTopic(window.location.pathname, window.location.search, day) ||
          new URLSearchParams(window.location.search).has("source")) setMode("day");
    };
    openDayEvidence();
    return subscribeNavigation(openDayEvidence);
  }, [day]);
  return <>
    <ArchiveSearch timezone={timezone} />
    <div className="button-row segmented-control" role="group" aria-label={t("Archive view")}>
      <button className={`button ${mode === "day" ? "dark" : ""}`} aria-pressed={mode === "day"} onClick={() => setMode("day")}>{t("Days")}</button>
      <button className={`button ${mode === "week" ? "dark" : ""}`} aria-pressed={mode === "week"} onClick={() => setMode("week")}>{t("Weeks")}</button>
    </div>
    {mode === "week" ? <WeeklyReview key={timezone} timezone={timezone} onChat={onChat} /> : <div className="diary-layout">
      <aside className="diary-index">
        <h2>{t("Browse by date")}</h2>
        <MonthCalendar day={day} today={localDay(timezone)} onDay={onDay} />
        <button className="button" onClick={() => onChat({ kind: "all" })}>
          <Icon name="chat" size={17} />{t("Chat across all days")}
        </button>
      </aside>
      <div className="diary-detail">
        <div className="section-heading">
          <div className="diary-date">{dayLabel(day)}</div>
          <button className="button" onClick={() => onChat({ kind: "day", day })}>
            <Icon name="chat" size={17} />{t("Chat with this day")}
          </button>
        </div>
        <ArchivePassage key={`source-${day}`} day={day} timezone={timezone} />
        <RecapSection key={day} day={day} timezone={timezone} onChat={onChat} compact archive />
        <TranscriptTimeline key={`recordings-${day}`} day={day} timezone={timezone}
          onUpload={onUpload} onChat={onChat} archive onRewindAt={onRewindAt} />
        <details className="day-utility" key={`rewind-${day}`}>
          <summary><Icon name="rewind" size={18} />{t("Quick rewind")}</summary>
          <QuickRewind historical={day !== localDay(timezone)} onRewind={onRewind} />
        </details>
      </div>
    </div>}
  </>;
}
