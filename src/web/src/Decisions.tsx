import { t } from "./i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, newId, post, request } from "./api";
import { DecisionEvidence } from "./DecisionEvidence";
import { DecisionEditor } from "./DecisionEditor";
import { DecisionFollowups } from "./DecisionFollowups";
import type { Decision, DecisionClaim, DecisionContent, DecisionList } from "./decisionModels";
import { dateTimeLabel } from "./format";
import { ErrorState, Loading, Notice } from "./UI";
import { useResource } from "./useResource";

function useDecisions<T>(path: string | null, interval = 0) {
  const resource = useResource<T>(path, interval);
  const { setData, refresh } = resource;
  useEffect(() => {
    const invalidate = () => { setData(undefined); refresh(); };
    window.addEventListener("omi:archive-changed", invalidate);
    window.addEventListener("omi:decisions-changed", invalidate);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("omi:archive-changed", invalidate);
      window.removeEventListener("omi:decisions-changed", invalidate);
      window.removeEventListener("focus", refresh);
    };
  }, [setData, refresh]);
  return resource;
}

function Claim({ label, claim, historical = false, opposing = false, actions }: { label: string; claim: DecisionClaim | null; historical?: boolean; opposing?: boolean; actions?: ReactNode }) {
  return <div className="decision-claim"><h4>{label}</h4>
    <p>{claim?.text || "Not stated"}{actions}</p>
    {claim?.evidence.map((anchor, index) => <DecisionEvidence key={index} anchor={anchor} historical={historical} opposing={opposing} />)}
  </div>;
}

function Contents({ content, historical = false, actions }: { content: DecisionContent; historical?: boolean; actions?: ReactNode }) {
  return <>
    <Claim label={t("Choice")} claim={content.choice} historical={historical} actions={actions} />
    <Claim label={t("Context")} claim={content.context} historical={historical} />
    <Claim label={t("Rationale")} claim={content.rationale} historical={historical} />
    {content.alternatives.map((claim, index) => <Claim key={index} label={`Explicitly discussed alternative ${index + 1}`} claim={claim} historical={historical} />)}
    {(content.contradictions || []).map((claim, index) => <Claim key={`opposing-${index}`} label={`Contradictory evidence for ${claim.field}`} claim={claim} historical={historical} opposing />)}
    {content.owner_notes ? <div className="decision-claim"><h4>{t("Owner notes")}</h4><p>{content.owner_notes}</p><p className="caption">{t("Owner interpretation; not recorded speech.")}</p></div> : null}
    {content.relationships.length ? <p className="caption">{t("Links:")}{" "}{content.relationships.map((link) => `${link.kind}: ${link.label}${link.target_id ? "" : " (local label)"}`).join(" · ")}</p> : null}
  </>;
}

function RevisionPicker({ current, selected, onSelect, timezone }: { current: Decision; selected?: Decision; onSelect: (decision?: Decision) => void; timezone: string }) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [page, setPage] = useState(0);
  const resource = useDecisions<DecisionList>(`/v1/decisions?${new URLSearchParams({ q: submitted, review_status: "confirmed", limit: "30", offset: String(page * 30) })}`);
  const candidates = resource.data?.items.filter((item) => item.id !== current.id && Date.parse(item.started_at) < Date.parse(current.started_at) && !item.successor_id && item.evidence_condition === "current" && item.content) || [];
  return <div className="decision-revision-picker">
    <p className="caption">{t("Choose the earlier choice that this recording explicitly replaces. Matching topics or later timestamps alone are insufficient.")}</p>
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); setSubmitted(query); setPage(0); onSelect(undefined); }}>
      <input value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} aria-label={t("Search earlier decisions across all days")} placeholder={t("Search decisions across all days")} />
      <button className="text-button">{t("Search")}</button>
    </form>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.loading ? <Loading /> : <label><span>{t("Earlier confirmed decision")}</span>
      <select value={selected?.id || ""} onChange={(event) => onSelect(candidates.find((item) => item.id === event.target.value))}>
        <option value="">{t("Choose a decision…")}</option>
        {candidates.map((item) => <option key={item.id} value={item.id}>{dateTimeLabel(item.started_at, timezone)} · {item.content?.choice.text}</option>)}
      </select>
    </label>}
    {resource.data && resource.data.total > 30 ? <div className="pagination"><button type="button" className="button small" disabled={page === 0} onClick={() => { setPage((value) => value - 1); onSelect(undefined); }}>{t("Previous matches")}</button><button type="button" className="button small" disabled={(page + 1) * 30 >= resource.data.total} onClick={() => { setPage((value) => value + 1); onSelect(undefined); }}>{t("Next matches")}</button></div> : null}
    {selected?.content ? <div className="decision-predecessor"><p className="caption">{t("Inspect the earlier choice and evidence:")}</p><Contents content={selected.content} /></div> : null}
  </div>;
}

