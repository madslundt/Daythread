import { TagFilter } from "./ConversationTags";
import { ZoneFilter } from "./LocationZones";
import { t } from "./i18n";
import { profileResourceUrl } from "./profiles";
import { ImportanceButton } from "./Importance";
import { useEffect, useState, type ReactNode } from "react";
import { dateTimeLabel, localDay, timeLabel } from "./format";
import { CaptureStatus, RecordingActivity } from "./CaptureStatus";
export { CaptureStatus } from "./CaptureStatus";
import { Icon } from "./Icon";
import {
  CompletenessDetails,
  EvidenceFeedback,
  FeedbackReview,
} from "./CompletenessFeedback";
import { InboxEntry } from "./Inbox";
import { Decisions } from "./Decisions";
import { SourceList } from "./SourceList";
import { RecordingGroups, type RecordingGroupList } from "./RecordingGroups";
import { Speakers } from "./Speakers";
import { RecapContent } from "./RecapContent";
import { SummaryTranslation } from "./SummaryTranslation";
import type { ChatScope, Recap, Source, Status, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading, Notice, RichText } from "./UI";

export function RecapSection({
  day,
  timezone,
  status,
  statusError,
  onUpload,
  onChat,
  compact = false,
}: {
  day: string;
  timezone: string;
  status?: Status;
  statusError?: string;
  onUpload?: () => void;
  onChat: (scope: ChatScope, question?: string) => void;
  compact?: boolean;
}) {
  const recap = useResource<Recap>(`/v1/recaps/${day}`, 15_000);
  const statusAt = status?.as_of ? Date.parse(status.as_of) : NaN;
  const summarizing = !statusError && Number.isFinite(statusAt) && Date.now() - statusAt <= 120_000 &&
    (status?.generation?.stages.summarizing || 0) > 0;
  const [showVersions, setShowVersions] = useState(false);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", recap.refresh);
    return () =>
      window.removeEventListener("omi:archive-changed", recap.refresh);
  }, [recap.refresh]);
  return (
    <section className="recap-section">
      <div className="section-heading">
        <div>
          <h2>{t("Day recap")}</h2>
        </div>
      </div>
      {recap.error ? <ErrorState message={recap.error} retry={recap.refresh} /> : null}
      {recap.data?.stale ? (
        <Notice>
          {t("New or changed evidence is available. This recap will update automatically.")}{" "}</Notice>
      ) : null}
      {recap.data ? <RecapCoverage recap={recap.data} timezone={timezone} /> : null}
      {!compact ? <CompletenessDetails value={recap.data?.completeness} /> : null}
      {recap.loading && !recap.data ? (
        <Loading>{t("Loading recap…")}</Loading>
      ) : recap.data?.summary ? (
        <div className="recap-content">
          <SummaryTranslation outputId={recap.data.output_id} original={recap.data.summary}>
            <RecapContent
              key={`${day}:${recap.data.output_id || recap.data.generated_at || "unversioned"}:${recap.data.summary}`}
              text={recap.data.summary}
              day={day}
              version={recap.data.output_id || recap.data.generated_at || "unversioned"}
              sources={recap.data.sources || []}
              timezone={timezone}
              onCorrect={recap.refresh}
              compact={compact}
            />
          </SummaryTranslation>
          {recap.data.pending_chunks ? (
            <p className="caption">
              {recap.data.pending_chunks} {t("recording(s) were awaiting transcription when generated. This recap updates automatically to include later evidence.")}{" "}</p>
          ) : null}
          <RecapUtilities compact={compact} generatedAt={recap.data.generated_at} timezone={timezone}>
          <EvidenceFeedback outputId={recap.data.output_id} evidence={recap.data.evidence} outputText={recap.data.summary} />
          <div className="recap-footer">
            {recap.data.generated_at ? (
              <span className="caption">
                {t("Generated at")}{" "}{timeLabel(recap.data.generated_at, timezone)}
              </span>
            ) : null}
            <a
              className="text-link caption"
              href={profileResourceUrl(`/v1/diary/${day}.md`)}
              download
            >
              <Icon name="download" size={14} />
              {t("Export Markdown")}{" "}</a>
            <button
              className="text-button"
              onClick={() => setShowVersions((value) => !value)}
            >
              {showVersions ? t("Hide saved versions") : t("Saved versions")}
            </button>
          </div>
          {showVersions ? (
            <RecapVersions day={day} timezone={timezone} />
          ) : null}
          {recap.data.sources?.length ? (
            <details className="recap-sources">
              <summary>
                {t("View")}{" "}{recap.data.sources.length} {t("source recording(s)")}{" "}</summary>
              <SourceList
                sources={recap.data.sources}
                timezone={timezone}
                onCorrect={recap.refresh}
              />
            </details>
          ) : null}
          {compact ? <CompletenessDetails value={recap.data.completeness} /> : null}
          </RecapUtilities>
        </div>
      ) : (
        <div className="recap-empty">
          <span className="empty-symbol"><Icon name="diary" size={25} /></span>
          {compact ? <div>
            <h3>{t("Your recap comes together automatically.")}</h3>
            <p>{emptyRecapMessage(recap.data, summarizing)}</p>
            {onUpload ? <button className="text-button recap-upload" onClick={onUpload}>{t("Add a recording")}</button> : null}
          </div> : <>
            <h3>{t("The story of your day starts here.")}</h3>
            <p>{emptyRecapMessage(recap.data, summarizing)}</p>
            {onUpload ? <button className="button accent recap-upload" onClick={onUpload}><Icon name="upload" size={17} />{t("Add a recording")}</button> : null}
          </>}
        </div>
      )}
      {compact && !recap.data?.summary ? <CompletenessDetails value={recap.data?.completeness} /> : null}
    </section>
  );
}

