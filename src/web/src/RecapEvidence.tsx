import { t } from "./i18n";
import { useId, useState } from "react";
import { timeLabel } from "./format";
import { ImportanceButton } from "./Importance";
import { recapSourceSections } from "./recapItems";
import { TranscriptPassage } from "./TranscriptTranslation";
import { SourceList } from "./SourceList";
import type { Source } from "./types";
import { audioUrl } from "./api";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";

// Topic passages stay separate from the optional complete recording.
export function RecapEvidence({ source, point, topic = "", timezone, onCorrect, sections: selectedSections }: {
  source: Source; point: string; topic?: string; timezone: string; onCorrect: () => void;
  sections?: NonNullable<Source["sections"]>;
}) {
  const [fullRecording, setFullRecording] = useState(false);
  const id = useId();
  const sections = selectedSections ?? recapSourceSections(point, source, topic);
  return <article className="recap-evidence">
    <div className="recap-evidence-heading">
      <span className="caption">{timeLabel(source.started_at, timezone)}</span>
      <span>{sections.length === 1 ? sections[0].title : source.title || t("Recording")}</span>
      <ImportanceButton target={{ kind: "source", id: source.id }} label={t("recording")} compact />
    </div>
    <p className="caption">{sections.length ? t("Matching topic passages") : t("Original transcript · no specific topic passage identified. Open the full recording to inspect its context.")}</p>
    {sections.length ? sections.map((section) => <blockquote key={section.start_offset}>
      {sections.length > 1 ? <p className="caption">{section.title}</p> : null}
      {section.start_seconds !== undefined && source.audio_available !== false && source.audio_availability !== "missing"
        ? <RecordingAudioPlayer label={section.title}
          src={`${audioUrl(source.id)}#t=${section.start_seconds},${section.end_seconds}`} /> : null}
      <TranscriptPassage source={source} section={section} />
    </blockquote>) : null}
    <details className="recap-full-recording" onToggle={(event) => {
      if (event.target === event.currentTarget) setFullRecording(event.currentTarget.open);
    }}>
      <summary>{t("Full recording & tools")}</summary>
      {fullRecording ? <SourceList sources={[source]} timezone={timezone} expanded onCorrect={onCorrect} idPrefix={`recap-full-${id}`} /> : null}
    </details>
  </article>;
}
