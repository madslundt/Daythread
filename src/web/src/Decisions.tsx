import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, post, request } from "./api";
import { DecisionEvidence } from "./DecisionEvidence";
import { DecisionEditor } from "./DecisionEditor";
import type { Decision, DecisionClaim, DecisionContent, DecisionExtraction, DecisionList } from "./decisionModels";
import { dateTimeLabel } from "./format";
import { ErrorState, Loading, Notice } from "./UI";
import { useResource } from "./useResource";

function useDecisions<T>(path: string | null) {
  const resource = useResource<T>(path);
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

function Claim({ label, claim, historical = false, opposing = false }: { label: string; claim: DecisionClaim | null; historical?: boolean; opposing?: boolean }) {
  return <div className="decision-claim"><h4>{label}</h4>
    <p>{claim?.text || "Not stated"}</p>
    {claim?.evidence.map((anchor, index) => <DecisionEvidence key={index} anchor={anchor} historical={historical} opposing={opposing} />)}
  </div>;
}

function Contents({ content, historical = false }: { content: DecisionContent; historical?: boolean }) {
  return <>
    <Claim label="Choice" claim={content.choice} historical={historical} />
    <Claim label="Context" claim={content.context} historical={historical} />
    <Claim label="Rationale" claim={content.rationale} historical={historical} />
    {content.alternatives.map((claim, index) => <Claim key={index} label={`Explicitly discussed alternative ${index + 1}`} claim={claim} historical={historical} />)}
    {(content.contradictions || []).map((claim, index) => <Claim key={`opposing-${index}`} label={`Contradictory evidence for ${claim.field}`} claim={claim} historical={historical} opposing />)}
    {content.owner_notes ? <div className="decision-claim"><h4>Owner notes</h4><p>{content.owner_notes}</p><p className="caption">Owner interpretation; not recorded speech.</p></div> : null}
    {content.relationships.length ? <p className="caption">Links: {content.relationships.map((link) => `${link.kind}: ${link.label}${link.target_id ? "" : " (local label)"}`).join(" · ")}</p> : null}
  </>;
}

function RevisionPicker({ current, selected, onSelect, timezone }: { current: Decision; selected?: Decision; onSelect: (decision?: Decision) => void; timezone: string }) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [page, setPage] = useState(0);
  const resource = useDecisions<DecisionList>(`/v1/decisions?${new URLSearchParams({ q: submitted, review_status: "confirmed", limit: "30", offset: String(page * 30) })}`);
  const candidates = resource.data?.items.filter((item) => item.id !== current.id && Date.parse(item.started_at) < Date.parse(current.started_at) && !item.successor_id && item.evidence_condition === "current" && item.content) || [];
  return <div className="decision-revision-picker">
    <p className="caption">Choose the earlier choice that this recording explicitly replaces. Matching topics or later timestamps alone are insufficient.</p>
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); setSubmitted(query); setPage(0); onSelect(undefined); }}>
      <input value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} aria-label="Search earlier decisions across all days" placeholder="Search decisions across all days" />
      <button className="text-button">Search</button>
    </form>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.loading ? <Loading /> : <label><span>Earlier confirmed decision</span>
      <select value={selected?.id || ""} onChange={(event) => onSelect(candidates.find((item) => item.id === event.target.value))}>
        <option value="">Choose a decision…</option>
        {candidates.map((item) => <option key={item.id} value={item.id}>{dateTimeLabel(item.started_at, timezone)} · {item.content?.choice.text}</option>)}
      </select>
    </label>}
    {resource.data && resource.data.total > 30 ? <div className="pagination"><button type="button" className="button small" disabled={page === 0} onClick={() => { setPage((value) => value - 1); onSelect(undefined); }}>Previous matches</button><button type="button" className="button small" disabled={(page + 1) * 30 >= resource.data.total} onClick={() => { setPage((value) => value + 1); onSelect(undefined); }}>Next matches</button></div> : null}
    {selected?.content ? <div className="decision-predecessor"><p className="caption">Inspect the earlier choice and evidence:</p><Contents content={selected.content} /></div> : null}
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
    <div className="section-heading"><h3>Decision record</h3><button className="text-button" onClick={onClose}>Close record</button></div>
    {error || resource.error ? <ErrorState message={error || resource.error} retry={resource.refresh} /> : null}
    {!decision ? resource.loading ? <Loading /> : null : <>
      <p className="decision-meta">{dateTimeLabel(decision.started_at, timezone)} · {decision.display_state} · {decision.review_status} · version {decision.version}</p>
      {decision.evidence_condition !== "current" ? <Notice tone="error">Evidence is {decision.evidence_condition}. {decision.evidence_condition === "stale" ? "Review corrected transcripts and explicitly reattach evidence before confirming again." : "Resolve the evidence issue before confirming."}</Notice> : null}
      {decision.predecessor_id ? <button className="text-button" onClick={() => onNavigate(decision.predecessor_id!)}>View earlier choice</button> : null}
      {decision.successor_id ? <button className="text-button" onClick={() => onNavigate(decision.successor_id!)}>View revised choice</button> : null}
      {decision.content ? <Contents content={decision.content} historical={decision.evidence_condition !== "current"} /> : <p>Content removed with its source evidence.</p>}
      {decision.content ? <>
        <p className="caption">Confirmation means this record accurately represents the evidence. It does not turn a tentative choice into a final one.</p>
        <div className="button-row">
          <button className="button small" disabled={busy} onClick={() => setEditing(true)}>Edit record</button>
          {decision.review_status !== "confirmed" && !decision.predecessor_id && !decision.successor_id ? <button className="button small" disabled={busy} onClick={() => void act("dismiss")}>Dismiss proposal</button> : null}
          <button className="text-button" disabled={busy || decision.evidence_condition === "stale"} onClick={() => void act(decision.evidence_condition === "conflicted" ? "resolve" : "conflict")}>{decision.evidence_condition === "conflicted" ? "Resolve conflict after review" : "Flag contradictory evidence"}</button>
        </div>
        {decision.review_status !== "confirmed" ? <div className="decision-confirm">
          {!decision.predecessor_id && !decision.successor_id ? <label className="checkbox-label"><input type="checkbox" checked={revising} disabled={busy} onChange={(event) => { setRevising(event.target.checked); setPrevious(undefined); }} />This explicitly revises an earlier decision</label> : null}
          {revising ? <RevisionPicker current={decision} selected={previous} onSelect={setPrevious} timezone={timezone} /> : null}
          <button className="button dark small" disabled={busy || decision.evidence_condition !== "current" || (revising && !previous)} onClick={() => void act("confirm")}>{busy ? "Saving…" : revising ? "Confirm and link revision" : "Confirm record"}</button>
        </div> : null}
      </> : null}
      <details className="decision-history"><summary>Record history</summary>
        {decision.history?.length ? decision.history.map((version) => <details key={version.version}>
          <summary>Version {version.version} · {version.event} · {dateTimeLabel(version.created_at, timezone)}</summary>
          <p className="caption">Historical interpretation; this is not the current record.</p>
          {version.content ? <Contents content={version.content} historical /> : <p>Content removed.</p>}
        </details>) : <p className="caption">No saved history.</p>}
      </details>
      <button className="text-button danger" disabled={busy} onClick={() => void act("delete")}>Delete decision and history</button>
      {editing && decision.content ? <DecisionEditor decision={decision} onClose={() => setEditing(false)} onSaved={resource.refresh} /> : null}
    </>}
  </article>;
}

