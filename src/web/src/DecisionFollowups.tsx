import { useEffect, useMemo, useState } from "react";
import { errorMessage } from "./api";
import { canSuggestFollowups, DecisionFollowupRequests, followupLinkLabel } from "./decisionFollowupModels";
import type { CalendarFile, DecisionFollowup, DecisionSuggestions } from "./decisionFollowupModels";
import type { Decision } from "./decisionModels";

async function openFollowup(requests: DecisionFollowupRequests, suggestion: DecisionFollowup): Promise<boolean> {
  const file = await requests.run<CalendarFile>("calendar-export", suggestion);
  if (!file) return false;
  const url = URL.createObjectURL(new Blob([file.content], { type: "text/calendar;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = file.filename;
  link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return true;
}

export function DecisionFollowupAction({ suggestion, requests, timezone }: { suggestion: DecisionFollowup; requests: DecisionFollowupRequests; timezone: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [downloaded, setDownloaded] = useState(false);
  const event = suggestion.kind === "event";
  const label = followupLinkLabel(suggestion, timezone);
  const action = `${event ? "Add to calendar" : "Add reminder"}: ${suggestion.title}`;
  async function open() {
    setBusy(true); setError("");
    try { setDownloaded(await openFollowup(requests, suggestion)); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  if (!label) return null;
  return <span className="decision-inline-action">
    <button type="button" className="decision-action-link" disabled={busy || downloaded} aria-label={action}
      title={downloaded ? `File downloaded. Open it in your ${event ? "calendar" : "reminder"} app to add it.` : suggestion.reason ? `${action}. ${suggestion.reason}` : action} onClick={() => void open()}>
      {busy ? "Opening…" : label}{downloaded ? " ✓" : ""}
    </button>
    {error ? <span className="decision-link-error" role="alert">{error}</span> : null}
    {downloaded ? <span className="sr-only" role="status">File downloaded. Open the downloaded file in your {event ? "calendar" : "reminder"} app to add it.</span> : null}
  </span>;
}

export function DecisionFollowups({ decision, timezone }: { decision: Decision; timezone: string }) {
  const [result, setResult] = useState<DecisionSuggestions>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const requests = useMemo(() => new DecisionFollowupRequests(decision), [decision.id, decision.version, revision]);
  const eligible = canSuggestFollowups(decision);
  useEffect(() => {
    if (!eligible) return;
    const client = requests;
    setBusy(true); setError(""); setResult(undefined);
    client.run<DecisionSuggestions>("suggestions").then((response) => {
      if (response && response.decision_id === decision.id && response.decision_version === decision.version) setResult(response);
    }).catch((error) => { setError(errorMessage(error)); }).finally(() => { setBusy(false); });
    const invalidate = () => { client.cancel(); setResult(undefined); setBusy(false); };
    window.addEventListener("omi:archive-changed", invalidate);
    window.addEventListener("omi:decisions-changed", invalidate);
    return () => { client.cancel(); window.removeEventListener("omi:archive-changed", invalidate); window.removeEventListener("omi:decisions-changed", invalidate); };
  }, [decision.id, decision.version, eligible, requests]);
  if (!eligible) return null;
  const suggestions = result?.suggestions || [];
  const actions = suggestions.filter((item) => item.kind === "event" ? item.start_at : item.due_at);
  if (!busy && !error && !actions.length) return null;
  return <span className="decision-action-links" role="group" aria-label="Add this decision to your apps">
    {actions.map((suggestion) => <DecisionFollowupAction key={JSON.stringify(suggestion)} suggestion={suggestion} requests={requests} timezone={result?.timezone || timezone} />)}
    {busy ? <span className="caption" role="status">Finding dates…</span> : null}
    {error ? <span className="decision-link-error" title={error}>Suggestions unavailable. <button type="button" className="decision-action-link" onClick={() => setRevision((value) => value + 1)}>Try again</button></span> : null}
  </span>;
}
