import { useState } from "react";
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
  if (!chapters.length) return null;
  return <div className="recording-sections recording-chapters">
    <h3>{t("Topics")}</h3>
    {chapters.map((chapter) => <Chapter key={chapter.id} chapter={chapter} timezone={timezone} onCorrect={onCorrect} />)}
  </div>;
}

function Chapter({ chapter, timezone, onCorrect }: {
  chapter: RecordingChapter; timezone: string; onCorrect: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  return <details onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{chapter.title}</summary>
    {open ? <div className="chapter-passages">
      {chapter.parts.slice(page * 10, (page + 1) * 10).map((part) => <ChapterPassage key={`${part.source_id}:${part.start_offset}`}
        chapterID={chapter.id} part={part} timezone={timezone} onCorrect={onCorrect} />)}
      {chapter.parts.length > 10 ? <div className="pagination">
        <button className="button small" disabled={page === 0} onClick={() => setPage(page - 1)}>{t("Previous recordings")}</button>
        <button className="button small" disabled={(page + 1) * 10 >= chapter.parts.length} onClick={() => setPage(page + 1)}>{t("Next recordings")}</button>
      </div> : null}
    </div> : null}
  </details>;
}

function ChapterPassage({ chapterID, part, timezone, onCorrect }: {
  chapterID: string; part: RecordingChapter["parts"][number]; timezone: string; onCorrect: () => void;
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
      ? <RecordingAudioPlayer src={audio} label={section.title} /> : null}
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