function emptyRecapMessage(recap?: Recap, summarizing = false): string {
  if (summarizing) return t("Your recap is being generated now. It will appear here when ready.");
  if (recap?.no_recap_content)
    return t("No useful recap content in transcribed recordings yet. Your recordings remain available.");
  if (recap?.waiting_for_pause)
    return t("Live conversations are waiting for a longer pause before being summarized.");
  if (recap?.deferred_sources || recap?.pending_chunks)
    return t("Some recordings are awaiting upload or transcription.");
  return t("Uploaded recordings are summarized after transcription. Live conversations wait for a longer pause.");
}

export function RecapCoverage({ recap, timezone }: { recap: Recap; timezone: string }) {
  return <div className="recap-coverage caption" role="status">
    {recap.included_through ? <p>{t("Includes transcribed recordings through")}{" "}{dateTimeLabel(recap.included_through, timezone)}.</p>
      : recap.included_through === null ? <p>{t("No transcribed recordings included yet.")}</p> : null}
    {recap.waiting_for_pause ? <p>{t("Live conversations are waiting for a longer pause before being summarized.")}</p> : null}
    {recap.pending_upload_sources || recap.pending_chunks || (!recap.waiting_for_pause && recap.deferred_sources)
      ? <p>{t("Some recordings are awaiting upload or transcription.")}</p> : null}
    {recap.boundary_note ? <p>{t(recap.boundary_note)}</p> : null}
  </div>;
}

function RecapUtilities({ compact, generatedAt, timezone, children }: { compact: boolean; generatedAt?: string; timezone: string; children: ReactNode }) {
  return compact ? <details className="recap-utilities">
    <summary><span>{t("Sources, export & history")}</span>{generatedAt ? <span>{t("Generated at")}{" "}{timeLabel(generatedAt, timezone)}</span> : null}</summary>
    {children}
  </details> : <>{children}</>;
}

function RecapVersions({ day, timezone }: { day: string; timezone: string }) {
  const resource = useResource<{
    items: { id: string; created_at: string; content: Recap }[];
  }>(`/v1/diary/${day}/versions`);
  return (
    <div className="recap-history">
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : resource.loading ? (
        <Loading>{t("Loading saved versions…")}</Loading>
      ) : resource.data?.items.length ? (
        resource.data.items.map((version) => (
          <details key={version.id}>
            <summary>{dateTimeLabel(version.created_at, timezone)}</summary>
            {version.content.summary ? <SummaryTranslation outputId={version.id} original={version.content.summary} />
              : <RichText text={t("No summary in this version.")} />}
          </details>
        ))
      ) : (
        <p className="caption">{t("No previous versions saved.")}</p>
      )}
    </div>
  );
}

