import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, post, request } from "./api";
import { dateTimeLabel } from "./format";
import { allDayFilters, dayFilterKey, dayFilterParams, hasDayFilters, type DayFilters } from "./dayFilters";
import { t } from "./i18n";
import { InboxEditor } from "./InboxEditor";
import { deadlineLabel, type InboxItem, type InboxPage, type InboxStatus } from "./inboxTypes";
import type { RecapFocus } from "./recapItems";
import type { ChatScope } from "./types";
import { statusLabel } from "./statusLabels";
import { ErrorState, Loading } from "./UI";
import { useHighlightList } from "./useHighlightList";
import { DecisionHighlights } from "./DecisionHighlights";
import { actionableKind, actionMatchesTopic, highlightCompletionLabel, highlightGroups, highlightReviewLabel, orderedHighlights, type HighlightCategory } from "./dayHighlights";
import { DisplayTranslationStatus, useDisplayTranslations } from "./displayTranslations";
export { actionMatchesTopic } from "./dayHighlights";

export function ActionPoints({ day, timezone, focus, onChat, filters = allDayFilters }: {
  day: string; timezone: string; focus?: RecapFocus;
  filters?: DayFilters;
  onChat?: (scope: ChatScope, question?: string) => void;
}) {
  const [status, setStatus] = useState<InboxStatus>("pending");
  const [category, setCategory] = useState<HighlightCategory>("all");
  const [visible, setVisible] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<string>();
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const filtered = hasDayFilters(filters);
  const scopeKey = dayFilterKey(filters);
  const params = new URLSearchParams({ day, status: "pending", limit: "200", offset: "0", ...dayFilterParams(filters) });
  const resource = useHighlightList<InboxPage>(`/v1/inbox?${params}`, "omi:inbox-changed");
  const reviewed = useHighlightList<InboxPage>(`/v1/inbox?${new URLSearchParams({ day, status: "completed", limit: "200", offset: "0", ...dayFilterParams(filters) })}`, "omi:inbox-changed");
  useEffect(() => { setSelected(undefined); setVisible({}); setCategory("all"); setStatus("pending"); setScanError(""); }, [day, scopeKey]);
  const refresh = () => { resource.refresh(); reviewed.refresh(); };
  useEffect(() => {
    const close = () => setSelected(undefined);
    window.addEventListener("omi:archive-changed", close);
    return () => window.removeEventListener("omi:archive-changed", close);
  }, []);
  async function scan() {
    setScanning(true); setScanError("");
    try { await post("/v1/inbox/extractions", { start_day: day, end_day: day, retry_failed: true }); refresh(); }
    catch (error) { setScanError(errorMessage(error)); }
    finally { setScanning(false); }
  }
  const latest = new Map<string, InboxItem>();
  for (const item of [...resource.items, ...reviewed.items]) if (!latest.has(item.id) || latest.get(item.id)!.version <= item.version) latest.set(item.id, item);
  const items = orderedHighlights([...latest.values()].filter((item) => item.health !== "withdrawn"
    && (!actionableKind(item.kind) || item.status === status) && (!focus || actionMatchesTopic(item, focus))));
  const display = useDisplayTranslations(items.map((item) => ({ kind: "inbox", id: item.id, version: item.version, original_title: item.title })), `${day}:${scopeKey}`);
  const coverage = resource.data?.coverage;
  const showFollowups = category !== "decisions";
  const extracting = Boolean(coverage?.jobs.running || coverage?.jobs.queued);
  const extractionFailed = Boolean(coverage?.jobs.failed);
  const groups = highlightGroups.map((group) => ({ ...group, items: items.filter((item) => new Set<string>(group.kinds).has(item.kind)) }));
  const shown = groups.filter((group) => category === "all" || category === group.id);
  const renderGroup = (group: typeof groups[number]) => !group.items.length ? null : <section className="highlight-group" key={group.id} aria-label={t(group.title)}>
    <div className="highlight-group-heading"><h3>{t(group.title)}</h3><span className="caption">{group.items.length}</span></div>
    {group.items.slice(0, visible[group.id] || 5).map((item) => <ActionPointRow key={`${item.id}:${item.version}`} item={item} timezone={timezone}
      displayTitle={display.title({ kind: "inbox", id: item.id, version: item.version, original_title: item.title })} language={display.language}
      onReview={() => setSelected(item.id)} onChanged={refresh} onChat={!filtered && onChat ? () => onChat({ kind: "day", day }, item.kind === "question"
        ? t("Help me answer this question from this day: {value0}", { value0: item.title })
        : t("Help me think through this topic from this day: {value0}", { value0: item.title })) : undefined} />)}
    {group.items.length > (visible[group.id] || 5) ? <button type="button" className="text-button highlight-show-more" onClick={() => setVisible((current) => ({ ...current, [group.id]: (current[group.id] || 5) + 10 }))}>{t("Show more")}</button> : null}
  </section>;
  return <section className="action-points day-highlights" aria-label={t("Your day at a glance")}>
    <div className="section-heading"><h2>{t("Your day at a glance")}</h2><a className="text-link" href="/inbox">{t("Open Inbox")}</a></div>
    <p className="caption">{focus ? t("Highlights with evidence in {value0}.", { value0: display.language ? focus.displayTitle || focus.title : focus.title }) : t("What to do, answer, remember, or think through.")}</p>
    <DisplayTranslationStatus value={display} />
    <div className="highlight-controls">
      <label><span className="sr-only">{t("Overview category")}</span><select value={category} onChange={(event) => setCategory(event.target.value as HighlightCategory)}>
        <option value="all">{t("All categories")}</option>
        {highlightGroups.slice(0, 3).map((group) => <option key={group.id} value={group.id}>{t(group.title)}</option>)}
        <option value="decisions">{t("Decisions")}</option>
        {highlightGroups.slice(3).map((group) => <option key={group.id} value={group.id}>{t(group.title)}</option>)}
      </select></label>
      {showFollowups && !filtered ? <button type="button" className="text-button" disabled={scanning} onClick={() => void scan()}>{scanning ? t("Queuing…") : t("Find highlights")}</button> : null}
    </div>
    {!["decisions", "reflections", "notes"].includes(category) ? <div className="action-point-filters" aria-label={t("Follow-up status")}>
      <span className="caption">{t("Follow-ups")}</span>
      <button type="button" className="text-button" aria-pressed={status === "pending"} onClick={() => setStatus("pending")}>{t("Open")}</button>
      <button type="button" className="text-button" aria-pressed={status === "completed"} onClick={() => setStatus("completed")}>{t("Reviewed / done")}</button>
    </div> : null}
    {showFollowups && (resource.error || reviewed.error || scanError) ? <ErrorState message={resource.error || reviewed.error || scanError} retry={refresh} /> : null}
    {showFollowups && (extracting || extractionFailed) ? <section className="highlight-extraction-status" aria-label={t("Follow-up extraction")}>
      <h3>{t("Follow-up extraction")}</h3>
      {extracting ? <p className="caption" role="status">{t("Highlights are being extracted from your recordings.")}</p> : null}
      {extractionFailed ? <><p className="caption">{t("Some follow-ups or notes could not be extracted.")}</p>
        <div className="recap-topic-actions">{!filtered ? <button type="button" className="text-button" disabled={scanning} onClick={() => void scan()}>{scanning ? t("Queuing…") : t("Retry follow-up extraction")}</button> : null}
          <a className="text-link" href="/inbox">{t("Open Inbox")}</a></div></> : null}
    </section> : null}
    {showFollowups && (resource.loading && !resource.data || resource.loadingMore || reviewed.loading && !reviewed.data || reviewed.loadingMore) ? <Loading>{t("Loading highlights…")}</Loading> : null}
    {shown.filter((group) => group.id !== "reflections" && group.id !== "notes").map(renderGroup)}
    {category === "all" || category === "decisions" ? <DecisionHighlights day={day} timezone={timezone} focus={focus} filters={filters} /> : null}
    {shown.filter((group) => group.id === "reflections" || group.id === "notes").map(renderGroup)}
    {showFollowups && !extracting && !extractionFailed && resource.data && reviewed.data && !resource.loadingMore && !reviewed.loadingMore && !shown.some((group) => group.items.length) ? <p className="action-points-empty">{focus ? t("No matching follow-ups or notes found in this topic.")
        : status === "completed" ? t("No reviewed or completed items in this view.") : t("No open follow-ups or notes found in this view.")}</p> : null}
    <p className="caption action-point-note">{t("Saved across your devices. Your answers and notes stay separate from recorded evidence.")}</p>
    {selected ? <InboxEditor key={selected} id={selected} timezone={timezone} onClose={() => { setSelected(undefined); refresh(); }} /> : null}
  </section>;
}

