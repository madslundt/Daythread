import { useEffect, useMemo, useState } from "react";
import { errorMessage } from "./api";
import { DecisionFollowupRequests, followupLinkLabel, type CalendarFile, type DecisionFollowup } from "./decisionFollowupModels";
import { t } from "./i18n";
import type { InboxItem } from "./inboxTypes";
import { actionableKind } from "./dayHighlights";
import { ErrorState } from "./UI";

type ActionSuggestions = { item_id: string; item_version: number; timezone: string; suggestions: DecisionFollowup[] };

export function ActionFollowups({ item, timezone, disabled = false }: { item: InboxItem; timezone: string; disabled?: boolean }) {
  const requests = useMemo(() => new DecisionFollowupRequests(item, "inbox"), [item.id, item.version]);
  const [result, setResult] = useState<ActionSuggestions>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const invalidate = () => { requests.cancel(); setResult(undefined); };
    window.addEventListener("omi:archive-changed", invalidate);
    window.addEventListener("omi:inbox-changed", invalidate);
    return () => {
      requests.cancel(); window.removeEventListener("omi:archive-changed", invalidate);
      window.removeEventListener("omi:inbox-changed", invalidate);
    };
  }, [requests]);
  if (item.health !== "current" || item.status !== "pending" || !actionableKind(item.kind)) return null;
  async function suggest() {
    setBusy(true); setError("");
    try {
      const response = await requests.run<ActionSuggestions>("suggestions");
      if (response?.item_id === item.id && response.item_version === item.version) setResult(response);
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <section className="action-followups">
    <h3>{t("Calendar & reminders")}</h3>
    <p className="caption">{t("Save your review first. Then choose a suggested event or reminder to add to your apps.")}</p>
    <button type="button" className="button" disabled={busy || disabled} onClick={() => void suggest()}>{busy ? t("Finding follow-ups…") : t("Find calendar & reminder actions")}</button>
    {error ? <ErrorState message={error} /> : null}
    {!disabled ? result?.suggestions.map((suggestion, index) => <ActionFollowup key={`${item.version}:${index}`} suggestion={suggestion} requests={requests} timezone={result.timezone || timezone} />) : null}
    {result && !result.suggestions.length ? <p className="caption">{t("No calendar or reminder suggestions found in the supporting evidence.")}</p> : null}
  </section>;
}

export function ActionFollowup({ suggestion, requests, timezone }: { suggestion: DecisionFollowup; requests: DecisionFollowupRequests; timezone: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [downloaded, setDownloaded] = useState(false);
  const event = suggestion.kind === "event";
  const nearby = suggestion.kind === "reminder" && suggestion.location_trigger && suggestion.location.trim();
  const date = followupLinkLabel(suggestion, timezone);
  async function download() {
    setBusy(true); setError("");
    try {
      const file = await requests.run<CalendarFile>("calendar-export", suggestion);
      if (file) {
        const url = URL.createObjectURL(new Blob([file.content], { type: "text/calendar;charset=utf-8" }));
        const link = document.createElement("a"); link.href = url; link.download = file.filename; link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000); setDownloaded(true);
      }
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <div className="action-followup">
    <p><strong>{suggestion.title}</strong></p>
    {date || suggestion.location ? <p className="caption">{[date, suggestion.location].filter(Boolean).join(" · ")}</p> : null}
    {suggestion.reason ? <p className="caption">{suggestion.reason}</p> : null}
    {nearby ? <p className="caption">{t("Open on iPhone to set a nearby reminder")}</p> : event && !suggestion.start_at
      ? <p className="caption">{t("No event date was recorded. Open on iPhone to choose a date in your calendar.")}</p>
      : <button type="button" className="button small" disabled={busy || downloaded} onClick={() => void download()}>
        {busy ? t("Opening…") : event ? t("Add to calendar") : t("Add reminder")}
      </button>}
    {downloaded ? <p className="caption" role="status">{t("File downloaded. Open it in your calendar or reminder app to finish adding it.")}</p> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
  </div>;
}
