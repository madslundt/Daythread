import { useEffect, useState } from "react";
import { errorMessage, post } from "./api";
import { localDay, dateTimeLabel } from "./format";
import { InboxEditor } from "./InboxEditor";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading, Notice } from "./UI";
import { deadlineLabel, type InboxPage, type InboxStatus } from "./inboxTypes";

export function InboxEntry() {
  const inbox = useResource<InboxPage>("/v1/inbox?limit=1", 15_000);
  return (
    <a className="button" href="/inbox">
      Inbox
      {inbox.data
        ? ` · ${inbox.data.pending} pending across days`
        : " · across all days"}
    </a>
  );
}

export function Inbox({ timezone }: { timezone: string }) {
  const [status, setStatus] = useState<InboxStatus>("pending");
  const [kind, setKind] = useState("");
  const [due, setDue] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string>();
  const [start, setStart] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() - 6);
    return localDay(timezone, date);
  });
  const [end, setEnd] = useState(() => localDay(timezone));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const params = new URLSearchParams({
    status,
    limit: "25",
    offset: String(offset),
  });
  if (kind) params.set("kind", kind);
  if (due) params.set("due", due);
  const resource = useResource<InboxPage>(`/v1/inbox?${params}`, 10_000);
  useEffect(() => {
    window.addEventListener("omi:archive-changed", resource.refresh);
    return () =>
      window.removeEventListener("omi:archive-changed", resource.refresh);
  }, [resource.refresh]);
  async function scan() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await post<{ total: number }>("/v1/inbox/extractions", {
        start_day: start,
        end_day: end,
        retry_failed: true,
      });
      setMessage(
        `${result.total} extraction window(s) queued or already scanned. Progress appears below; the local worker must be running.`,
      );
      resource.refresh();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  const coverage = resource.data?.coverage;
  return (
    <div className="feature-content">
      <p className="caption">
        Pending promises, deadlines and unanswered questions across all days.
        Model proposals need your review.
      </p>
      <details className="inbox-scan">
        <summary>Scan earlier recordings / retry failed work</summary>
        <form
          className="inbox-controls"
          onSubmit={(e) => {
            e.preventDefault();
            void scan();
          }}
        >
          <label>
            From
            <input
              type="date"
              required
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            Through
            <input
              type="date"
              required
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button className="button" disabled={busy}>
            {busy ? "Queuing…" : "Scan / retry"}
          </button>
        </form>
        <p className="caption">
          Up to 31 days per scan. Dates use {timezone}. New transcripts are
          scanned automatically. No external task synchronization.
        </p>
      </details>
      {message ? <Notice>{message}</Notice> : null}
      {error || resource.error ? (
        <ErrorState
          message={error || resource.error}
          retry={resource.refresh}
        />
      ) : null}
      {coverage ? (
        <div className="caption" role="status">
          {coverage.jobs.done || 0} scanned windows ·{" "}
          {coverage.jobs.queued || 0} waiting · {coverage.jobs.running || 0}{" "}
          extracting · {coverage.jobs.failed || 0} failed ·{" "}
          {coverage.unscanned_sources} unscanned sources
          {resource.data?.recent_jobs
            .filter((j) => j.error)
            .map((j) => (
              <p key={j.id}>
                {j.recording_start
                  ? `${dateTimeLabel(j.recording_start, timezone)} · `
                  : ""}
                {j.state}: {j.error} (attempt {j.attempts})
              </p>
            ))}
        </div>
      ) : null}
      <div className="inbox-controls">
        <label>
          Status
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as InboxStatus);
              setOffset(0);
            }}
          >
            <option value="pending">Pending</option>
            <option value="completed">Completed / answered</option>
            <option value="dismissed">Dismissed</option>
          </select>
        </label>
        <label>
          Kind
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All kinds</option>
            <option value="commitment">Commitments</option>
            <option value="deadline">Deadlines</option>
            <option value="question">Questions</option>
          </select>
        </label>
        <label>
          Deadline
          <select
            value={due}
            onChange={(e) => {
              setDue(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All deadlines</option>
            <option value="overdue">Overdue</option>
            <option value="upcoming">Upcoming</option>
            <option value="unknown">Unknown / unresolved</option>
          </select>
        </label>
      </div>
      {resource.loading && !resource.data ? <Loading /> : null}
      {resource.data?.items.map((item) => (
        <article className="inbox-card" key={item.id}>
          <button className="inbox-open" onClick={() => setSelected(item.id)}>
            <span className="eyebrow">
              {item.kind} · {item.reviewed_at ? "Reviewed" : "Unreviewed"}
            </span>
            <h2>{item.title}</h2>
            <p>
              {item.owner || "Owner unknown"} · {deadlineLabel(item.deadline)}
              {item.overdue ? " · Overdue" : ""}
            </p>
            {item.health !== "current" ? (
              <strong>
                Evidence{" "}
                {item.health === "withdrawn"
                  ? "withdrawn"
                  : "changed — review required"}
              </strong>
            ) : null}
            {item.duplicate_of ? <p>Dismissed as duplicate</p> : null}
          </button>
        </article>
      ))}
      {resource.data && !resource.data.items.length ? (
        <EmptyState title="No items in this view">
          {coverage?.jobs.queued || coverage?.jobs.running
            ? "Extraction is still waiting or running."
            : coverage?.jobs.failed
              ? "Some extractions failed. Retry the relevant dates above."
              : !coverage?.jobs.done
                ? "No extraction has completed yet. Scan a date range or capture new speech."
                : "No candidates match these filters. This does not establish that all obligations were recorded or found."}
        </EmptyState>
      ) : null}
      {resource.data && resource.data.total > 25 ? (
        <div className="pagination">
          <button
            className="button"
            disabled={!offset}
            onClick={() => setOffset((v) => v - 25)}
          >
            Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 25, resource.data.total)} of{" "}
            {resource.data.total}
          </span>
          <button
            className="button"
            disabled={offset + 25 >= resource.data.total}
            onClick={() => setOffset((v) => v + 25)}
          >
            Next
          </button>
        </div>
      ) : null}
      {selected ? (
        <InboxEditor
          key={selected}
          id={selected}
          timezone={timezone}
          onClose={() => {
            setSelected(undefined);
            resource.refresh();
          }}
        />
      ) : null}
    </div>
  );
}
