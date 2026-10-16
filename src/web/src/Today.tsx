import { profileResourceUrl } from "./profiles";
import { ImportanceButton } from "./Importance";
import { useEffect, useState, type ReactNode } from "react";
import { errorMessage, post } from "./api";
import { dateTimeLabel, localDay, timeLabel } from "./format";
import { CaptureStatus } from "./CaptureStatus";
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
import { Speakers } from "./Speakers";
import { RecapContent } from "./RecapContent";
import type { ChatScope, Recap, Source, Status, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading, Notice, RichText } from "./UI";

export function RecapSection({
  day,
  timezone,
  onGenerated,
  onUpload,
  onChat,
  compact = false,
}: {
  day: string;
  timezone: string;
  onGenerated?: () => void;
  onUpload?: () => void;
  onChat: (scope: ChatScope, question?: string) => void;
  compact?: boolean;
}) {
  const recap = useResource<Recap>(`/v1/recaps/${day}`, day === localDay(timezone) ? 60_000 : 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showVersions, setShowVersions] = useState(false);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", recap.refresh);
    return () =>
      window.removeEventListener("omi:archive-changed", recap.refresh);
  }, [recap.refresh]);
  async function generate() {
    setBusy(true);
    setError("");
    try {
      const result = await post<Recap>(`/v1/recaps/${day}`);
      recap.setData(result);
      onGenerated?.();
      window.dispatchEvent(new Event("omi:archive-changed"));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="recap-section">
      <div className="section-heading">
        <div>
          <h2>Day recap</h2>
          {compact ? <p className="section-description">The important parts, brought together.</p> : null}
        </div>
        <button className={`button ${compact ? "accent" : ""}`} onClick={generate} disabled={busy}>
          <Icon name="diary" size={18} />
          {busy ? "Generating…" : "Generate recap"}
        </button>
      </div>
      {error || recap.error ? (
        <ErrorState
          message={error || recap.error}
          retry={error ? () => void generate() : recap.refresh}
        />
      ) : null}
      {recap.data?.stale ? (
        <Notice>
          New or changed evidence is available. Regenerate this recap.
        </Notice>
      ) : null}
      {recap.data ? <RecapCoverage recap={recap.data} timezone={timezone} /> : null}
      {!compact ? <CompletenessDetails value={recap.data?.completeness} /> : null}
      {recap.loading && !recap.data ? (
        <Loading>Loading recap…</Loading>
      ) : recap.data?.summary ? (
        <div className="recap-content">
          <RecapContent
            key={`${day}:${recap.data.output_id || recap.data.generated_at || "unversioned"}:${recap.data.summary}`}
            text={recap.data.summary}
            day={day}
            version={recap.data.output_id || recap.data.generated_at || "unversioned"}
            sources={recap.data.sources || []}
            timezone={timezone}
            onChat={onChat}
            onCorrect={recap.refresh}
            compact={compact}
          />
          {recap.data.pending_chunks ? (
            <p className="caption">
              {recap.data.pending_chunks} recording(s) were awaiting
              transcription when generated. Regenerate to include later
              evidence.
            </p>
          ) : null}
          <RecapUtilities compact={compact} generatedAt={recap.data.generated_at} timezone={timezone}>
          <EvidenceFeedback outputId={recap.data.output_id} evidence={recap.data.evidence} outputText={recap.data.summary} />
          <div className="recap-footer">
            {recap.data.generated_at ? (
              <span className="caption">
                Generated at {timeLabel(recap.data.generated_at, timezone)}
              </span>
            ) : null}
            <a
              className="text-link caption"
              href={profileResourceUrl(`/v1/diary/${day}.md`)}
              download
            >
              <Icon name="download" size={14} />
              Export Markdown
            </a>
            <button
              className="text-button"
              onClick={() => setShowVersions((value) => !value)}
            >
              {showVersions ? "Hide saved versions" : "Saved versions"}
            </button>
          </div>
          {showVersions ? (
            <RecapVersions day={day} timezone={timezone} />
          ) : null}
          {recap.data.sources?.length ? (
            <details className="recap-sources">
              <summary>
                View {recap.data.sources.length} source recording(s)
              </summary>
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
            <h3>Your recap is ready when you are.</h3>
            <p>{emptyRecapMessage(recap.data)}</p>
            {onUpload ? <button className="text-button recap-upload" onClick={onUpload}>Add a recording</button> : null}
          </div> : <>
            <h3>The story of your day starts here.</h3>
            <p>{emptyRecapMessage(recap.data)}</p>
            {onUpload ? <button className="button accent recap-upload" onClick={onUpload}><Icon name="upload" size={17} />Add a recording</button> : null}
          </>}
        </div>
      )}
      {compact && !recap.data?.summary ? <CompletenessDetails value={recap.data?.completeness} /> : null}
    </section>
  );
}

function emptyRecapMessage(recap?: Recap): string {
  if (recap?.no_recap_content)
    return "No useful recap content in completed conversations yet. Your recordings and transcripts remain in the archive under your retention settings.";
  if (recap?.deferred_conversations || recap?.deferred_sources)
    return "Your current conversation is waiting to finish before it can be included in a recap.";
  if (recap?.pending_chunks)
    return "Audio is still being transcribed. Completed conversations can be included once it is ready.";
  return "Completed conversations will bring the important parts of your day together here.";
}

export function RecapCoverage({ recap, timezone }: { recap: Recap; timezone: string }) {
  const deferred = recap.deferred_conversations || 0;
  const deferredSources = recap.deferred_sources || 0;
  return <div className="recap-coverage caption" role="status">
    {recap.included_through ? <p>Includes completed conversations through {dateTimeLabel(recap.included_through, timezone)}.</p>
      : recap.included_through === null ? <p>No completed conversations included yet.</p> : null}
    {deferred || deferredSources ? <p>
      {deferred ? `${deferred} ${deferred === 1 ? "conversation is" : "conversations are"}` : "Recent recordings are"} waiting to finish before being included. Active conversations stay out of the recap.
    </p> : null}
    {recap.boundary_note ? <p>{recap.boundary_note}</p> : null}
  </div>;
}

function RecapUtilities({ compact, generatedAt, timezone, children }: { compact: boolean; generatedAt?: string; timezone: string; children: ReactNode }) {
  return compact ? <details className="recap-utilities">
    <summary><span>Sources, export & history</span>{generatedAt ? <span>Generated at {timeLabel(generatedAt, timezone)}</span> : null}</summary>
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
        <Loading>Loading saved versions…</Loading>
      ) : resource.data?.items.length ? (
        resource.data.items.map((version) => (
          <details key={version.id}>
            <summary>{dateTimeLabel(version.created_at, timezone)}</summary>
            <RichText
              text={version.content.summary || "No summary in this version."}
            />
          </details>
        ))
      ) : (
        <p className="caption">No previous versions saved.</p>
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
        <RecapSection key={day} day={day} timezone={timezone} onUpload={onUpload} onChat={onChat} compact />
        <TranscriptTimeline
          day={day}
          timezone={timezone}
          timeline={timeline}
          onUpload={onUpload}
          onChat={onChat}
        />
        <Decisions key={`decisions-${day}`} day={day} timezone={timezone} compact />
      </div>
      <aside className="day-rail" aria-label="Day tools and capture">
        <QuickRewind onRewind={onRewind} />
        <section className="day-tools">
          <h2>Day tools</h2>
          <button className="day-tool" onClick={() => onChat({ kind: "day", day })}>
            <Icon name="chat" size={19} />
            <span>Chat with this day<small>Ask about your recordings</small></span>
            <Icon name="chevron-right" size={15} />
          </button>
          <button className="day-tool" onClick={onPrepare}>
            <Icon name="people" size={19} />
            <span>Prepare for a conversation<small>Bring the right context</small></span>
            <Icon name="chevron-right" size={15} />
          </button>
        </section>
        <section className="follow-through"><h2>Follow-through</h2><p className="caption">Promises and open questions.</p><InboxEntry /></section>
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
      <h2>Quick rewind</h2>
      <p className="caption">Jump back into the conversation.</p>
      <label className="select-with-icon">
        <Icon name="clock" size={19} />
        <select
          value={minutes}
          onChange={(event) => setMinutes(Number(event.target.value))}
          aria-label="Rewind duration"
        >
          {[5, 10, 15, 30, 60].map((value) => (
            <option key={value} value={value}>
              {value} minutes
            </option>
          ))}
        </select>
      </label>
      <button className="button dark" onClick={() => onRewind(minutes)}>
        <Icon name="skip-back" size={19} />
        Rewind audio
      </button>
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
  const [page, setPage] = useState(0);
  const params = new URLSearchParams({
    day,
    q: submitted,
    limit: "30",
    offset: String(page * 30),
  });
  const resource = useResource<{ items: Source[]; total: number }>(
    `/v1/transcripts?${params}`,
    15_000,
  );
  const markers =
    timeline?.markers.filter(
      (mark) => localDay(timezone, new Date(mark.occurred_at)) === day,
    ) || [];
  const meetings =
    timeline?.meetings.filter(
      (meeting) => localDay(timezone, new Date(meeting.started_at)) === day,
    ) || [];
  const sources = (resource.data?.items || []).map((source) => ({
    ...source,
    important: timeline?.markers.some(
      (mark) =>
        source.started_at < mark.context_end &&
        source.ended_at > mark.context_start,
    ),
    meeting_hint_ids: timeline?.meetings
      .filter(
        (meeting) =>
          source.started_at < meeting.context_end &&
          source.ended_at > meeting.context_start,
      )
      .map((meeting) => meeting.id),
  }));
  return (
    <section className="timeline-section">
      <div className="section-heading">
        <div className="timeline-title">
          <h2>Timeline</h2>
          {resource.data ? <span className="caption">{resource.data.total} {submitted ? "matching " : ""}{resource.data.total === 1 ? "recording" : "recordings"}</span> : null}
        </div>
        {resource.data?.total ? (
          <button className="text-button" onClick={onUpload}>
            <Icon name="upload" size={16} />
            Upload
          </button>
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
                Important around {timeLabel(mark.occurred_at, timezone)}
              </span>
            </p>
          ))}
          {meetings.map((meeting) => (
            <div key={meeting.id}>
              <p>
              <Icon name="mic" size={15} />
              <span>
                Meeting around {timeLabel(meeting.started_at, timezone)}
                {meeting.ended_at
                  ? `–${timeLabel(meeting.ended_at, timezone)}`
                  : " · in progress"}
              </span>
              <button
                className="text-button"
                onClick={() =>
                  onChat({ kind: "meeting", meeting_id: meeting.id })
                }
                aria-label={`Chat with meeting at ${timeLabel(meeting.started_at, timezone)}`}
              >
                Chat with meeting
              </button>
              <ImportanceButton target={{ kind: "meeting", id: meeting.id }} label={`meeting at ${timeLabel(meeting.started_at, timezone)}`} />
              </p>
              <MeetingSpeakers id={meeting.id} />
            </div>
          ))}
          <p className="caption">
            Hints guide interpretation of nearby speech; these are approximate
            times.
          </p>
        </div>
      ) : null}
      {resource.data?.total || submitted ? (
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
            placeholder="Search this day’s transcripts"
            aria-label="Search this day's transcripts"
          />
          <button className="text-button" type="submit">
            Search
          </button>
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
              Clear
            </button>
          ) : null}
        </form>
      ) : null}
      {resource.loading && !resource.data ? (
        <Loading>Loading recordings…</Loading>
      ) : sources.length ? (
        <>
          <SourceList
            sources={sources}
            timezone={timezone}
            onCorrect={resource.refresh}
          />
          {(resource.data?.total || 0) > 30 || page > 0 ? <div className="pagination">
            <span className="caption">
              {page * 30 + 1}–{page * 30 + sources.length} of{" "}
              {resource.data?.total}
            </span>
            <div className="button-row">
              <button
                className="button small"
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
              >
                Previous
              </button>
              <button
                className="button small"
                disabled={(page + 1) * 30 >= (resource.data?.total || 0)}
                onClick={() => setPage((value) => value + 1)}
              >
                Next
              </button>
            </div>
          </div> : null}
        </>
      ) : (
        <EmptyState
          icon={submitted ? "search" : "mic"}
          title={submitted ? "No matching recordings" : "Make room for your first memory"}
        >
          {submitted
            ? "Try another word or choose a different day."
            : "Connect Omi on your iPhone, or upload a recording."}
          <span className="empty-action">
            <button className="button accent" onClick={onUpload}>
              <Icon name="upload" size={18} />
              Upload recording
            </button>
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
    <summary>Meeting speakers</summary>
    {open ? <Speakers kind="meeting" id={id} /> : null}
  </details>;
}