export function Decisions({ day, timezone }: { day: string; timezone: string }) {
  const [allDays, setAllDays] = useState(false);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [review, setReview] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [extraction, setExtraction] = useState<DecisionExtraction>();
  const pendingExtraction = useRef<{ key: string; id: string } | null>(null);
  const sourceGeneration = useRef(0);
  const params = new URLSearchParams({ q: submitted, limit: "30", offset: String(page * 30), include_dismissed: review === "dismissed" ? "true" : "false" });
  if (!allDays) params.set("day", day);
  if (review) params.set("review_status", review);
  const resource = useDecisions<DecisionList>(`/v1/decisions?${params}`);
  useEffect(() => {
    const invalidate = () => { sourceGeneration.current += 1; setExtraction(undefined); setSelected(undefined); };
    window.addEventListener("omi:archive-changed", invalidate);
    return () => window.removeEventListener("omi:archive-changed", invalidate);
  }, []);
  async function extract() {
    setBusy(true); setError("");
    const generation = sourceGeneration.current;
    const key = JSON.stringify({ day, run: extraction?.next_run_id });
    if (pendingExtraction.current?.key !== key) pendingExtraction.current = { key, id: newId() };
    try {
      const result = await post<DecisionExtraction>("/v1/decision-extractions", { request_id: pendingExtraction.current.id, ...(extraction?.next_run_id ? { run_id: extraction.next_run_id } : { day }) });
      if (generation !== sourceGeneration.current) return;
      pendingExtraction.current = null;
      setExtraction(result);
      window.dispatchEvent(new Event("omi:decisions-changed"));
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <section className="decisions-section">
    <div className="section-heading"><h2>Decisions</h2><button className="button" disabled={busy} onClick={() => void extract()}>{busy ? "Finding decisions…" : extraction?.next_run_id ? "Continue finding decisions" : "Find decisions for this day"}</button></div>
    <p className="caption">Evidence-backed records, reviewed by you. Create one from a source recording, or ask the local model for proposals.</p>
    {extraction ? <Notice>
      Processed {extraction.processed_sources} of {extraction.total_sources} transcript sources. {extraction.processed_source_offset ? `Also processed ${extraction.processed_source_offset.toLocaleString()} characters of the next source. ` : ""}{extraction.pending_chunks} recording(s) pending transcription; {extraction.gaps} capture gap(s).
      {extraction.suppressed_sources ? ` ${extraction.suppressed_sources} previously removed source(s) excluded from automatic extraction. Explicit manual records remain available.` : ""}
      {extraction.next_run_id ? " More sources remain; continue to process the next batch." : extraction.status === "complete" ? " Available transcripts processed. Missing audio or pending transcripts can leave decisions undiscovered." : ` Extraction is ${extraction.status}; start again with current evidence.`}
    </Notice> : null}
    {error || resource.error ? <ErrorState message={error || resource.error} retry={resource.refresh} /> : null}
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); setSubmitted(query); setPage(0); }}><input maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Search decisions" placeholder="Search decisions" /><button className="text-button">Search</button></form>
    <div className="button-row decision-filters">
      <label className="checkbox-label"><input type="checkbox" checked={allDays} onChange={(event) => { setAllDays(event.target.checked); setPage(0); }} />Across all days</label>
      <label><span className="caption">Review status </span><select value={review} onChange={(event) => { setReview(event.target.value); setPage(0); }} aria-label="Filter decision review status"><option value="">Active records</option><option value="proposed">Proposed</option><option value="confirmed">Confirmed</option><option value="dismissed">Dismissed</option></select></label>
      <button className="text-button" onClick={resource.refresh} type="button">Refresh</button>
    </div>
    {resource.loading && !resource.data ? <Loading>Loading decisions…</Loading> : resource.data?.items.length ? <div className="decision-list">
      {resource.data.items.map((decision) => <button key={decision.id} className={`decision-row ${selected === decision.id ? "selected" : ""}`} onClick={() => setSelected(decision.id)}>
        <span>{decision.content?.choice.text || "Content removed"}</span><small>{dateTimeLabel(decision.started_at, timezone)} · {decision.display_state} · {decision.review_status}{decision.evidence_condition !== "current" ? ` · evidence ${decision.evidence_condition}` : ""}</small>
      </button>)}
      <p className="caption">Showing {page * 30 + 1}–{page * 30 + resource.data.items.length} of {resource.data.total} records{allDays ? " across all days" : " for this day"}.</p>
    </div> : <p className="caption">No matching decision records. Model proposals require your review before confirmation.</p>}
    {resource.data && resource.data.total > 30 ? <div className="pagination"><button className="button small" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>Previous decisions</button><button className="button small" disabled={(page + 1) * 30 >= resource.data.total} onClick={() => setPage((value) => value + 1)}>Next decisions</button></div> : null}
    {selected ? <DecisionDetail key={selected} id={selected} timezone={timezone} onClose={() => setSelected(undefined)} onNavigate={setSelected} /> : null}
  </section>;
}