export function ActionPointRow({ item, timezone, onReview, onChanged, onChat, displayTitle = item.title, language }: {
  item: InboxItem; timezone: string; onReview: () => void; onChanged: () => void; onChat?: () => void;
  displayTitle?: string; language?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const operation = useRef<string | undefined>(undefined);
  async function complete() {
    if (busy) return;
    setBusy(true); setError(""); operation.current ??= newId();
    try {
      await request(`/v1/inbox/${encodeURIComponent(item.id)}`, { method: "PATCH", body: JSON.stringify({
        operation_id: operation.current, expected_version: item.version, status: item.status === "completed" ? "pending" : "completed",
      }) });
      window.dispatchEvent(new Event("omi:inbox-changed")); onChanged();
    } catch (error) { setError(errorMessage(error)); onChanged(); }
    finally { setBusy(false); }
  }
  const date = item.deadline?.precision === "datetime" && item.deadline.instant ? dateTimeLabel(item.deadline.instant, timezone)
    : item.deadline?.precision === "date" || item.deadline?.phrase ? deadlineLabel(item.deadline) : actionableKind(item.kind) ? t("No date set") : "";
  const meta = [statusLabel(item.kind), item.owner || (actionableKind(item.kind) ? t("Owner unknown") : ""), date,
    !actionableKind(item.kind) && item.status === "completed" ? t("Reviewed") : !item.reviewed_at ? t("Unreviewed") : ""].filter(Boolean).join(" · ");
  return <article className={`action-point ${item.status === "completed" && ["commitment", "deadline", "reminder"].includes(item.kind) ? "is-completed" : ""}`}>
    <div className="action-point-line">
      <label className="action-point-check"><input type="checkbox" checked={item.status === "completed"} disabled={busy || item.health !== "current"}
        onChange={() => void complete()} aria-label={highlightCompletionLabel(item, displayTitle)} /></label>
      <div className="action-point-text"><p lang={language}>{displayTitle}</p><p className="caption">{meta}</p>
        {item.overdue ? <p className="caption action-point-warning">{t("Overdue")}</p> : null}
        {item.health !== "current" ? <p className="caption action-point-warning">{t("Evidence changed — review required")}</p> : null}
      </div>
      <button className="button small" onClick={onReview}>{highlightReviewLabel(item)}</button>
    </div>
    {item.user_note ? <p className="highlight-personal-note"><strong>{item.kind === "question" ? t("Your answer") : t("Your notes")}</strong> {item.user_note}</p> : null}
    {onChat && ["question", "reflection"].includes(item.kind) ? <button type="button" className="text-button highlight-chat" onClick={onChat}>{item.kind === "question" ? t("Ask your memory") : t("Think this through")}</button> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
  </article>;
}
