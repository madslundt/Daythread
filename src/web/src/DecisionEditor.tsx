import { t } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, post, request } from "./api";
import { blankDecisionContent, decisionAnchor } from "./decisionModels";
import type { Decision, DecisionClaim, DecisionContent, DecisionContradiction } from "./decisionModels";
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
    <label><span>{label} {t("statement")}{required ? t("(required)") : t("(leave empty if not stated)")}</span>
      <textarea rows={2} maxLength={4000} value={claim?.text || ""} required={required}
        onChange={(event) => onChange(event.target.value ? { text: event.target.value, evidence: claim?.evidence || [] } : null)} />
    </label>
    {claim ? <>
      <p className="caption">{t("Attached evidence:")}{" "}{claim.evidence.length ? `${claim.evidence.length} quotation(s)` : t("None — attach a quotation before saving.")}</p>
      {claim.evidence.map((anchor, index) => <blockquote key={index}>{anchor.quote}</blockquote>)}
      <details>
        <summary>{t("Choose or replace supporting quotation")}</summary>
        <label><span>{t("Source transcript")}</span><select value={source?.id || ""} onChange={(event) => { setSourceId(event.target.value); setQuote(""); }}>
          {sources.map((item) => <option key={item.id} value={item.id}>{item.started_at} · {item.id.slice(0, 8)}</option>)}
        </select></label>
        {source ? <p className="transcript-text decision-original">{source.text}</p> : <p className="caption">{t("No current source transcript available.")}</p>}
        <label><span>{t("Exact quotation from this transcript")}</span><textarea rows={3} maxLength={20000} value={quote} onChange={(event) => setQuote(event.target.value)} /></label>
        <button className="button small" type="button" onClick={attach}>{t("Use this quotation")}</button>
        <p className="caption">{t("This replaces this field’s attached quotations. Check that the passage supports the statement.")}</p>
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
  const [content, setContent] = useState<DecisionContent>(() => decision?.content ? { ...structuredClone(decision.content), contradictions: structuredClone(decision.content.contradictions || []) } : blankDecisionContent());
  const [state, setState] = useState<"tentative" | "final">(decision?.state || "tentative");
  const [sources, setSources] = useState<Source[]>(source ? [source] : []);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [additionalSourceId, setAdditionalSourceId] = useState("");
  const [addingSource, setAddingSource] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    const claims = decision?.content ? [decision.content.choice, decision.content.context, decision.content.rationale, ...decision.content.alternatives, ...(decision.content.contradictions || [])] : [];
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
    const claims = [content.choice, content.context, content.rationale, ...content.alternatives, ...content.contradictions].filter((value) => value !== null);
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
  async function addSource() {
    setAddingSource(true); setError("");
    try {
      const item = await request<Source>(`/v1/transcripts/${encodeURIComponent(additionalSourceId.trim())}`, { cache: "no-store" });
      if (item.state !== "done" || !item.text || !item.source_version) throw new Error("Choose a source with a current transcript.");
      setSources((items) => [...items.filter((source) => source.id !== item.id), item]);
      setAdditionalSourceId("");
    } catch (error) { setError(errorMessage(error)); }
    finally { setAddingSource(false); }
  }
  return <dialog ref={dialog} className="upload-dialog decision-dialog" onCancel={(event) => { if (busy) event.preventDefault(); else onClose(); }}>
    <form onSubmit={save}><fieldset className="decision-form-fields" disabled={busy}>
      <div className="section-heading"><h2>{decision ? t("Edit decision record") : t("Record a decision")}</h2>
        <button type="button" className="text-button" disabled={busy} onClick={onClose}>{t("Close")}</button></div>
      <p className="caption">{t("Record an explicit choice. Suggestions and disagreement alone do not establish a decision. Saving creates a proposal for review.")}</p>
      {loading ? <Loading>{t("Loading original evidence…")}</Loading> : null}
      <details><summary>{t("Add another source for context or opposing evidence")}</summary>
        <label><span>{t("Additional source UUID")}</span><input value={additionalSourceId} onChange={(event) => setAdditionalSourceId(event.target.value)} maxLength={36} /></label>
        <button type="button" className="button small" disabled={loading || addingSource || !additionalSourceId.trim()} onClick={() => void addSource()}>{addingSource ? t("Loading source…") : t("Load additional source")}</button>
      </details>
      <label className="decision-state"><span>{t("State expressed in the recording")}</span><select value={state} onChange={(event) => setState(event.target.value as "tentative" | "final")}>
        <option value="tentative">{t("Tentative")}</option><option value="final">{t("Final")}</option>
      </select></label>
      <ClaimEditor label={t("Choice")} required claim={content.choice} sources={sources} onChange={(choice) => setContent({ ...content, choice: choice || { text: "", evidence: [] } })} />
      <ClaimEditor label={t("Context")} claim={content.context} sources={sources} onChange={(context) => setContent({ ...content, context })} />
      <ClaimEditor label={t("Rationale")} claim={content.rationale} sources={sources} onChange={(rationale) => setContent({ ...content, rationale })} />
      {content.alternatives.map((alternative, index) => <div key={index}>
        <ClaimEditor label={`Alternative ${index + 1}`} claim={alternative} sources={sources} onChange={(claim) => setContent({ ...content, alternatives: content.alternatives.map((item, i) => i === index ? claim || { text: "", evidence: [] } : item) })} />
        <button type="button" className="text-button" onClick={() => setContent({ ...content, alternatives: content.alternatives.filter((_, i) => i !== index) })}>{t("Remove alternative")}</button>
      </div>)}
      <button className="text-button" type="button" onClick={() => setContent({ ...content, alternatives: [...content.alternatives, { text: "", evidence: [] }] })}>{t("Add explicitly discussed alternative")}</button>
      {content.contradictions.map((opposing, index) => <div key={`opposing-${index}`}>
        <label><span>{t("Contradicted field")}</span><select value={opposing.field} onChange={(event) => setContent({ ...content, contradictions: content.contradictions.map((item, i) => i === index ? { ...item, field: event.target.value as DecisionContradiction["field"] } : item) })}>
          <option value="choice">{t("Choice")}</option><option value="context">{t("Context")}</option><option value="rationale">{t("Rationale")}</option><option value="alternatives">{t("Alternatives")}</option>
        </select></label>
        <ClaimEditor label={`Opposing statement ${index + 1}`} claim={opposing} sources={sources} onChange={(claim) => setContent({ ...content, contradictions: content.contradictions.map((item, i) => i === index ? { ...(claim || { text: "", evidence: [] }), field: item.field } : item) })} />
        <button type="button" className="text-button" onClick={() => setContent({ ...content, contradictions: content.contradictions.filter((_, i) => i !== index) })}>{t("Remove opposing statement")}</button>
      </div>)}
      <button className="text-button" type="button" onClick={() => setContent({ ...content, contradictions: [...content.contradictions, { field: "choice", text: "", evidence: [] }] })}>{t("Add contradictory evidence")}</button>
      {content.contradictions.length ? <p className="caption">{t("Save the opposing passages, explain your resolution in owner notes, then explicitly resolve the conflict after review. The passages remain in the record.")}</p> : null}
      <label><span>{t("Owner notes — your interpretation, entered now")}</span><textarea rows={3} maxLength={8000} value={content.owner_notes} onChange={(event) => setContent({ ...content, owner_notes: event.target.value })} /></label>
      <p className="caption">{t("Use notes for your own explanation. They are not attributed to the recording.")}</p>
      <fieldset className="decision-claim-editor"><legend>{t("Optional links")}</legend>
        <p className="caption">{t("Local labels work before projects, people or commitments exist. A person label does not verify a speaker or assign a promise.")}</p>
        {content.relationships.map((link, index) => <div className="decision-link-editor" key={index}>
          <label><span>{t("Link type")}</span><select value={link.kind} disabled={Boolean(link.target_id)} onChange={(event) => setContent({ ...content, relationships: content.relationships.map((item, i) => i === index ? { ...item, kind: event.target.value as typeof link.kind } : item) })}>
            <option value="project">{t("Project")}</option><option value="person">{t("Person")}</option><option value="commitment">{t("Commitment")}</option><option value="meeting">{t("Meeting")}</option>
          </select></label>
          <label><span>{link.target_id ? t("Resolved link label") : t("Local label")}</span><input maxLength={200} required value={link.label} onChange={(event) => setContent({ ...content, relationships: content.relationships.map((item, i) => i === index ? { ...item, label: event.target.value } : item) })} /></label>
          <button type="button" className="text-button" onClick={() => setContent({ ...content, relationships: content.relationships.filter((_, i) => i !== index) })}>{t("Remove link")}</button>
        </div>)}
        <button type="button" className="text-button" onClick={() => setContent({ ...content, relationships: [...content.relationships, { kind: "project", label: "", target_id: null }] })}>{t("Add local label")}</button>
      </fieldset>
      {error ? <ErrorState message={error} /> : null}
      <div className="button-row"><button className="button dark" disabled={busy || loading || addingSource}>{busy ? t("Saving…") : t("Save proposal")}</button>
        <button type="button" className="button" disabled={busy} onClick={onClose}>{t("Cancel")}</button></div>
    </fieldset></form>
  </dialog>;
}
