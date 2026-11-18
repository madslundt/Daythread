import { t } from "./i18n";
import { useId, useState } from "react";
import { timeLabel } from "./format";
import { ImportanceButton } from "./Importance";
import { recapSourceSections } from "./recapItems";
import { TranscriptPassage } from "./TranscriptTranslation";
import { SourceList } from "./SourceList";
import type { Source } from "./types";
import { audioUrl, recordingAudioUrl } from "./api";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { useSourceTurns, useSpeakerFilters, useSpeakerReading } from "./RecordingTranscript";

// Topic passages stay separate from the optional complete recording.
export function RecapEvidence({ source, point, topic = "", timezone, onCorrect, sections: selectedSections, search = false }: {
  source: Source; point: string; topic?: string; timezone: string; onCorrect: () => void;
  sections?: NonNullable<Source["sections"]>;
  search?: boolean;
}) {
  const [fullRecording, setFullRecording] = useState(false);
  const id = useId();
  const isRecordingVariant = source.reference_type === "recording_transcription";
  const sourceAudioUrl = isRecordingVariant
    ? recordingAudioUrl(source.recording_id || source.id) : audioUrl(source.id);
  const sections = selectedSections ?? recapSourceSections(point, source, topic);
  const turns = useSourceTurns(source, !isRecordingVariant && !!sections.length);
  const filters = useSpeakerFilters();
  const canPlay = source.audio_available !== false && source.audio_availability !== "missing";
  const reading = useSpeakerReading(turns, `${source.id}:${source.transcript_version ?? ""}`, filters,
    canPlay, canPlay && !isRecordingVariant);
  return <article className="recap-evidence">
    <div className="recap-evidence-heading">
      <span className="caption">{timeLabel(source.started_at, timezone)}</span>
      <span>{sections.length === 1 ? sections[0].title : source.title || t("Recording")}</span>
      {!isRecordingVariant ? <ImportanceButton target={{ kind: "source", id: source.id }} label={t("recording")} compact /> : null}
    </div>
    {!search ? <p className="caption">{sections.length ? t("Matching topic passages") : t("Original transcript · no specific topic passage identified. Open the full recording to inspect its context.")}</p> : null}
    {sections.length ? sections.map((section) => <blockquote key={section.start_offset}>
      {sections.length > 1 ? <p className="caption">{section.title}</p> : null}
      {(search || section.start_seconds !== undefined) && source.audio_available !== false && source.audio_availability !== "missing"
        ? <RecordingAudioPlayer label={section.title} skipIntervals={reading.intervals}
          src={section.start_seconds === undefined ? sourceAudioUrl : `${sourceAudioUrl}#t=${section.start_seconds},${section.end_seconds}`} /> : null}
      {search && section.start_seconds === undefined && source.audio_available !== false && source.audio_availability !== "missing"
        ? <p className="caption">{t("Search excerpt · full transcript below. Timing covers the recording interval.")}</p> : null}
      {search && (source.audio_available === false || source.audio_availability === "missing")
        ? <p className="caption">{t("Original audio is unavailable.")}</p> : null}
      <TranscriptPassage source={source} section={section} reading={reading} fullTurns={turns} />
    </blockquote>) : null}
    <details className="recap-full-recording" onToggle={(event) => {
      if (event.target === event.currentTarget) setFullRecording(event.currentTarget.open);
    }}>
      <summary>{t("Full recording & tools")}</summary>
      {fullRecording ? isRecordingVariant ? <>
        {source.audio_available ? <RecordingAudioPlayer label={source.title || t("Recording")}
          src={sourceAudioUrl} /> : null}
        {source.text ? <p className="transcript-text">{source.text}</p> : null}
      </> : <SourceList sources={[source]} timezone={timezone} expanded onCorrect={onCorrect} idPrefix={`recap-full-${id}`} /> : null}
    </details>
  </article>;
}
