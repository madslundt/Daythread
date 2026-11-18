import { t } from "./i18n";
import { useState } from "react";
import { TranscriptPassage } from "./TranscriptTranslation";
import { audioUrl } from "./api";
import type { Source, TranscriptTurn } from "./types";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { groupTopicRuns } from "./topicOccurrences";
import { RecordingTopicList } from "./RecordingTopicList";
import { playbackTime } from "./recordingWaveform";
import { TopicReading } from "./TopicReading";
import { useSourceTurns, useSpeakerFilters, useSpeakerReading, type SpeakerReading } from "./RecordingTranscript";

type SpeakerProps = { reading?: SpeakerReading; fullTurns?: TranscriptTurn[]; showingTranscript?: boolean };

export function RecordingSections({ source, compact = false, onSeek, onPlay, selectedOffset, onSelectOffset, ...speakerProps }: {
  source: Source; compact?: boolean; onSeek?: (seconds?: number) => void;
  onPlay?: (seconds: number) => void;
  selectedOffset?: number | null; onSelectOffset?: (offset: number | null) => void;
} & SpeakerProps) {
  if (!source.sections?.length || !source.text) return null;
  if (compact) return <CompactTopics source={source} onSeek={onSeek} onPlay={onPlay} selectedOffset={selectedOffset} onSelectOffset={onSelectOffset} {...speakerProps} />;
  return <div className="recording-sections">
    <h3>{source.sections.length === 1 ? t("Topic") : t("{value0} topic sections", { value0: source.sections.length })}</h3>
    {source.sections.map((section) => <RecordingSection key={section.start_offset}
      source={source} section={section} onSeek={onSeek} {...speakerProps} />)}
  </div>;
}

function CompactTopics({ source, onSeek, onPlay, selectedOffset, onSelectOffset, ...speakerProps }: {
  source: Source; onSeek?: (seconds?: number) => void; selectedOffset?: number | null; onSelectOffset?: (offset: number | null) => void;
  onPlay?: (seconds: number) => void;
} & SpeakerProps) {
  const [selected, setSelected] = useState<number>();
  const topics = groupTopicRuns(source.sections || [], (section) => section.title,
    (left, right) => left.end_offset === right.start_offset);
  const spans = topics.flatMap((topic) => topic.occurrences.map((sections) => ({ ...sections[0],
    end_offset: sections.at(-1)!.end_offset, end_seconds: sections.at(-1)!.end_seconds,
    summary: [...new Set(sections.map((section) => section.summary?.trim()).filter(Boolean))].join("\n\n") })));
  const active = spans.find((span) => span.start_offset === (onSelectOffset ? selectedOffset : selected));
  const canPlay = source.audio_available !== false && source.audio_availability !== "missing";
  return <div className="recording-sections">
    <h3>{t("Topics")}</h3>
    <RecordingTopicList topics={topics.map((topic) => ({ key: topic.key, title: topic.title,
      occurrences: topic.occurrences.map(([section]) => ({ id: String(section.start_offset),
         label: section.start_seconds === undefined ? "—" : playbackTime(section.start_seconds),
         play: canPlay && onPlay && section.start_seconds != null ? () => onPlay(section.start_seconds!) : undefined })) }))}
      selected={active ? String(active.start_offset) : undefined} onSelect={(id) => {
        const span = spans.find((span) => String(span.start_offset) === id);
        setSelected(span?.start_offset); onSelectOffset?.(span?.start_offset ?? null);
       }} renderTopic={(id) => active ? <RecordingTopicReader key={id} source={source} section={active} onSeek={onSeek} hasPlayBookmark={!!onPlay} {...speakerProps} /> : null} />
  </div>;
}

function RecordingSection({ source, section, onSeek, ...speakerProps }: {
  source: Source; section: NonNullable<Source["sections"]>[number];
  onSeek?: (seconds?: number) => void;
} & SpeakerProps) {
  const [open, setOpen] = useState(false);
  return <details onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{section.title}</summary>
    {open ? <RecordingTopicReader source={source} section={section} onSeek={onSeek} {...speakerProps} /> : null}
  </details>;
}

function RecordingTopicReader({ source, section, onSeek, hasPlayBookmark, showingTranscript, ...speakerProps }: {
  source: Source; section: NonNullable<Source["sections"]>[number]; onSeek?: (seconds: number) => void;
  hasPlayBookmark?: boolean;
} & SpeakerProps) {
  const canPlay = source.audio_available !== false && source.audio_availability !== "missing";
  const filters = useSpeakerFilters();
  const turns = useSourceTurns(source, !speakerProps.reading);
  const local = useSpeakerReading(speakerProps.reading ? undefined : turns, source.id, filters, canPlay);
  const reading = speakerProps.reading ?? local;
  const summary = section.summary?.trim() ? <p className="transcript-text">{section.summary}</p> : null;
  const transcript = () => <TranscriptPassage source={source} section={section} onSeek={canPlay ? onSeek : undefined}
    reading={reading} fullTurns={speakerProps.fullTurns ?? turns} />;
  return <>
    {showingTranscript === undefined ? <TopicReading summary={summary} transcript={(visible) => visible ? transcript() : null} />
      : showingTranscript ? transcript() : summary || <p className="caption">{t("No topic summary yet")}</p>}
    {!hasPlayBookmark && canPlay && section.start_seconds !== undefined ? onSeek
      ? <button type="button" className="text-button" onClick={() => onSeek(section.start_seconds!)}>{t("Listen from this topic")}</button>
      : <RecordingAudioPlayer label={section.title} skipIntervals={reading.intervals}
        src={`${audioUrl(source.id)}#t=${section.start_seconds},${section.end_seconds}`} /> : null}
  </>;
}
