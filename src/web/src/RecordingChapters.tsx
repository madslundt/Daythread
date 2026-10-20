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

export type RecordingChapter = {
  id: string; title: string; started_at: string;
  parts: {
    source_id: string; source_index: number; transcript_version: string;
    start_offset: number; end_offset: number; start_seconds?: number; end_seconds?: number;
  }[];
};

export function RecordingChapters({ chapters, timezone, onCorrect }: {
  chapters: RecordingChapter[]; timezone: string; onCorrect: () => void;
}) {
  const [activeChapter, setActiveChapter] = useState<string | null>(null);
  if (!chapters.length) return null;
  return <div className="recording-sections recording-chapters">
    <h3>{t("Topics")}</h3>
    {chapters.map((chapter) => <details key={chapter.id} open={activeChapter === chapter.id}
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        const open = event.currentTarget.open;
        setActiveChapter((current) => open ? chapter.id : current === chapter.id ? null : current);
      }}>
      <summary>{chapter.title}</summary>
      {activeChapter === chapter.id ? <RecordingChapterContent chapter={chapter} timezone={timezone} onCorrect={onCorrect} /> : null}
    </details>)}
  </div>;
}

export function RecordingChapterContent({ chapter, timezone, onCorrect }: {
  chapter: RecordingChapter; timezone: string; onCorrect: () => void;
}) {
  const [selected, setSelected] = useState(0);
  const [autoPlay, setAutoPlay] = useState(false);
  const [speed, setSpeed] = useState(1);
  const audio = useRef<HTMLAudioElement | null>(null);
  const bindAudio = useCallback((element: HTMLAudioElement | null) => { audio.current = element; }, []);
  const index = Math.min(selected, chapter.parts.length - 1);
  const part = chapter.parts[index];
  if (!part) return null;
  function select(index: number) {
    setAutoPlay(Boolean(audio.current && !audio.current.paused));
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
      autoPlay={autoPlay} audioRef={bindAudio} initialSpeed={speed} />
  </div>;
}

function ChapterPassage({ chapterID, part, timezone, onCorrect, autoPlay, audioRef, initialSpeed }: {
  chapterID: string; part: RecordingChapter["parts"][number]; timezone: string; onCorrect: () => void;
  autoPlay: boolean; audioRef: (element: HTMLAudioElement | null) => void;
  initialSpeed: number;
}) {
  const resource = useResource<Source>(`/v1/transcripts/${encodeURIComponent(part.source_id)}`);
  const [toolsOpen, setToolsOpen] = useState(false);
  if (resource.error) return <ErrorState message={resource.error} retry={resource.refresh} />;
  const source = resource.data;
  if (!source) return <Loading>{t("Loading recordings…")}</Loading>;
  const section = source.sections?.find((section) => (section.chapter_id ?? `${source.id}:${section.start_offset}`) === chapterID &&
    section.start_offset === part.start_offset && section.end_offset === part.end_offset);
  if (!section || source.transcript_version !== part.transcript_version)
    return <ErrorState message={t("Recorded evidence changed or expired; reload and review it")} retry={onCorrect} />;
  const audio = audioUrl(source.id) + (section.start_seconds !== undefined
    ? `#t=${section.start_seconds},${section.end_seconds}` : "");
  return <article className="chapter-passage">
    <p className="caption">{timeLabel(source.started_at, timezone)}</p>
    {source.audio_available !== false && source.audio_availability !== "missing"
      ? <RecordingAudioPlayer src={audio} label={section.title} autoPlay={autoPlay} audioRef={audioRef} initialSpeed={initialSpeed} /> : null}
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