export function DecisionDetail({ id, timezone, onClose, onNavigate }: { id: string; timezone: string; onClose: () => void; onNavigate: (id: string) => void }) {
  const resource = useDecisions<Decision>(`/v1/decisions/${id}`);
  const pendingCommand = useRef<{ key: string; id: string } | null>(null);
  const [editing, setEditing] = useState(false);
  const [revising, setRevising] = useState(false);
  const [previous, setPrevious] = useState<Decision>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decision = resource.data;
  useEffect(() => {
    const closeDraft = () => { setEditing(false); setPrevious(undefined); setRevising(false); };
    window.addEventListener("omi:archive-changed", closeDraft);
    return () => window.removeEventListener("omi:archive-changed", closeDraft);
  }, []);
  async function act(type: "confirm" | "dismiss" | "conflict" | "resolve" | "delete") {
    if (!decision) return;
    if (type === "delete" && !window.confirm("Permanently delete this decision and its saved content history? Source transcripts remain. This cannot be undone.")) return;
    setBusy(true); setError("");
    const key = JSON.stringify({ type, version: decision.version, predecessor: revising ? previous?.id : null, predecessorVersion: previous?.version });
    if (pendingCommand.current?.key !== key) pendingCommand.current = { key, id: newId() };
    try {
      if (type === "delete") await request(`/v1/decisions/${id}?expected_version=${decision.version}`, { method: "DELETE" });
      else await post(`/v1/decisions/${id}/events`, {
        command_id: pendingCommand.current.id, expected_version: decision.version, type,
        ...(type === "confirm" && revising && previous ? { predecessor_id: previous.id, predecessor_version: previous.version } : {}),
      });
      setPrevious(undefined); setRevising(false);
      window.dispatchEvent(new Event("omi:decisions-changed"));
      if (type === "delete") onClose();
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <article className="decision-detail">
    <div className="section-heading"><h3>{t("Decision record")}</h3><button className="text-button" onClick={onClose}>{t("Close record")}</button></div>
    {error || resource.error ? <ErrorState message={error || resource.error} retry={resource.refresh} /> : null}
    {!decision ? resource.loading ? <Loading /> : null : <>
      <p className="decision-meta">{dateTimeLabel(decision.started_at, timezone)} · {decision.display_state} · {decision.review_status} {t("· version")}{" "}{decision.version}</p>
      {decision.evidence_condition !== "current" ? <Notice tone="error">{t("Evidence is")}{" "}{decision.evidence_condition}. {decision.evidence_condition === "stale" ? t("Review corrected transcripts and explicitly reattach evidence before confirming again.") : t("Resolve the evidence issue before confirming.")}</Notice> : null}
      {decision.predecessor_id ? <button className="text-button" onClick={() => onNavigate(decision.predecessor_id!)}>{t("View earlier choice")}</button> : null}
      {decision.successor_id ? <button className="text-button" onClick={() => onNavigate(decision.successor_id!)}>{t("View revised choice")}</button> : null}
      {decision.content ? <Contents content={decision.content} historical={decision.evidence_condition !== "current"} actions={<DecisionFollowups key={`${decision.id}:${decision.version}`} decision={decision} timezone={timezone} />} /> : <p>{t("Content removed with its source evidence.")}</p>}
      {decision.content ? <>
        <p className="caption">{t("Confirmation means this record accurately represents the evidence. It does not turn a tentative choice into a final one.")}</p>
        <div className="button-row">
          <button className="button small" disabled={busy} onClick={() => setEditing(true)}>{t("Edit record")}</button>
          {decision.review_status !== "confirmed" && !decision.predecessor_id && !decision.successor_id ? <button className="button small" disabled={busy} onClick={() => void act("dismiss")}>{t("Dismiss proposal")}</button> : null}
          <button className="text-button" disabled={busy || decision.evidence_condition === "stale"} onClick={() => void act(decision.evidence_condition === "conflicted" ? "resolve" : "conflict")}>{decision.evidence_condition === "conflicted" ? t("Resolve conflict after review") : t("Flag contradictory evidence")}</button>
        </div>
        {decision.review_status !== "confirmed" ? <div className="decision-confirm">
          {!decision.predecessor_id && !decision.successor_id ? <label className="checkbox-label"><input type="checkbox" checked={revising} disabled={busy} onChange={(event) => { setRevising(event.target.checked); setPrevious(undefined); }} />{t("This explicitly revises an earlier decision")}</label> : null}
          {revising ? <RevisionPicker current={decision} selected={previous} onSelect={setPrevious} timezone={timezone} /> : null}
          <button className="button dark small" disabled={busy || decision.evidence_condition !== "current" || (revising && !previous)} onClick={() => void act("confirm")}>{busy ? t("Saving…") : revising ? t("Confirm and link revision") : t("Confirm record")}</button>
        </div> : null}
      </> : null}
      <details className="decision-history"><summary>{t("Record history")}</summary>
        {decision.history?.length ? decision.history.map((version) => <details key={version.version}>
          <summary>{t("Version")}{" "}{version.version} · {version.event} · {dateTimeLabel(version.created_at, timezone)}</summary>
          <p className="caption">{t("Historical interpretation; this is not the current record.")}</p>
          {version.content ? <Contents content={version.content} historical /> : <p>{t("Content removed.")}</p>}
        </details>) : <p className="caption">{t("No saved history.")}</p>}
      </details>
      <button className="text-button danger" disabled={busy} onClick={() => void act("delete")}>{t("Delete decision and history")}</button>
      {editing && decision.content ? <DecisionEditor decision={decision} onClose={() => setEditing(false)} onSaved={resource.refresh} /> : null}
    </>}
  </article>;
}

export function Decisions({ day, timezone, compact = false }: { day: string; timezone: string; compact?: boolean }) {
  const [allDays, setAllDays] = useState(false);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [review, setReview] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string>();
  const params = new URLSearchParams({ q: submitted, limit: "30", offset: String(page * 30), include_dismissed: review === "dismissed" ? "true" : "false" });
  if (!allDays) params.set("day", day);
  if (review) params.set("review_status", review);
  const resource = useDecisions<DecisionList>(`/v1/decisions?${params}`, 15_000);
  useEffect(() => {
    const invalidate = () => setSelected(undefined);
    window.addEventListener("omi:archive-changed", invalidate);
    return () => window.removeEventListener("omi:archive-changed", invalidate);
  }, []);
  const filters = <>
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); setSubmitted(query); setPage(0); }}><input maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} aria-label={t("Search decisions")} placeholder={t("Search decisions")} /><button className="text-button">{t("Search")}</button></form>
    <div className="button-row decision-filters">
      <label className="checkbox-label"><input type="checkbox" checked={allDays} onChange={(event) => { setAllDays(event.target.checked); setPage(0); }} />{t("Across all days")}</label>
      <label><span className="caption">{t("Review status")}{" "}</span><select value={review} onChange={(event) => { setReview(event.target.value); setPage(0); }} aria-label={t("Filter decision review status")}><option value="">{t("Active records")}</option><option value="proposed">{t("Proposed")}</option><option value="confirmed">{t("Confirmed")}</option><option value="dismissed">{t("Dismissed")}</option></select></label>
      <button className="text-button" onClick={resource.refresh} type="button">{t("Refresh")}</button>
    </div>
  </>;
  return <section className={`decisions-section ${compact ? "decisions-compact" : ""}`}>
    <div className="section-heading"><div><h2>{t("Decisions")}</h2><p className="section-description">{t("Found automatically from new transcripts. Review the evidence before confirming.")}</p></div></div>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {compact ? <details className="decision-search"><summary>{t("Search & filter")}{submitted || allDays || review ? t("· filters active") : ""}</summary>{filters}</details> : filters}
    {resource.loading && !resource.data ? <Loading>{t("Loading decisions…")}</Loading> : resource.data?.items.length ? <div className="decision-list">
      {resource.data.items.map((decision) => <button key={decision.id} className={`decision-row ${selected === decision.id ? "selected" : ""}`} onClick={() => setSelected(decision.id)}>
        <span>{decision.content?.choice.text || "Content removed"}</span><small>{dateTimeLabel(decision.started_at, timezone)} · {decision.display_state} · {decision.review_status}{decision.evidence_condition !== "current" ? ` · evidence ${decision.evidence_condition}` : ""}</small>
      </button>)}
      <p className="caption">{t("Showing")}{" "}{page * 30 + 1}–{page * 30 + resource.data.items.length} {t("of")}{" "}{resource.data.total} {t("records")}{allDays ? t("across all days") : t("for this day")}.</p>
    </div> : <div className={compact ? "decision-empty" : ""}><p className="caption">{compact && !submitted && !allDays && !review ? t("No decisions saved for this day.") : t("No matching decision records.")} <span>{t("Model proposals require your review before confirmation.")}</span></p></div>}
    {resource.data && resource.data.total > 30 ? <div className="pagination"><button className="button small" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>{t("Previous decisions")}</button><button className="button small" disabled={(page + 1) * 30 >= resource.data.total} onClick={() => setPage((value) => value + 1)}>{t("Next decisions")}</button></div> : null}
    {selected ? <DecisionDetail key={selected} id={selected} timezone={timezone} onClose={() => setSelected(undefined)} onNavigate={setSelected} /> : null}
  </section>;
}
