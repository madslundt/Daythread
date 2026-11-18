import { useMemo, useState } from "react";
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
import { RecordingTranscript, TranscriptTurns, turnsInRange, useSpeakerFilters, useSpeakerReading } from "./RecordingTranscript";
import type { TranscriptTurn } from "./types";
import { TopicReading } from "./TopicReading";

export type CombinedRecording = {
  id: string; title: string; started_at: string; ended_at: string;
  state: string; duration_seconds: number; audio_available: boolean;
  audio_availability?: string;
  mixed_audio?: boolean;
  audio_version?: string;
  interruptions?: { position_seconds: number; gap_seconds: number; previous_end: string; next_start: string }[];
  text: string | null; transcript_version: string;
  recording_summary?: string | null;
  transcript_turns?: TranscriptTurn[];
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
    start_seconds?: number; end_seconds?: number; start_estimated?: boolean; summary?: string }[];
};

export function MergedRecording({ recordingID, timezone, archive }: { recordingID: string; timezone?: string; archive?: boolean }) {
  const resource = useResource<CombinedRecording>(`/v1/recordings/${encodeURIComponent(recordingID)}${archive ? "?archive=true" : ""}`, 15_000);
  return <>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {!resource.data && resource.loading ? <Loading>{t("Loading recordings…")}</Loading> : null}
    {resource.data ? <MergedRecordingContent key={recordingID} recording={resource.data}
      refresh={resource.refresh} timezone={timezone} /> : null}
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
    summary: [...new Set(sections.map((section) => section.summary?.trim()).filter(Boolean))].join("\n\n"),
    occurrenceNumber: index + 1, occurrenceCount: topic.occurrences.length,
  }))), [topics]);
  const [selectedTopic, setSelectedTopic] = useState<string>();
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const topic = occurrences.find((section) => section.id === selectedTopic);
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
  const speakerRuns = useMemo(
    () => compactSpeakerEvidence(speakerEvidence?.utterances || []), [speakerEvidence],
  );
  const hasSpeakerEvidence = speakerEvidence?.state === "ready"
    && exactSpeakerEvidence(speakerEvidence.utterances, selectedText ?? "");
  const filters = useSpeakerFilters();
  const originalScope = `original:${recording.id}:${recording.transcript_version}`;
  const selectedScope = `selected:${recording.id}:${selection?.result_version ?? selection?.transcript?.version ?? ""}`;
  const skipSupported = recording.audio_available && !recording.mixed_audio && recording.audio_availability !== "partial";
  const originalReading = useSpeakerReading(recording.transcript_turns, originalScope, filters, recording.audio_available, skipSupported);
  const selectedReading = useSpeakerReading(hasSpeakerEvidence ? speakerEvidence?.utterances : undefined,
    selectedScope, filters, recording.audio_available, skipSupported);
  const activeReading = showingSelected && filters.selection?.scope === selectedScope ? selectedReading : originalReading;
  const displayedText = showingSelected ? selectedText : recording.text;
  const originalText = Array.from(recording.text || "");
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
      bookmarks={occurrences.length < 2 ? [] : occurrences.flatMap((section) => section.start_seconds === undefined ? [] : [{
        label: section.title + (section.occurrenceCount > 1 ? ` · ${t("Recording")} ${section.occurrenceNumber}` : "")
          + (section.start_estimated ? ` ${t("· estimated times")}` : ""),
        seconds: section.start_seconds, onSelect: () => selectTopic(section.id),
      }])}
      audioRef={playback.audioRef} onReady={playback.onReady} skipIntervals={activeReading.intervals} /> : null}
    {recording.interruptions?.length ? <details className="recording-disclosure">
      <summary>{t("Playback joins available audio; gaps and overlaps are marked here.")}</summary>
      <ul>{recording.interruptions.map((boundary, index) => <li key={index}>
        <button className="text-link" onClick={() => playback.seekTo(boundary.position_seconds)}>
          {playbackTime(boundary.position_seconds)}
        </button>{" · "}{boundaryTime(boundary.previous_end)} → {boundaryTime(boundary.next_start)}
      </li>)}</ul>
    </details> : null}
    {recording.audio_availability === "partial" ? <p className="caption">{t("Recording audio is incomplete")}</p> : null}
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
    {recording.recording_summary ? <section className="recording-overview">
      <h3>{t("Recording summary")}</h3><p className="transcript-text">{recording.recording_summary}</p>
      {showingSelected ? <p className="caption">{t("Based on the original part transcripts.")}</p> : null}
    </section> : null}
    {occurrences.length ? <div className="recording-sections">
      <h3>{showingSelected ? t("Chapters in the original transcript") : t("Chapters")}</h3>
      <RecordingTopicList topics={topics.map((group) => ({ key: group.key, title: group.title,
        occurrences: group.occurrences.map(([section]) => ({ id: `${section.chapter_id}:${section.start_offset}`,
          label: section.start_seconds === undefined ? "—" : playbackTime(section.start_seconds), estimated: section.start_estimated })) }))}
        selected={topic?.id} onSelect={setSelectedTopic} renderTopic={(id) => topic ? <div key={id}>
          <TopicReading summary={topic.summary ? <p className="transcript-text">{topic.summary}</p> : null}
            transcript={(visible) => visible ? <TranscriptTurns text={originalText.slice(topic.start_offset, topic.end_offset).join("")}
              turns={turnsInRange(recording.text || "", recording.transcript_turns, topic)} reading={originalReading}
              onSeek={recording.audio_available ? playback.seekTo : undefined} /> : null} />
          {recording.audio_available && topic.start_seconds !== undefined ? <button type="button" className="text-button"
            onClick={() => playback.seekTo(topic.start_seconds)}>{t("Listen from this topic")}</button> : null}
        </div> : null} />
    </div> : null}
    {displayedText !== null ? <RecordingTranscript>
      <TranscriptTurns text={displayedText} turns={showingSelected ? hasSpeakerEvidence ? speakerRuns : undefined : recording.transcript_turns}
        reading={showingSelected ? selectedReading : originalReading}
        onSeek={recording.audio_available ? playback.seekTo : undefined} />
      {showingSelected && recording.text !== null ? <RecordingTranscript label={t("Original part transcripts")}>
        <TranscriptTurns text={recording.text} turns={recording.transcript_turns} reading={originalReading}
          onSeek={recording.audio_available ? playback.seekTo : undefined} />
      </RecordingTranscript> : null}
    </RecordingTranscript> : <p className="caption" role="status">{recording.state === "expired"
        ? t("Transcript unavailable after retention.") : t("This recording is waiting for transcription.")}</p>}
    {recording.text !== null && recording.state === "processing"
      ? <p className="caption" role="status">{t("Processing audio")}</p> : null}
    {recording.text !== null && recording.state === "expired"
      ? <p className="caption" role="status">{t("Some transcript parts are unavailable after retention.")}</p> : null}
    {recording.audio_available ? <a className="text-link caption" download
      href={url("audio")}>{t("Download archived audio")}</a> : null}
  </div>;
}

