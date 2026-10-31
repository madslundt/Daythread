import { TagFilter } from "./ConversationTags";
import { ZoneFilter } from "./LocationZones";
import { t } from "./i18n";
import { ImportanceButton } from "./Importance";
import { useEffect, useState } from "react";
import { allDayFilters, dayFilterKey, dayFiltersForProfile, hasDayFilters, rememberDayFilters, type DayFilters } from "./dayFilters";
import { localDay, timeLabel } from "./format";
import { CaptureStatus, RecordingActivity } from "./CaptureStatus";
export { CaptureStatus } from "./CaptureStatus";
import { Icon } from "./Icon";
import { FeedbackReview } from "./CompletenessFeedback";
import { InboxEntry } from "./Inbox";
import { RecordingGroups, type RecordingGroupList } from "./RecordingGroups";
import { Speakers } from "./Speakers";
import { RecapSection } from "./RecapSection";
export { RecapSection, RecapCoverage } from "./RecapSection";
import type { ChatScope, Status, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading } from "./UI";

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
  const [filters, setFilters] = useState(dayFiltersForProfile);
  const [filtersOpen, setFiltersOpen] = useState(() => hasDayFilters(dayFiltersForProfile()));
  const [membershipVersion, setMembershipVersion] = useState(0);
  const filtered = hasDayFilters(filters);
  const scopeKey = `${day}:${dayFilterKey(filters)}:${membershipVersion}`;
  function changeFilters(next: DayFilters) { rememberDayFilters(next); setFilters(next); }
  useEffect(() => {
    const refresh = () => setMembershipVersion((value) => value + 1);
    const events = ["omi:tags-changed", "omi:zones-changed", "omi:locations-changed", "omi:archive-changed"];
    for (const event of events) window.addEventListener(event, refresh);
    return () => { for (const event of events) window.removeEventListener(event, refresh); };
  }, []);
  return (
    <div className="day-layout">
      <div className="day-main">
        {day === localDay(timezone) ? <RecordingActivity status={status} error={statusError} /> : null}
        <section className="day-filters" aria-label={t("Day filters")}>
          <div className="day-filter-heading">
            <button type="button" className="text-button" aria-expanded={filtersOpen} aria-controls="day-filter-options" onClick={() => setFiltersOpen(!filtersOpen)}>
              <Icon name="filter" size={17} />{t("Day filters")}
              {filtered ? <span className="filter-count">{Number(Boolean(filters.tagID)) + Number(Boolean(filters.zoneID))}</span> : null}
              <Icon name="chevron-right" size={13} />
            </button>
            {filtered ? <button type="button" className="text-button" onClick={() => changeFilters(allDayFilters)}>{t("Clear filters")}</button> : null}
          </div>
          <div id="day-filter-options" hidden={!filtersOpen}>
          <div className="timeline-filters">
            <TagFilter value={filters.tagID} onChange={(tagID) => changeFilters({ ...filters, tagID })} />
            <ZoneFilter value={filters.zoneID} onChange={(zoneID) => changeFilters({ ...filters, zoneID })} />
          </div>
          <p className="caption">{t("Filters apply to the recap, highlights, topics, and timeline.")}</p>
          </div>
          {filtered ? <p className="caption" role="status">{t("Only recordings matching every selected filter are included.")}</p> : null}
        </section>
        <RecapSection key={`recap-${scopeKey}`} day={day} timezone={timezone} status={status} statusError={statusError} onUpload={onUpload} onChat={onChat} filters={filters} compact />
        <TranscriptTimeline
          key={`timeline-${scopeKey}`}
          day={day}
          timezone={timezone}
          timeline={timeline}
          onUpload={onUpload}
          onChat={onChat}
          filters={filters}
        />
      </div>
      <aside className="day-rail" aria-label={t("Day tools and capture")}>
        {filtered ? <p className="caption day-whole-day-tools">{t("Chat and quick rewind use the whole day.")}</p> : null}
        <details className="day-utility"><summary><Icon name="rewind" size={18} />{t("Quick rewind")}<Icon name="chevron-right" size={14} /></summary>
          <QuickRewind onRewind={onRewind} />
        </details>
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
  filters,
}: {
  day: string;
  timezone: string;
  timeline?: Timeline;
  onUpload: () => void;
  onChat: (scope: ChatScope) => void;
  filters: DayFilters;
}) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const { zoneID, tagID } = filters;
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
  const markers =
    !hasDayFilters(filters) && timeline?.markers.filter(
      (mark) => localDay(timezone, new Date(mark.occurred_at)) === day,
    ) || [];
  const meetings =
    !hasDayFilters(filters) && timeline?.meetings.filter(
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
