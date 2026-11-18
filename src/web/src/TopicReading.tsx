import { useId, useState, type ReactNode } from "react";
import { t } from "./i18n";

export function TopicReading({ summary, transcript }: { summary: ReactNode; transcript: (visible: boolean) => ReactNode }) {
  const [showTranscript, setShowTranscript] = useState(false);
  const id = useId();
  return <div className="topic-reading">
    <div role="tablist" aria-label={t("Topic view")} className="translation-tabs" onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = event.currentTarget.querySelectorAll("button");
      const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : event.target === tabs[0] ? 1 : 0;
      tabs[next]?.focus();
    }}>
      <button type="button" role="tab" id={`${id}-summary`} aria-controls={id} aria-selected={!showTranscript} tabIndex={showTranscript ? -1 : 0}
        onClick={() => setShowTranscript(false)}>{t("Topic summary")}</button>
      <button type="button" role="tab" id={`${id}-transcript`} aria-controls={id} aria-selected={showTranscript} tabIndex={showTranscript ? 0 : -1}
        onClick={() => setShowTranscript(true)}>{t("Topic transcript")}</button>
    </div>
    <div id={id} role="tabpanel" aria-labelledby={`${id}-${showTranscript ? "transcript" : "summary"}`}>
      {!showTranscript ? summary || <p className="caption">{t("No topic summary yet")}</p> : null}
      {transcript(showTranscript)}
    </div>
  </div>;
}
