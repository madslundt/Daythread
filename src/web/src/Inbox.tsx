import { statusLabel } from "./statusLabels";
import { t } from "./i18n";
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
      {t("Inbox")}{" "}{inbox.data
        ? t(" · {value0} pending across days", { value0: inbox.data.pending })
        : t("· across all days")}
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
        t("{value0} extraction window(s) queued or already scanned. Progress appears below; the local worker must be running.", { value0: result.total }),
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
        {t("Pending promises, deadlines and unanswered questions across all days. Model proposals need your review.")}{" "}</p>
      <details className="inbox-scan">
        <summary>{t("Scan earlier recordings / retry failed work")}</summary>
        <form
          className="inbox-controls"
          onSubmit={(e) => {
            e.preventDefault();
            void scan();
          }}
        >
          <label>
            {t("From")}{" "}<input
              type="date"
              required
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            {t("Through")}{" "}<input
              type="date"
              required
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button className="button" disabled={busy}>
            {busy ? t("Queuing…") : t("Scan / retry")}
          </button>
        </form>
        <p className="caption">
          {t("Up to 31 days per scan. Dates use")}{" "}{timezone}{t(". New transcripts are scanned automatically. No external task synchronization.")}{" "}</p>
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
          {coverage.jobs.done || 0} {t("scanned windows ·")}{" "}
          {coverage.jobs.queued || 0} {t("waiting ·")}{" "}{coverage.jobs.running || 0}{" "}
          {t("extracting ·")}{" "}{coverage.jobs.failed || 0} {t("failed ·")}{" "}
          {coverage.unscanned_sources} {t("unscanned sources")}{" "}{resource.data?.recent_jobs
            .filter((j) => j.error)
            .map((j) => (
              <p key={j.id}>
                {j.recording_start
                  ? `${dateTimeLabel(j.recording_start, timezone)} · `
                  : ""}
                {statusLabel(j.state)}: {t(j.error || "")} {t("(attempt")}{" "}{j.attempts})
              </p>
            ))}
        </div>
      ) : null}
      <div className="inbox-controls">
        <label>
          {t("Status")}{" "}<select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as InboxStatus);
              setOffset(0);
            }}
          >
            <option value="pending">{t("Pending")}</option>
            <option value="completed">{t("Completed / answered")}</option>
            <option value="dismissed">{t("Dismissed")}</option>
          </select>
        </label>
        <label>
          {t("Kind")}{" "}<select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">{t("All kinds")}</option>
            <option value="commitment">{t("Commitments")}</option>
            <option value="deadline">{t("Deadlines")}</option>
            <option value="question">{t("Questions")}</option>
          </select>
        </label>
        <label>
          {t("Deadline")}{" "}<select
            value={due}
            onChange={(e) => {
              setDue(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">{t("All deadlines")}</option>
            <option value="overdue">{t("Overdue")}</option>
            <option value="upcoming">{t("Upcoming")}</option>
            <option value="unknown">{t("Unknown / unresolved")}</option>
          </select>
        </label>
      </div>
      {resource.loading && !resource.data ? <Loading /> : null}
      {resource.data?.items.map((item) => (
        <article className="inbox-card" key={item.id}>
          <button className="inbox-open" onClick={() => setSelected(item.id)}>
            <span className="eyebrow">
              {statusLabel(item.kind)} · {item.reviewed_at ? t("Reviewed") : t("Unreviewed")}
            </span>
            <h2>{item.title}</h2>
            <p>
              {item.owner || t("Owner unknown")} · {deadlineLabel(item.deadline)}
              {item.overdue ? t("· Overdue") : ""}
            </p>
            {item.health !== "current" ? (
              <strong>
                {t("Evidence")}{" "}
                {item.health === "withdrawn"
                  ? t("withdrawn")
                  : t("changed — review required")}
              </strong>
            ) : null}
            {item.duplicate_of ? <p>{t("Dismissed as duplicate")}</p> : null}
          </button>
        </article>
      ))}
      {resource.data && !resource.data.items.length ? (
        <EmptyState title={t("No items in this view")}>
          {coverage?.jobs.queued || coverage?.jobs.running
            ? t("Extraction is still waiting or running.")
            : coverage?.jobs.failed
              ? t("Some extractions failed. Retry the relevant dates above.")
              : !coverage?.jobs.done
                ? t("No extraction has completed yet. Scan a date range or capture new speech.")
                : t("No candidates match these filters. This does not establish that all obligations were recorded or found.")}
        </EmptyState>
      ) : null}
      {resource.data && resource.data.total > 25 ? (
        <div className="pagination">
          <button
            className="button"
            disabled={!offset}
            onClick={() => setOffset((v) => v - 25)}
          >
            {t("Previous")}{" "}</button>
          <span>
            {offset + 1}–{Math.min(offset + 25, resource.data.total)} {t("of")}{" "}
            {resource.data.total}
          </span>
          <button
            className="button"
            disabled={offset + 25 >= resource.data.total}
            onClick={() => setOffset((v) => v + 25)}
          >
            {t("Next")}{" "}</button>
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
