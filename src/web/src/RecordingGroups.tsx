import { ConversationTags, type ConversationTag } from "./ConversationTags";
import { t } from "./i18n";
import { useState } from "react";
import { Icon } from "./Icon";
import { SourceList } from "./SourceList";
import { RecordingChapters, type RecordingChapter } from "./RecordingChapters";
import { ErrorState, Loading } from "./UI";
import { timeLabel } from "./format";
import type { Source, Timeline } from "./types";
import { useResource } from "./useResource";
import { MergedRecording } from "./MergedRecording";

export type RecordingGroup = {
  id: string;
  started_at: string;
  ended_at: string;
  title: string;
  recording_count: number;
  recording_id?: string;
  topic_count?: number;
  estimated_times?: boolean;
  tags?: ConversationTag[];
  sources: Source[];
};
export type RecordingGroupList = {
  items: RecordingGroup[];
  total: number;
  total_recordings: number;
};

export function RecordingGroups({ groups, day, query, zoneID, tagID, timezone, onCorrect, timeline }: {
  groups: RecordingGroup[]; day: string; query: string; zoneID?: string; tagID?: string; timezone: string; onCorrect: () => void; timeline?: Timeline;
}) {
  return <div className="source-list source-list-compact recording-groups">
    {groups.map((group) => <div className="recording-group" key={group.id}>
      {!group.recording_id && group.recording_count === 1 ? (
        <SourceList compact sources={withHints(group.sources, timeline)} timezone={timezone} onCorrect={onCorrect} />
      ) : <RecordingSession group={group} day={day} query={query} zoneID={zoneID} tagID={tagID} timezone={timezone} onCorrect={onCorrect} timeline={timeline} />}
    </div>)}
  </div>;
}

function RecordingSession({ group, day, query, zoneID, tagID, timezone, onCorrect, timeline }: {
  group: RecordingGroup; day: string; query: string; zoneID?: string; tagID?: string; timezone: string; onCorrect: () => void; timeline?: Timeline;
}) {
  const [open, setOpen] = useState(false);
  return <details className="source-row source-row-compact recording-session" onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>
      <span className="source-time"><span>{timeLabel(group.started_at, timezone)}</span><span className="source-time-end">{recordingDuration(group.started_at, group.ended_at)}</span></span>
      <span className="source-overview"><span className="source-preview">{group.title}</span>
        <span className="source-glance">{group.topic_count
          ? `${group.topic_count} ${group.topic_count === 1 ? t("topic") : t("topics")}`
          : group.recording_id ? t("Recording") : `${group.recording_count} ${t("recordings")}`}{group.estimated_times ? t("· estimated times") : ""}</span></span>
      <Icon name="chevron-right" size={15} />
    </summary>
    <div className="source-detail">
      <div className="recording-organization"><ConversationTags groupID={group.id} day={day} tags={group.tags} onChanged={onCorrect} /></div>
      {open ? group.recording_id ? <MergedRecording recordingID={group.recording_id} />
        : <SessionSources key={`${day}:${query}:${zoneID || ""}:${tagID || ""}`} group={group} day={day} query={query} zoneID={zoneID} tagID={tagID} timezone={timezone} onCorrect={onCorrect} timeline={timeline} /> : null}
    </div>
  </details>;
}

function recordingDuration(startedAt: string, endedAt: string): string {
  const seconds = Math.round((Date.parse(endedAt) - Date.parse(startedAt)) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return t("Duration unavailable");
  if (seconds < 60) return t("{value0} sec", { value0: seconds });
  const minutes = Math.round(seconds / 60);
  return minutes < 60
    ? t("{value0} min", { value0: minutes })
    : t("{value0} hr{value1}", {
      value0: Math.floor(minutes / 60),
      value1: minutes % 60 ? t(" {value0} min", { value0: minutes % 60 }) : "",
    });
}

export function SessionSources({ group, day, query, zoneID, tagID, timezone, onCorrect, timeline }: {
  group: RecordingGroup; day: string; query: string; zoneID?: string; tagID?: string; timezone: string; onCorrect: () => void; timeline?: Timeline;
}) {
  const [page, setPage] = useState(0);
  const [toolsOpen, setToolsOpen] = useState(false);
  const params = new URLSearchParams({ day, q: query, limit: "30", offset: String(page * 30) });
  if (zoneID) params.set("zone_id", zoneID);
  if (tagID) params.set("tag_id", tagID);
  const resource = useResource<{ items: Source[]; total: number; chapters?: RecordingChapter[] }>(`/v1/recording-groups/${group.id}/sources?${params}`, 15_000);
  const correct = () => { resource.refresh(); onCorrect(); };
  return <>
    <RecordingChapters chapters={resource.data?.chapters || []} timezone={timezone} onCorrect={correct} />
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.loading && !resource.data ? <Loading>{t("Loading recordings…")}</Loading> : null}
    {resource.data ? <details className="recording-disclosure recording-originals" open={toolsOpen} onToggle={(event) => {
      if (event.target === event.currentTarget) setToolsOpen(event.currentTarget.open);
    }}>
      <summary>{t("Full recording & tools")}</summary>
      {toolsOpen ? <>
        <SourceList compact sources={withHints(resource.data.items, timeline)} timezone={timezone} onCorrect={correct} />
        {resource.data.total > 30 || page > 0 ? <div className="pagination">
          <span className="caption">{t("Recordings")}{" "}{page * 30 + 1}–{page * 30 + resource.data.items.length} {t("of")}{" "}{resource.data.total}</span>
          <button className="button small" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>{t("Previous recordings")}</button>
          <button className="button small" disabled={(page + 1) * 30 >= resource.data.total} onClick={() => setPage((value) => value + 1)}>{t("Next recordings")}</button>
        </div> : null}
      </> : null}
    </details> : null}
  </>;
}

function withHints(sources: Source[], timeline?: Timeline): Source[] {
  return sources.map((source) => ({
    ...source,
    important: source.important || timeline?.markers.some((mark) =>
      source.started_at < mark.context_end && source.ended_at > mark.context_start),
    meeting_hint_ids: timeline?.meetings.filter((meeting) =>
      source.started_at < meeting.context_end && source.ended_at > meeting.context_start).map((meeting) => meeting.id) || source.meeting_hint_ids,
  }));
}
