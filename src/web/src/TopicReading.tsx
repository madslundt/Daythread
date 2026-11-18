import { useId, useState, type ReactNode } from "react";
import { t } from "./i18n";

export function TopicReading({ summary, transcript }: { summary: ReactNode; transcript: (visible: boolean) => ReactNode }) {
  const [showTranscript, setShowTranscript] = useState(false);
  const id = useId();
  return <div className="topic-reading">
    <div role="group" aria-label={t("Topic view")} className="topic-reading-options">
      <button type="button" className="button small" aria-controls={id} aria-pressed={!showTranscript}
        onClick={() => setShowTranscript(false)}>{t("Topic summary")}</button>
      <button type="button" className="button small" aria-controls={id} aria-pressed={showTranscript}
        onClick={() => setShowTranscript(true)}>{t("Topic transcript")}</button>
    </div>
    <div id={id}>
      {!showTranscript ? summary || <p className="caption">{t("No topic summary yet")}</p> : null}
      {transcript(showTranscript)}
    </div>
  </div>;
}
