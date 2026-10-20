import { useCallback, useRef, useState } from "react";
import { t } from "./i18n";
import { profileResourceUrl } from "./profiles";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";

export type CombinedRecording = {
  id: string; title: string; started_at: string; ended_at: string;
  state: string; duration_seconds: number; audio_available: boolean;
  audio_availability?: string;
  text: string | null; transcript_version: string;
  sections: { title: string; chapter_id: string; start_offset: number; end_offset: number;
    start_seconds?: number; end_seconds?: number }[];
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
  const audio = useRef<HTMLAudioElement | null>(null);
  const bindAudio = useCallback((element: HTMLAudioElement | null) => { audio.current = element; }, []);
  const pendingSeek = useRef<number | null>(null);
  const seekWhenReady = useCallback(() => {
    if (audio.current && pendingSeek.current !== null) {
      audio.current.currentTime = pendingSeek.current;
      pendingSeek.current = null;
    }
  }, []);
  const [selectedTopic, setSelectedTopic] = useState("");
  const topicIndex = Math.max(0, recording.sections.findIndex((section) => section.chapter_id === selectedTopic));
  const topic = recording.sections[topicIndex];
  const followPosition = useCallback((seconds: number) => {
    // Untimed topics remain readable without inventing a seek position.
    if (!recording.sections.every((section) => section.start_seconds !== undefined)) return;
    for (let index = recording.sections.length - 1; index >= 0; index--) {
      if (recording.sections[index].start_seconds! <= seconds) {
        setSelectedTopic(recording.sections[index].chapter_id);
        break;
      }
    }
  }, [recording.sections]);
  function selectTopic(index: number) {
    const section = recording.sections[index];
    if (!section) return;
    setSelectedTopic(section.chapter_id);
    pendingSeek.current = section.start_seconds ?? null;
    if (audio.current && audio.current.readyState >= 1) seekWhenReady();
  }
  const base = `/v1/recordings/${encodeURIComponent(recording.id)}`;
  const text = Array.from(recording.text || "");
  return <div className="merged-recording">
    {recording.audio_available ? <RecordingAudioPlayer
      src={profileResourceUrl(`${base}/audio`)} waveformSrc={profileResourceUrl(`${base}/waveform`)}
      label={recording.title || t("Recording")} durationHint={recording.duration_seconds}
      audioRef={bindAudio} onPositionChange={followPosition} onReady={seekWhenReady} /> : null}
    {!recording.audio_available && recording.state === "done" ? <p className="caption">
      {recording.audio_availability === "missing"
        ? t("The archive expects audio, but the file is missing.")
        : t("Audio was removed by your retention policy. The transcript remains available.")}
    </p> : null}
    {topic ? <div className="recording-sections">
      <h3>{t("Topics")}</h3>
      <div className="recording-topic-navigation" role="group" aria-label={t("Topics")}>
        <button type="button" className="button small" disabled={topicIndex === 0}
          onClick={() => selectTopic(topicIndex - 1)}>{t("Previous")}</button>
        <select aria-label={t("Topics")} value={topic.chapter_id}
          onChange={(event) => selectTopic(recording.sections.findIndex((section) => section.chapter_id === event.target.value))}>
          {recording.sections.map((section) => <option key={section.chapter_id} value={section.chapter_id}>{section.title}</option>)}
        </select>
        <button type="button" className="button small" disabled={topicIndex === recording.sections.length - 1}
          onClick={() => selectTopic(topicIndex + 1)}>{t("Next")}</button>
      </div>
      <p className="caption" aria-live="polite">{topicIndex + 1} / {recording.sections.length}</p>
      <p className="transcript-text">{text.slice(topic.start_offset, topic.end_offset).join("")}</p>
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
