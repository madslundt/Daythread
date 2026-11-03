import { useCallback, useMemo, useState } from "react";
import { errorMessage, request } from "./api";
import { displayLocale, t } from "./i18n";
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
  audio_version?: string;
  interruptions?: { position_seconds: number; gap_seconds: number; previous_end: string; next_start: string }[];
  text: string | null; transcript_version: string;
  recording_transcription?: { state: string; version?: string | null; text?: string | null };
  transcript_selection?: {
    state: "unselected" | "selected" | "stale";
    selection_version: number;
    result_version?: string | null;
    transcript?: { text?: string | null; version?: string | null;
      speaker_evidence?: { state: string; version?: string | null; utterances: {
        text: string; start?: number | null; end?: number | null; start_offset: number;
        end_offset: number; attribution: string; speaker_id?: string | null;
        speaker_label?: string | null; speaker_name?: string | null;
      }[] } } | null;
  };
  sections: { title: string; chapter_id: string; start_offset: number; end_offset: number;
    start_seconds?: number; end_seconds?: number; start_estimated?: boolean }[];
};

export function MergedRecording({ recordingID }: { recordingID: string }) {
  const resource = useResource<CombinedRecording>(`/v1/recordings/${encodeURIComponent(recordingID)}`, 15_000);
  return <>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {!resource.data && resource.loading ? <Loading>{t("Loading recordings…")}</Loading> : null}
    {resource.data ? <MergedRecordingContent key={recordingID} recording={resource.data}
      refresh={resource.refresh} /> : null}
  </>;
}

