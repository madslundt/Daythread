import { useEffect, useState } from "react";
import { errorMessage, post } from "./api";
import { dateTimeLabel, localDay, timeLabel } from "./format";
import { Icon } from "./Icon";
import {
  CompletenessDetails,
  EvidenceFeedback,
  FeedbackReview,
} from "./CompletenessFeedback";
import { SourceList } from "./SourceList";
import type { ChatScope, Recap, Source, Status, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading, Notice, RichText } from "./UI";

export function RecapSection({
  day,
  timezone,
  onGenerated,
}: {
  day: string;
  timezone: string;
  onGenerated?: () => void;
}) {
  const recap = useResource<Recap>(`/v1/recaps/${day}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showVersions, setShowVersions] = useState(false);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", recap.refresh);
    return () =>
      window.removeEventListener("omi:archive-changed", recap.refresh);
  }, [recap.refresh]);
  async function generate() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await post<Recap>(`/v1/recaps/${day}`);
      recap.setData(result);
      onGenerated?.();
      window.dispatchEvent(new Event("omi:archive-changed"));
      if (!result.summary)
        setMessage(
          result.pending_chunks
            ? "Audio is still being transcribed. Generate your recap once it is ready."
            : "There is no transcribed speech for this day yet.",
        );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="recap-section">
      <div className="section-heading">
        <h2>Day recap</h2>
        <button className="button" onClick={generate} disabled={busy}>
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
      {message ? <Notice>{message}</Notice> : null}
      {recap.data?.stale ? (
        <Notice>
          New or changed evidence is available. Regenerate this recap.
        </Notice>
      ) : null}
      <CompletenessDetails value={recap.data?.completeness} />
      {recap.loading && !recap.data ? (
        <Loading>Loading recap…</Loading>
      ) : recap.data?.summary ? (
        <div className="recap-content">
          <RichText text={recap.data.summary} />
          <EvidenceFeedback
            outputId={recap.data.output_id}
            evidence={recap.data.evidence}
            outputText={recap.data.summary}
          />
          {recap.data.pending_chunks ? (
            <p className="caption">
              {recap.data.pending_chunks} recording(s) were awaiting
              transcription when generated. Regenerate to include later
              evidence.
            </p>
          ) : null}
          <div className="recap-footer">
            {recap.data.generated_at ? (
              <span className="caption">
                Generated at {timeLabel(recap.data.generated_at, timezone)}
              </span>
            ) : null}
            <a
              className="text-link caption"
              href={`/v1/diary/${day}.md`}
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
        </div>
      ) : (
        <div className="recap-empty">
          Your recap will appear here once audio has been transcribed.
        </div>
      )}
    </section>
  );
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
}: {
  day: string;
  timezone: string;
  status?: Status;
  statusError?: string;
  timeline?: Timeline;
  onUpload: () => void;
  onRewind: (minutes: number) => void;
  onChat: (scope: ChatScope) => void;
}) {
  return (
    <div className="day-layout">
      <div className="day-main">
        <div className="day-chat-action">
          <button
            className="button"
            onClick={() => onChat({ kind: "day", day })}
          >
            <Icon name="chat" size={17} />
            Chat with this day
          </button>
        </div>
        <RecapSection key={day} day={day} timezone={timezone} />
        <TranscriptTimeline
          day={day}
          timezone={timezone}
          timeline={timeline}
          onUpload={onUpload}
          onChat={onChat}
        />
      </div>
      <aside className="day-rail">
        <QuickRewind onRewind={onRewind} />
        <CaptureStatus status={status} error={statusError} />
        <FeedbackReview />
      </aside>
    </div>
  );
}

function QuickRewind({ onRewind }: { onRewind: (minutes: number) => void }) {
  const [minutes, setMinutes] = useState(10);
  return (
    <section className="quick-rewind">
      <h2>Rewind</h2>
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
        Rewind
      </button>
      <p className="caption">Listen back or get a quick recap.</p>
    </section>
  );
}

export function CaptureStatus({
  status,
  error,
}: {
  status?: Status;
  error?: string;
}) {
  const when = (at?: string | null) =>
    at ? new Date(at).toLocaleString() : "unknown";
  const processing = status?.processing;
  const stale =
    !!error ||
    (!!status?.as_of && Date.now() - Date.parse(status.as_of) > 120_000);
  return (
    <section className="capture-status">
      <h2>Capture status</h2>
      {error ? <ErrorState message={error} /> : null}
      {!status ? (
        <p className="caption">No current backend observation.</p>
      ) : (
        <>
          <p className="caption">
            Backend observation {when(status.as_of)}
            {stale ? " · stale; current state unknown" : ""}
          </p>
          <p>
            Last recording received: {when(status.ingestion?.last_received_at)}
          </p>
          <p className="caption">
            Latest captured endpoint: {when(status.ingestion?.last_captured_at)}
            . Backlog uploads can arrive later; this does not establish
            continuous capture.
          </p>
          <p className="caption">
            Shared pause requested: {status.capture_paused ? "yes" : "no"}.
            Observed phone state is separate.
          </p>
          {processing ? (
            <>
              <p>
                {processing.queued} queued ({processing.retrying} retrying) ·{" "}
                {processing.transcribing} transcribing · {processing.blocked}{" "}
                blocked
              </p>
              <p className="caption">
                Oldest known pending receipt:{" "}
                {when(processing.oldest_received_at)}
                {processing.oldest_age_seconds != null
                  ? ` · waiting ${Math.round(processing.oldest_age_seconds / 60)} min`
                  : ""}
              </p>
              {processing.recent_errors.map((e) => (
                <p className="caption" key={e.id}>
                  {e.id}: {e.error_code} · {e.attempts} attempts
                </p>
              ))}
            </>
          ) : null}
          {!status.worker_running ? (
            <p className="caption">
              Transcription worker{" "}
              {status.worker_enabled ? "not running" : "disabled"}.
            </p>
          ) : null}
          {status.phones?.length ? (
            status.phones.map((phone) => (
              <div key={phone.phone_id}>
                <p>
                  Phone{" "}
                  {phone.fresh && !stale
                    ? phone.capture_state
                    : "state unknown"}
                </p>
                <p className="caption">
                  Reported {when(phone.observed_at)}: {phone.capture_state} (
                  {phone.cause}). Last decoded audio{" "}
                  {when(phone.last_decoded_at)}.
                </p>
                <p className="caption">
                  {phone.pending_recordings} recordings waiting ·{" "}
                  {phone.recovery_items} unfinished/recovery ·{" "}
                  {Math.round(phone.pending_seconds / 60)} min ·{" "}
                  {phone.upload_policy.replaceAll("_", " ")} ·{" "}
                  {phone.upload_state}
                </p>
              </div>
            ))
          ) : (
            <p className="caption">
              No phone report received; phone connectivity is unknown.
            </p>
          )}
          <p className="caption">
            {status.capture_gaps ?? 0} stored capture interruptions. A saved
            archive does not prove live capture.
          </p>
        </>
      )}
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
        <h2>Timeline</h2>
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
            <p key={meeting.id}>
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
            </p>
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
          <div className="pagination">
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
          </div>
        </>
      ) : (
        <EmptyState
          title={submitted ? "No matching recordings" : "No audio yet"}
        >
          {submitted
            ? "Try another word or choose a different day."
            : "Connect Omi on your iPhone, or upload a recording."}
          <span className="empty-action">
            <button className="button" onClick={onUpload}>
              <Icon name="upload" size={18} />
              Upload recording
            </button>
          </span>
        </EmptyState>
      )}
    </section>
  );
}