export function Today({
  day,
  timezone,
  status,
  statusError,
  timeline,
  onUpload,
  onRewind,
  onChat,
  onPrepare,
}: {
  day: string;
  timezone: string;
  status?: Status;
  statusError?: string;
  timeline?: Timeline;
  onUpload: () => void;
  onPrepare: () => void;
  onRewind: (minutes: number) => void;
  onChat: (scope: ChatScope, question?: string) => void;
}) {
  return (
    <div className="day-layout">
      <div className="day-main">
        {day === localDay(timezone) ? <RecordingActivity status={status} error={statusError} /> : null}
        <RecapSection key={`recap-${day}`} day={day} timezone={timezone} status={status} statusError={statusError} onUpload={onUpload} onChat={onChat} compact />
        <TranscriptTimeline
          key={`timeline-${day}`}
          day={day}
          timezone={timezone}
          timeline={timeline}
          onUpload={onUpload}
          onChat={onChat}
        />
        <Decisions key={`decisions-${day}`} day={day} timezone={timezone} compact />
      </div>
      <aside className="day-rail" aria-label={t("Day tools and capture")}>
        <QuickRewind onRewind={onRewind} />
        <section className="day-tools">
          <h2>{t("Day tools")}</h2>
          <button className="day-tool" onClick={() => onChat({ kind: "day", day })}>
            <Icon name="chat" size={19} />
            <span>{t("Chat with this day")}</span>
            <Icon name="chevron-right" size={15} />
          </button>
          <button className="day-tool" onClick={onPrepare}>
            <Icon name="people" size={19} />
            <span>{t("Prepare for a conversation")}</span>
            <Icon name="chevron-right" size={15} />
          </button>
        </section>
        <section className="follow-through"><h2>{t("Follow-through")}</h2><InboxEntry /></section>
        <CaptureStatus status={status} error={statusError} compact />
        <div className="day-feedback"><FeedbackReview /></div>
      </aside>
    </div>
  );
}

function QuickRewind({ onRewind }: { onRewind: (minutes: number) => void }) {
  const [minutes, setMinutes] = useState(10);
  return (
    <section className="quick-rewind">
      <h2>{t("Quick rewind")}</h2>
      <label className="select-with-icon">
        <Icon name="clock" size={19} />
        <select
          value={minutes}
          onChange={(event) => setMinutes(Number(event.target.value))}
          aria-label={t("Rewind duration")}
        >
          {[5, 10, 15, 30, 60].map((value) => (
            <option key={value} value={value}>
              {value} {t("minutes")}{" "}</option>
          ))}
        </select>
      </label>
      <button className="button dark" onClick={() => onRewind(minutes)}>
        <Icon name="skip-back" size={19} />
        {t("Rewind audio")}{" "}</button>
    </section>
  );
}

