import { useCallback, useMemo, useState } from "react";
import { t } from "./i18n";
import { profileResourceUrl } from "./profiles";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";
import { playbackTime } from "./recordingWaveform";
import { useRecordingSeek } from "./useRecordingSeek";
import { groupTopicRuns } from "./topicOccurrences";
import { RecordingTopicList } from "./RecordingTopicList";

export type CombinedRecording = {
  id: string; title: string; started_at: string; ended_at: string;
  state: string; duration_seconds: number; audio_available: boolean;
  audio_availability?: string;
  text: string | null; transcript_version: string;
  sections: { title: string; chapter_id: string; start_offset: number; end_offset: number;
    start_seconds?: number; end_seconds?: number; start_estimated?: boolean }[];
};

export function MergedRecording({ recordingID }: { recordingID: string }) {
  const resource = useResource<CombinedRecording>(`/v1/recordings/${encodeURIComponent(recordingID)}`, 15_000);
  return <>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {!resource.data && resource.loading ? <Loading>{t("Loading recordings…")}</Loading> : null}
    {resource.data ? <MergedRecordingContent key={recordingID} recording={resource.data} /> : null}
  </>;
}

export function MergedRecordingContent({ recording }: { recording: CombinedRecording }) {
  const playback = useRecordingSeek();
  const topics = useMemo(() => groupTopicRuns(recording.sections, (section) => section.title,
    (left, right) => left.end_offset === right.start_offset), [recording.sections]);
  const occurrences = useMemo(() => topics.flatMap((topic) => topic.occurrences.map((sections, index) => ({
    ...sections[0], id: `${sections[0].chapter_id}:${sections[0].start_offset}`,
    end_offset: sections.at(-1)!.end_offset, end_seconds: sections.at(-1)!.end_seconds,
    occurrenceNumber: index + 1, occurrenceCount: topic.occurrences.length,
  }))), [topics]);
  const [selectedTopic, setSelectedTopic] = useState("");
  const topic = occurrences.find((section) => section.id === selectedTopic) || occurrences[0];
  const followPosition = useCallback((seconds: number) => {
    // Untimed topics remain readable without inventing a seek position.
    const chronological = occurrences.filter((section) => section.start_seconds !== undefined)
      .sort((a, b) => a.start_offset - b.start_offset);
    for (let index = chronological.length - 1; index >= 0; index--) {
      if (chronological[index].start_seconds! <= seconds) { setSelectedTopic(chronological[index].id); break; }
    }
  }, [occurrences]);
  function selectTopic(id: string) {
    const section = occurrences.find((section) => section.id === id);
    if (!section) return;
    setSelectedTopic(section.id);
    playback.seekTo(section.start_seconds);
  }
  const base = `/v1/recordings/${encodeURIComponent(recording.id)}`;
  const text = Array.from(recording.text || "");
  return <div className="merged-recording">
    {recording.audio_available ? <RecordingAudioPlayer
      src={profileResourceUrl(`${base}/audio`)} waveformSrc={profileResourceUrl(`${base}/waveform`)}
      label={recording.title || t("Recording")} durationHint={recording.duration_seconds}
      bookmarks={occurrences.flatMap((section) => section.start_seconds === undefined ? [] : [{
        label: section.title + (section.occurrenceCount > 1 ? ` · ${t("Recording")} ${section.occurrenceNumber}` : "")
          + (section.start_estimated ? ` ${t("· estimated times")}` : ""),
        seconds: section.start_seconds, onSelect: () => selectTopic(section.id),
      }])}
      audioRef={playback.audioRef} onPositionChange={followPosition} onReady={playback.onReady} /> : null}
    {!recording.audio_available && recording.state === "done" ? <p className="caption">
      {recording.audio_availability === "missing"
        ? t("The archive expects audio, but the file is missing.")
        : t("Audio was removed by your retention policy. The transcript remains available.")}
    </p> : null}
    {topic ? <div className="recording-sections">
      <h3>{t("Topics")}</h3>
      <RecordingTopicList topics={topics.map((group) => ({ key: group.key, title: group.title,
        occurrences: group.occurrences.map(([section]) => ({ id: `${section.chapter_id}:${section.start_offset}`,
          label: section.start_seconds === undefined ? "—" : playbackTime(section.start_seconds), estimated: section.start_estimated })) }))}
        selected={topic.id} onSelect={selectTopic} />
      <p className="transcript-text" aria-live="polite">{text.slice(topic.start_offset, topic.end_offset).join("")}</p>
    </div> : null}
    {recording.text !== null ? recording.sections.length ? <details className="recording-disclosure">
      <summary>{t("Full transcript")}</summary>
      <p className="transcript-text">{recording.text}</p>
    </details> : <p className="transcript-text">{recording.text}</p>
      : <p className="caption" role="status">{recording.state === "expired"
        ? t("Transcript unavailable after retention.") : t("This recording is waiting for transcription.")}</p>}
    {recording.audio_available ? <a className="text-link caption" download
      href={profileResourceUrl(`${base}/audio`)}>{t("Download archived audio")}</a> : null}
  </div>;
}
