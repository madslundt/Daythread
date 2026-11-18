import { useId, useState, type ReactNode } from "react";
import { t } from "./i18n";

export function TopicReading({ summary, transcript }: { summary: ReactNode; transcript: (visible: boolean) => ReactNode }) {
  const [showingTranscript, setShowingTranscript] = useState(false);
  return <ReadingTabs label={t("Topic view")} labels={[t("Topic summary"), t("Topic transcript")]}
    showingTranscript={showingTranscript} onChange={setShowingTranscript}>
    {!showingTranscript ? summary || <p className="caption">{t("No topic summary yet")}</p> : null}
    {transcript(showingTranscript)}
  </ReadingTabs>;
}

export function RecordingReading({ showingTranscript, onChange, children }: {
  showingTranscript: boolean; onChange: (value: boolean) => void; children: ReactNode;
}) {
  return <ReadingTabs label={t("Recording")} labels={[t("status.summary"), t("Transcript")]}
    showingTranscript={showingTranscript} onChange={onChange}>{children}</ReadingTabs>;
}

function ReadingTabs({ label, labels, showingTranscript, onChange, children }: {
  label: string; labels: [string, string]; showingTranscript: boolean; onChange: (value: boolean) => void; children: ReactNode;
}) {
  const id = useId();
  return <div className="topic-reading">
    <div role="tablist" aria-label={label} className="translation-tabs" onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = event.currentTarget.querySelectorAll("button");
      const next = event.key === "Home" ? 0 : event.key === "End" ? 1 : event.target === tabs[0] ? 1 : 0;
      tabs[next]?.focus();
    }}>
      <button type="button" role="tab" id={`${id}-summary`} aria-controls={id} aria-selected={!showingTranscript} tabIndex={showingTranscript ? -1 : 0}
        onClick={() => onChange(false)}>{labels[0]}</button>
      <button type="button" role="tab" id={`${id}-transcript`} aria-controls={id} aria-selected={showingTranscript} tabIndex={showingTranscript ? 0 : -1}
        onClick={() => onChange(true)}>{labels[1]}</button>
    </div>
    <div id={id} role="tabpanel" tabIndex={0} aria-labelledby={`${id}-${showingTranscript ? "transcript" : "summary"}`}>
      {children}
    </div>
  </div>;
}