function TranscriptTimeline({
  day,
  timezone,
  timeline,
  onUpload,
  onChat,
}: {
  day: string;
  timezone: string;
  timeline?: Timeline;
  onUpload: () => void;
  onChat: (scope: ChatScope) => void;
}) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [zoneID, setZoneID] = useState("");
  const [tagID, setTagID] = useState("");
  const [page, setPage] = useState(0);
  const params = new URLSearchParams({
    day,
    q: submitted,
    ...(zoneID ? { zone_id: zoneID } : {}),
    ...(tagID ? { tag_id: tagID } : {}),
    limit: "30",
    offset: String(page * 30),
  });
  const resource = useResource<RecordingGroupList>(
    `/v1/recording-groups?${params}`,
    15_000,
  );
  useEffect(() => {
    window.addEventListener("omi:tags-changed", resource.refresh);
    return () => window.removeEventListener("omi:tags-changed", resource.refresh);
  }, [resource.refresh]);
  const markers =
    timeline?.markers.filter(
      (mark) => localDay(timezone, new Date(mark.occurred_at)) === day,
    ) || [];
  const meetings =
    timeline?.meetings.filter(
      (meeting) => localDay(timezone, new Date(meeting.started_at)) === day,
    ) || [];
  const groups = resource.data?.items || [];
  return (
    <section className="timeline-section">
      <div className="section-heading">
        <div className="timeline-title">
          <h2>{t("Timeline")}</h2>
          {resource.data ? <span className="caption">{resource.data.total} {t("groups")}</span> : null}
        </div>
        {resource.data?.total ? (
          <button className="text-button" onClick={onUpload}>
            <Icon name="upload" size={16} />
            {t("Upload")}{" "}</button>
        ) : null}
      </div>
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : null}
      {markers.length || meetings.length ? (
        <div className="hint-list">
          {markers.map((mark) => (
            <p key={mark.event_id}>
              <Icon name="star" size={15} />
              <span>
                {t("Important around")}{" "}{timeLabel(mark.occurred_at, timezone)}
              </span>
            </p>
          ))}
          {meetings.map((meeting) => (
            <div key={meeting.id}>
              <p>
              <Icon name="mic" size={15} />
              <span>
                {t("Meeting around")}{" "}{timeLabel(meeting.started_at, timezone)}
                {meeting.ended_at
                  ? `–${timeLabel(meeting.ended_at, timezone)}`
                  : t("· in progress")}
              </span>
              <button
                className="text-button"
                onClick={() =>
                  onChat({ kind: "meeting", meeting_id: meeting.id })
                }
                aria-label={t("Chat with meeting at {value0}", { value0: timeLabel(meeting.started_at, timezone) })}
              >
                {t("Chat with meeting")}{" "}</button>
              <ImportanceButton target={{ kind: "meeting", id: meeting.id }} label={t("meeting at {value0}", { value0: timeLabel(meeting.started_at, timezone) })} />
              </p>
              <MeetingSpeakers id={meeting.id} />
            </div>
          ))}
          <p className="caption">
            {t("Hints guide interpretation of nearby speech; these are approximate times.")}{" "}</p>
        </div>
      ) : null}
      <div className="timeline-filters">
        <TagFilter value={tagID} onChange={(value) => { setTagID(value); setPage(0); }} />
        <ZoneFilter value={zoneID} onChange={(value) => { setZoneID(value); setPage(0); }} />
      </div>
      {resource.data?.total || submitted || zoneID || tagID ? (
        <form
          className="search-form"
          onSubmit={(event) => {
            event.preventDefault();
            setSubmitted(query.trim());
            setPage(0);
          }}
        >
          <Icon name="search" size={18} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("Search transcripts")}
            aria-label={t("Search this day's transcripts")}
          />
          <button className="text-button" type="submit">
            {t("Search")}{" "}</button>
          {submitted ? (
            <button
              className="text-button"
              type="button"
              onClick={() => {
                setQuery("");
                setSubmitted("");
                setPage(0);
              }}
            >
              {t("Clear")}{" "}</button>
          ) : null}
        </form>
      ) : null}
      {resource.loading && !resource.data ? (
        <Loading>{t("Loading recordings…")}</Loading>
      ) : groups.length ? (
        <>
          <RecordingGroups groups={groups} day={day} query={submitted} zoneID={zoneID} tagID={tagID} timezone={timezone} onCorrect={resource.refresh} timeline={timeline} />
          {(resource.data?.total || 0) > 30 || page > 0 ? <div className="pagination">
            <span className="caption">
              {page * 30 + 1}–{page * 30 + groups.length} {t("of")}{" "}
              {resource.data?.total}
            </span>
            <div className="button-row">
              <button
                className="button small"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                {t("Previous")}{" "}</button>
              <button
                className="button small"
                disabled={(page + 1) * 30 >= (resource.data?.total || 0)}
                onClick={() => setPage((value) => value + 1)}
              >
                {t("Next")}{" "}</button>
            </div>
          </div> : null}
        </>
      ) : (
        <EmptyState
          icon={submitted || zoneID || tagID ? "search" : "mic"}
          title={submitted || zoneID || tagID ? t("No matching recordings") : t("Make room for your first memory")}
        >
          {submitted || zoneID || tagID
            ? t("Try another word or choose a different day.")
            : t("Connect Omi on your iPhone, or upload a recording.")}
          <span className="empty-action">
            <button className="button accent" onClick={onUpload}>
              <Icon name="upload" size={18} />
              {t("Upload recording")}{" "}</button>
          </span>
        </EmptyState>
      )}
    </section>
  );
}

function MeetingSpeakers({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  return <details className="meeting-speakers" onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{t("Meeting speakers")}</summary>
    {open ? <Speakers kind="meeting" id={id} /> : null}
  </details>;
}
