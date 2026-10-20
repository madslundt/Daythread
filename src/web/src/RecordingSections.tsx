import { t } from "./i18n";
import { useState } from "react";
import { TranscriptPassage } from "./TranscriptTranslation";
import { Icon } from "./Icon";
import { audioUrl } from "./api";
import type { Source } from "./types";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { groupTopicRuns } from "./topicOccurrences";
import { RecordingTopicList } from "./RecordingTopicList";
import { playbackTime } from "./recordingWaveform";

export function RecordingSections({ source, compact = false, onSeek, selectedOffset, onSelectOffset }: {
  source: Source; compact?: boolean; onSeek?: (seconds?: number) => void;
  selectedOffset?: number; onSelectOffset?: (offset: number) => void;
}) {
  if (!source.sections?.length || !source.text) return null;
  if (compact) return <CompactTopics source={source} onSeek={onSeek} selectedOffset={selectedOffset} onSelectOffset={onSelectOffset} />;
  return <div className="recording-sections">
    <h3>{compact ? t("Topics") : source.sections.length === 1 ? t("Topic") : t("{value0} topic sections", { value0: source.sections.length })}</h3>
    {source.sections.map((section, index) => <RecordingSection key={section.start_offset}
      source={source} section={section} index={index} compact={compact} onSeek={onSeek} />)}
  </div>;
}

function CompactTopics({ source, onSeek, selectedOffset, onSelectOffset }: {
  source: Source; onSeek?: (seconds?: number) => void; selectedOffset?: number; onSelectOffset?: (offset: number) => void;
}) {
  const [selected, setSelected] = useState<number>();
  const topics = groupTopicRuns(source.sections || [], (section) => section.title,
    (left, right) => left.end_offset === right.start_offset);
  const spans = topics.flatMap((topic) => topic.occurrences.map((sections) => ({ ...sections[0],
    end_offset: sections.at(-1)!.end_offset, end_seconds: sections.at(-1)!.end_seconds })));
  const active = spans.find((span) => span.start_offset === (selectedOffset ?? selected)) || spans[0];
  return <div className="recording-sections">
    <h3>{t("Topics")}</h3>
    <RecordingTopicList topics={topics.map((topic) => ({ key: topic.key, title: topic.title,
      occurrences: topic.occurrences.map(([section]) => ({ id: String(section.start_offset),
        label: section.start_seconds === undefined ? "—" : playbackTime(section.start_seconds) })) }))}
      selected={active ? String(active.start_offset) : undefined} onSelect={(id) => {
        const span = spans.find((span) => String(span.start_offset) === id);
        if (!span) return;
        setSelected(span.start_offset); onSelectOffset?.(span.start_offset); onSeek?.(span.start_seconds ?? 0);
      }} />
    {active ? <TranscriptPassage source={source} section={active} /> : null}
  </div>;
}

function RecordingSection({ source, section, index, compact, onSeek }: {
  source: Source; section: NonNullable<Source["sections"]>[number]; index: number; compact: boolean;
  onSeek?: (seconds?: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return <details onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary onClick={() => onSeek?.(section.start_seconds ?? 0)}>{compact ? <><span>{section.title}</span><Icon name="chevron-right" size={14} /></> : <><span className="caption">{index + 1}.</span> {section.title}</>}</summary>
    {open && !onSeek && section.start_seconds !== undefined && source.audio_available !== false && source.audio_availability !== "missing"
      ? <RecordingAudioPlayer label={section.title}
        src={`${audioUrl(source.id)}#t=${section.start_seconds},${section.end_seconds}`} /> : null}
    {open ? <TranscriptPassage source={source} section={section} /> : <p className="transcript-text">
      {Array.from(source.text || "").slice(section.start_offset, section.end_offset).join("")}
    </p>}
  </details>;
}