export function MergedRecordingContent({ recording, resourceBase, resourceQuery = "", timezone = "UTC", refresh }: {
  recording: CombinedRecording; resourceBase?: string; resourceQuery?: string; timezone?: string;
  refresh?: () => void;
}) {
  const playback = useRecordingSeek();
  const topics = useMemo(() => groupTopicRuns(recording.sections, (section) => section.title,
    (left, right) => left.end_offset === right.start_offset), [recording.sections]);
  const occurrences = useMemo(() => topics.flatMap((topic) => topic.occurrences.map((sections, index) => ({
    ...sections[0], id: `${sections[0].chapter_id}:${sections[0].start_offset}`,
    end_offset: sections.at(-1)!.end_offset, end_seconds: sections.at(-1)!.end_seconds,
    occurrenceNumber: index + 1, occurrenceCount: topic.occurrences.length,
  }))), [topics]);
  const [selectedTopic, setSelectedTopic] = useState("");
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
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
  const base = resourceBase || `/v1/recordings/${encodeURIComponent(recording.id)}`;
  const url = (suffix: string) => profileResourceUrl(`${base}/${suffix}${resourceQuery}`);
  const boundaryTime = (value: string) => new Date(value).toLocaleTimeString(displayLocale(), { timeZone: timezone, hour12: false });
  const selection = recording.transcript_selection;
  const selectedText = selection?.state === "selected" ? selection.transcript?.text : null;
  const showingSelected = typeof selectedText === "string";
  const speakerEvidence = showingSelected ? selection?.transcript?.speaker_evidence : undefined;
  const attributedRuns = useMemo(() => {
    const runs: { text: string; attribution: string; speaker_id?: string | null;
      speaker_label?: string | null; speaker_name?: string | null; start?: number | null; }[] = [];
    for (const piece of speakerEvidence?.utterances || []) {
      const previous = runs.at(-1);
      const sameOwner = previous && previous.attribution === piece.attribution
        && previous.speaker_id === piece.speaker_id && previous.speaker_name === piece.speaker_name;
      if (sameOwner) previous.text += piece.text;
      else runs.push({ text: piece.text, attribution: piece.attribution,
        speaker_id: piece.speaker_id, speaker_label: piece.speaker_label,
        speaker_name: piece.speaker_name, start: piece.start });
    }
    return runs;
  }, [speakerEvidence]);
  const displayedText = showingSelected ? selectedText : recording.text;
  const text = Array.from(displayedText || "");
  async function chooseTranscript(resultVersion: string | null) {
    if (!selection || selectionBusy) return;
    setSelectionBusy(true); setSelectionError(null);
    try {
      await request(`/v1/recordings/${encodeURIComponent(recording.id)}/transcript-selection`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: selection.selection_version, result_version: resultVersion }),
      });
      refresh?.();
    } catch (error) { setSelectionError(errorMessage(error)); }
    finally { setSelectionBusy(false); }
  }
  return <div className="merged-recording">
    {recording.audio_available ? <RecordingAudioPlayer
      src={url("audio")} waveformSrc={url("waveform")}
      label={recording.title || t("Recording")} durationHint={recording.duration_seconds}
      bookmarks={occurrences.flatMap((section) => section.start_seconds === undefined ? [] : [{
        label: section.title + (section.occurrenceCount > 1 ? ` · ${t("Recording")} ${section.occurrenceNumber}` : "")
          + (section.start_estimated ? ` ${t("· estimated times")}` : ""),
        seconds: section.start_seconds, onSelect: () => selectTopic(section.id),
      }])}
      audioRef={playback.audioRef} onPositionChange={followPosition} onReady={playback.onReady} /> : null}
    {recording.interruptions?.length ? <details className="recording-disclosure">
      <summary>{t("Playback joins available audio; gaps and overlaps are marked here.")}</summary>
      <ul>{recording.interruptions.map((boundary, index) => <li key={index}>
        <button className="text-link" onClick={() => playback.seekTo(boundary.position_seconds)}>
          {playbackTime(boundary.position_seconds)}
        </button>{" · "}{boundaryTime(boundary.previous_end)} → {boundaryTime(boundary.next_start)}
      </li>)}</ul>
    </details> : null}
    {!recording.audio_available && recording.state === "done" ? <p className="caption">
      {recording.audio_availability === "transcript_only"
        ? t("Imported transcript only; original audio is unavailable.")
        : recording.audio_availability === "missing"
        ? t("The archive expects audio, but the file is missing.")
        : t("Audio was removed by your retention policy. The transcript remains available.")}
    </p> : null}
    {showingSelected ? <div className="caption recording-transcript-choice">
      <span>{t("Using selected full-recording transcript")}</span>
      <button className="text-button" disabled={selectionBusy}
        onClick={() => void chooseTranscript(null)}>{t("Use original part transcripts")}</button>
    </div> : recording.recording_transcription?.state === "ready"
      && recording.recording_transcription.version ? <div className="caption recording-transcript-choice">
        <span>{t("A full-recording transcript is ready")}</span>
        <button className="text-button" disabled={selectionBusy}
          onClick={() => void chooseTranscript(recording.recording_transcription!.version!)}>
          {t("Use full-recording transcript")}
        </button>
      </div> : null}
    {selection?.state === "stale" ? <p className="caption" role="status">
      {t("The selected transcript is out of date. Showing the original part transcripts.")}
    </p> : null}
    {selectionError ? <p className="caption" role="alert">{selectionError}</p> : null}
    {topic && !showingSelected ? <div className="recording-sections">
      <h3>{t("Topics")}</h3>
      <RecordingTopicList topics={topics.map((group) => ({ key: group.key, title: group.title,
        occurrences: group.occurrences.map(([section]) => ({ id: `${section.chapter_id}:${section.start_offset}`,
          label: section.start_seconds === undefined ? "—" : playbackTime(section.start_seconds), estimated: section.start_estimated })) }))}
        selected={topic.id} onSelect={selectTopic} />
      <p className="transcript-text" aria-live="polite">{text.slice(topic.start_offset, topic.end_offset).join("")}</p>
    </div> : null}
    {displayedText !== null ? showingSelected ? <>
      {speakerEvidence?.state === "ready" && attributedRuns.length ? <div className="transcript-text recording-attributed-transcript">
        {attributedRuns.map((run, index) => {
          const hasSpokenText = /[\p{L}\p{N}]/u.test(run.text);
          return <span className="recording-attributed-run" key={`${index}:${run.attribution}:${run.speaker_id || "unknown"}`}>
            {hasSpokenText ? <span className="caption recording-speaker-label">{run.attribution === "attributed"
              ? run.speaker_name || run.speaker_label || t("Unknown speaker")
              : run.attribution === "overlap" ? t("Overlapping speech") : t("Unknown speaker")}</span> : null}
            {hasSpokenText && run.start !== null && run.start !== undefined
              ? <button type="button" className="caption recording-speaker-time text-link"
                aria-label={`${t("Playback position")} ${playbackTime(run.start)}`}
                disabled={!recording.audio_available}
                onClick={() => playback.seekTo(run.start ?? undefined)}>{playbackTime(run.start)}</button> : null}
            <span>{run.text}</span>
          </span>;
        })}
      </div> : <p className="transcript-text">{displayedText}</p>}
      {recording.text !== null ? <details className="recording-disclosure">
        <summary>{t("Original part transcripts")}</summary>
        <p className="transcript-text">{recording.text}</p>
      </details> : null}
    </> : recording.sections.length ? <details className="recording-disclosure">
      <summary>{t("Full transcript")}</summary>
      <p className="transcript-text">{recording.text}</p>
    </details> : <p className="transcript-text">{recording.text}</p>
      : <p className="caption" role="status">{recording.state === "expired"
        ? t("Transcript unavailable after retention.") : t("This recording is waiting for transcription.")}</p>}
    {recording.text !== null && recording.state === "processing"
      ? <p className="caption" role="status">{t("Processing audio")}</p> : null}
    {recording.audio_available ? <a className="text-link caption" download
      href={url("audio")}>{t("Download archived audio")}</a> : null}
  </div>;
}