type SpeakerEvidencePiece = {
  text: string; start?: number | null; end?: number | null; start_offset: number;
  end_offset: number; attribution: string; speaker_id?: string | null;
  speaker_label?: string | null; speaker_name?: string | null;
};

function exactSpeakerEvidence(pieces: SpeakerEvidencePiece[], text: string) {
  const original = Array.from(text);
  let cursor = 0;
  for (const piece of pieces) {
    if (piece.start_offset !== cursor || piece.end_offset <= cursor || piece.end_offset > original.length
      || original.slice(cursor, piece.end_offset).join("") !== piece.text) return false;
    cursor = piece.end_offset;
  }
  return pieces.length > 0 && cursor === original.length;
}

export function compactSpeakerEvidence(pieces: SpeakerEvidencePiece[]) {
  const runs: (SpeakerEvidencePiece & { text: string })[] = [];
  for (let index = 0; index < pieces.length; index += 1) {
    const piece = pieces[index];
    const previous = runs.at(-1);
    const contiguous = previous?.end_offset === piece.start_offset;
    const sameOwner = previous && previous.attribution === "attributed"
      && piece.attribution === "attributed"
      && !!previous.speaker_id && previous.speaker_id === piece.speaker_id
      && previous.speaker_name === piece.speaker_name
      && contiguous;
    if (sameOwner) {
      previous.text += piece.text;
      previous.end_offset = piece.end_offset;
      previous.end = piece.end;
      continue;
    }
    const sameUnknownRun = previous?.attribution === "unknown"
      && piece.attribution === "unknown" && contiguous;
    if (sameUnknownRun) {
      previous.text += piece.text;
      previous.end_offset = piece.end_offset;
      previous.end = piece.end;
      continue;
    }

    const next = pieces[index + 1];
    const bridgeableGap = piece.attribution === "unknown"
      && !/[\p{L}\p{N}]/u.test(piece.text)
      && next?.attribution === "attributed"
      && previous?.attribution === "attributed"
      && !!previous.speaker_id && previous.speaker_id === next.speaker_id
      && previous.speaker_name === next.speaker_name
      && previous.end_offset === piece.start_offset
      && piece.end_offset === next.start_offset;
    if (bridgeableGap && next) {
      previous.text += piece.text + next.text;
      previous.end_offset = next.end_offset;
      previous.end = next.end;
      index += 1;
      continue;
    }
    runs.push({ ...piece, text: piece.text });
  }
  return runs;
}
