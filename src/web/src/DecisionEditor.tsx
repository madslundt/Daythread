import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, post, request } from "./api";
import { blankDecisionContent, decisionAnchor } from "./decisionModels";
import type { Decision, DecisionClaim, DecisionContent } from "./decisionModels";
import type { Source } from "./types";
import { ErrorState, Loading } from "./UI";

function ClaimEditor({ label, claim, sources, onChange, required = false }: {
  label: string; claim: DecisionClaim | null; sources: Source[];
  onChange: (claim: DecisionClaim | null) => void; required?: boolean;
}) {
  const [sourceId, setSourceId] = useState(claim?.evidence[0]?.source_id || sources[0]?.id || "");
  const [quote, setQuote] = useState(claim?.evidence[0]?.quote || "");
  const [error, setError] = useState("");
  const source = sources.find((item) => item.id === sourceId) || sources[0];
  function attach() {
    try {
      if (!source) throw new Error("The source is unavailable. Reload before attaching evidence.");
      onChange({ text: claim?.text || "", evidence: [decisionAnchor(source, quote)] });
      setError("");
    } catch (error) { setError(errorMessage(error)); }
  }
  return <fieldset className="decision-claim-editor">
    <legend>{label}</legend>
    <label><span>{label} statement{required ? " (required)" : " (leave empty if not stated)"}</span>
      <textarea rows={2} maxLength={4000} value={claim?.text || ""} required={required}
        onChange={(event) => onChange(event.target.value ? { text: event.target.value, evidence: claim?.evidence || [] } : null)} />
    </label>
    {claim ? <>
      <p className="caption">Attached evidence: {claim.evidence.length ? `${claim.evidence.length} quotation(s)` : "None — attach a quotation before saving."}</p>
      {claim.evidence.map((anchor, index) => <blockquote key={index}>{anchor.quote}</blockquote>)}
      <details>
        <summary>Choose or replace supporting quotation</summary>
        <label><span>Source transcript</span><select value={source?.id || ""} onChange={(event) => { setSourceId(event.target.value); setQuote(""); }}>
          {sources.map((item) => <option key={item.id} value={item.id}>{item.started_at} · {item.id.slice(0, 8)}</option>)}
        </select></label>
        {source ? <p className="transcript-text decision-original">{source.text}</p> : <p className="caption">No current source transcript available.</p>}
        <label><span>Exact quotation from this transcript</span><textarea rows={3} maxLength={20000} value={quote} onChange={(event) => setQuote(event.target.value)} /></label>
        <button className="button small" type="button" onClick={attach}>Use this quotation</button>
        <p className="caption">This replaces this field’s attached quotations. Check that the passage supports the statement.</p>
        {error ? <ErrorState message={error} /> : null}
      </details>
    </> : null}
  </fieldset>;
}

