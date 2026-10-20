import { useCallback, useRef, useState } from "react";
import { audioUrl } from "./api";
import { timeLabel } from "./format";
import { t } from "./i18n";
import { SourceList } from "./SourceList";
import { TranscriptPassage } from "./TranscriptTranslation";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";
import type { Source } from "./types";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { groupTopicRuns } from "./topicOccurrences";

export type RecordingChapter = {
  id: string; title: string; started_at: string;
  parts: {
    source_id: string; source_index: number; transcript_version: string;
    start_offset: number; end_offset: number; start_seconds?: number; end_seconds?: number; chapter_id?: string;
  }[];
};

export function RecordingChapters({ chapters, timezone, onCorrect }: {
  chapters: RecordingChapter[]; timezone: string; onCorrect: () => void;
}) {
  const [activeChapter, setActiveChapter] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  if (!chapters.length) return null;
  const topics = groupTopicRuns(chapters, (chapter) => chapter.title, (left, right) => {
    const last = left.parts.at(-1), first = right.parts[0];
    return !!last && !!first && (last.source_id === first.source_id
      ? last.end_offset === first.start_offset : first.source_index === last.source_index + 1);
  });
  return <div className="recording-sections recording-chapters">
    <h3>{t("Topics")}</h3>
    {topics.map((topic) => {
      const index = Math.min(selected, topic.occurrences.length - 1);
      const members = topic.occurrences[index];
      const chapter = { ...members[0], parts: members.flatMap((member) => member.parts.map((part) => ({ ...part, chapter_id: member.id }))) };
      return <details key={topic.key} open={activeChapter === topic.key}
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        const open = event.currentTarget.open;
        if (open) setSelected(0);
        setActiveChapter((current) => open ? topic.key : current === topic.key ? null : current);
      }}>
      <summary><span>{topic.title}<small className="recording-topic-count caption">{topic.occurrences.length} {topic.occurrences.length === 1 ? t("recording") : t("recordings")}</small></span></summary>
      {activeChapter === topic.key ? <>
        {topic.occurrences.length > 1 ? <div className="recording-topic-occurrences" role="group" aria-label={`${topic.title} · ${t("Recordings")}`}>
          {topic.occurrences.map(([first], occurrenceIndex) => <button type="button" key={`${first.id}:${occurrenceIndex}`}
            aria-pressed={index === occurrenceIndex} onClick={() => setSelected(occurrenceIndex)}>
            {timeLabel(first.started_at, timezone)}
          </button>)}
        </div> : null}
        <RecordingChapterContent key={`${topic.key}:${index}`} chapter={chapter} timezone={timezone} onCorrect={onCorrect} />
      </> : null}
    </details>;
    })}
  </div>;
}

export function RecordingChapterContent({ chapter, timezone, onCorrect }: {
  chapter: RecordingChapter; timezone: string; onCorrect: () => void;
}) {
  const [selected, setSelected] = useState(0);
  const [speed, setSpeed] = useState(1);
  const audio = useRef<HTMLAudioElement | null>(null);
  const bindAudio = useCallback((element: HTMLAudioElement | null) => { audio.current = element; }, []);
  const index = Math.min(selected, chapter.parts.length - 1);
  const part = chapter.parts[index];
  if (!part) return null;
  function select(index: number) {
    audio.current?.pause();
    if (audio.current) setSpeed(audio.current.playbackRate);
    setSelected(index);
  }
  return <div className="chapter-passages">
    {chapter.parts.length > 1 ? <div className="button-row" role="group" aria-label={chapter.title}>
      <button type="button" className="button small" disabled={index === 0}
        onClick={() => select(index - 1)}>{t("Previous")}</button>
      <span className="caption" aria-live="polite">{index + 1} / {chapter.parts.length}</span>
      <button type="button" className="button small" disabled={index === chapter.parts.length - 1}
        onClick={() => select(index + 1)}>{t("Next")}</button>
    </div> : null}
    <ChapterPassage key={`${part.source_id}:${part.start_offset}:${part.transcript_version}`}
      chapterID={chapter.id} part={part} timezone={timezone} onCorrect={onCorrect}
      audioRef={bindAudio} initialSpeed={speed} />
  </div>;
}

function ChapterPassage({ chapterID, part, timezone, onCorrect, audioRef, initialSpeed }: {
  chapterID: string; part: RecordingChapter["parts"][number]; timezone: string; onCorrect: () => void;
  audioRef: (element: HTMLAudioElement | null) => void;
  initialSpeed: number;
}) {
  const resource = useResource<Source>(`/v1/transcripts/${encodeURIComponent(part.source_id)}`);
  const [toolsOpen, setToolsOpen] = useState(false);
  if (resource.error) return <ErrorState message={resource.error} retry={resource.refresh} />;
  const source = resource.data;
  if (!source) return <Loading>{t("Loading recordings…")}</Loading>;
  const section = source.sections?.find((section) => (section.chapter_id ?? `${source.id}:${section.start_offset}`) === (part.chapter_id ?? chapterID) &&
    section.start_offset === part.start_offset && section.end_offset === part.end_offset);
  if (!section || source.transcript_version !== part.transcript_version)
    return <ErrorState message={t("Recorded evidence changed or expired; reload and review it")} retry={onCorrect} />;
  const audio = audioUrl(source.id) + (section.start_seconds !== undefined
    ? `#t=${section.start_seconds},${section.end_seconds}` : "");
  return <article className="chapter-passage">
    <p className="caption">{timeLabel(source.started_at, timezone)}</p>
    {source.audio_available !== false && source.audio_availability !== "missing"
      ? <RecordingAudioPlayer src={audio} label={section.title} audioRef={audioRef} initialSpeed={initialSpeed} /> : null}
    <TranscriptPassage source={source} section={section} />
    <details onToggle={(event) => {
      if (event.target === event.currentTarget) setToolsOpen(event.currentTarget.open);
    }}>
      <summary>{t("Full recording & tools")}</summary>
      {toolsOpen ? <SourceList sources={[source]} timezone={timezone} expanded onCorrect={onCorrect}
        idPrefix={`chapter-${part.source_id}-${part.start_offset}`} /> : null}
    </details>
  </article>;
}