export function DecisionEditor({ decision, source, onClose, onSaved }: {
  decision?: Decision; source?: Source; onClose: () => void; onSaved: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const pendingCommand = useRef<{ key: string; id: string } | null>(null);
  const [content, setContent] = useState<DecisionContent>(() => decision?.content ? structuredClone(decision.content) : blankDecisionContent());
  const [state, setState] = useState<"tentative" | "final">(decision?.state || "tentative");
  const [sources, setSources] = useState<Source[]>(source ? [source] : []);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    const claims = decision?.content ? [decision.content.choice, decision.content.context, decision.content.rationale, ...decision.content.alternatives] : [];
    const ids = source ? [source.id] : [...new Set(claims.flatMap((claim) => claim?.evidence.map((anchor) => anchor.source_id) || []))];
    const controller = new AbortController();
    Promise.all(ids.map((id) => request<Source>(`/v1/transcripts/${encodeURIComponent(id)}`, { signal: controller.signal, cache: "no-store" })))
      .then((items) => { if (!controller.signal.aborted) setSources(items); })
      .catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [decision, source]);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    const claims = [content.choice, content.context, content.rationale, ...content.alternatives].filter((value) => value !== null);
    if (!content.choice.text.trim() || claims.some((claim) => !claim.text.trim() || !claim.evidence.length)) {
      setError("Every recorded statement needs a supporting quotation. Leave unstated context or rationale empty."); return;
    }
    if (claims.some((claim) => claim.evidence.some((anchor) => sources.find((item) => item.id === anchor.source_id)?.source_version !== anchor.source_version))) {
      setError("Evidence has changed or is unavailable. Explicitly attach quotations from the current transcript before saving."); return;
    }
    setBusy(true);
    const key = JSON.stringify({ version: decision?.version, state, content });
    if (pendingCommand.current?.key !== key) pendingCommand.current = { key, id: newId() };
    const command_id = pendingCommand.current.id;
    try {
      if (decision) await post(`/v1/decisions/${decision.id}/events`, { command_id, expected_version: decision.version, type: "edit", state, content });
      else await post("/v1/decisions", { command_id, state, content });
      window.dispatchEvent(new Event("omi:decisions-changed"));
      onSaved(); onClose();
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="upload-dialog decision-dialog" onCancel={(event) => { if (busy) event.preventDefault(); else onClose(); }}>
    <form onSubmit={save}><fieldset className="decision-form-fields" disabled={busy}>
      <div className="section-heading"><h2>{decision ? "Edit decision record" : "Record a decision"}</h2>
        <button type="button" className="text-button" disabled={busy} onClick={onClose}>Close</button></div>
      <p className="caption">Record an explicit choice. Suggestions and disagreement alone do not establish a decision. Saving creates a proposal for review.</p>
      {loading ? <Loading>Loading original evidence…</Loading> : null}
      <label className="decision-state"><span>State expressed in the recording</span><select value={state} onChange={(event) => setState(event.target.value as "tentative" | "final")}>
        <option value="tentative">Tentative</option><option value="final">Final</option>
      </select></label>
      <ClaimEditor label="Choice" required claim={content.choice} sources={sources} onChange={(choice) => setContent({ ...content, choice: choice || { text: "", evidence: [] } })} />
      <ClaimEditor label="Context" claim={content.context} sources={sources} onChange={(context) => setContent({ ...content, context })} />
      <ClaimEditor label="Rationale" claim={content.rationale} sources={sources} onChange={(rationale) => setContent({ ...content, rationale })} />
      {content.alternatives.map((alternative, index) => <div key={index}>
        <ClaimEditor label={`Alternative ${index + 1}`} claim={alternative} sources={sources} onChange={(claim) => setContent({ ...content, alternatives: content.alternatives.map((item, i) => i === index ? claim || { text: "", evidence: [] } : item) })} />
        <button type="button" className="text-button" onClick={() => setContent({ ...content, alternatives: content.alternatives.filter((_, i) => i !== index) })}>Remove alternative</button>
      </div>)}
      <button className="text-button" type="button" onClick={() => setContent({ ...content, alternatives: [...content.alternatives, { text: "", evidence: [] }] })}>Add explicitly discussed alternative</button>
      <label><span>Owner notes — your interpretation, entered now</span><textarea rows={3} maxLength={8000} value={content.owner_notes} onChange={(event) => setContent({ ...content, owner_notes: event.target.value })} /></label>
      <p className="caption">Use notes for your own explanation. They are not attributed to the recording.</p>
      <fieldset className="decision-claim-editor"><legend>Optional links</legend>
        <p className="caption">Local labels work before projects, people or commitments exist. A person label does not verify a speaker or assign a promise.</p>
        {content.relationships.map((link, index) => <div className="decision-link-editor" key={index}>
          <label><span>Link type</span><select value={link.kind} disabled={Boolean(link.target_id)} onChange={(event) => setContent({ ...content, relationships: content.relationships.map((item, i) => i === index ? { ...item, kind: event.target.value as typeof link.kind } : item) })}>
            <option value="project">Project</option><option value="person">Person</option><option value="commitment">Commitment</option><option value="meeting">Meeting</option>
          </select></label>
          <label><span>{link.target_id ? "Resolved link label" : "Local label"}</span><input maxLength={200} required value={link.label} onChange={(event) => setContent({ ...content, relationships: content.relationships.map((item, i) => i === index ? { ...item, label: event.target.value } : item) })} /></label>
          <button type="button" className="text-button" onClick={() => setContent({ ...content, relationships: content.relationships.filter((_, i) => i !== index) })}>Remove link</button>
        </div>)}
        <button type="button" className="text-button" onClick={() => setContent({ ...content, relationships: [...content.relationships, { kind: "project", label: "", target_id: null }] })}>Add local label</button>
      </fieldset>
      {error ? <ErrorState message={error} /> : null}
      <div className="button-row"><button className="button dark" disabled={busy || loading}>{busy ? "Saving…" : "Save proposal"}</button>
        <button type="button" className="button" disabled={busy} onClick={onClose}>Cancel</button></div>
    </fieldset></form>
  </dialog>;
}
