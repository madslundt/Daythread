import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, request } from "./api";
import { DecisionDetail } from "./Decisions";
import type { DecisionExtraction, DecisionList } from "./decisionModels";
import { decisionMatchesTopic } from "./dayHighlights";
import { dateTimeLabel } from "./format";
import { t } from "./i18n";
import { profileScope } from "./profiles";
import type { RecapFocus } from "./recapItems";
import { statusLabel } from "./statusLabels";
import { ErrorState, Loading } from "./UI";
import { useHighlightList } from "./useHighlightList";

type ExtractionCommand = { request_id: string; day?: string; run_id?: string };

export function DecisionHighlights({ day, timezone, focus }: { day: string; timezone: string; focus?: RecapFocus }) {
  const resource = useHighlightList<DecisionList>(`/v1/decisions?${new URLSearchParams({ day, limit: "200", offset: "0" })}`, "omi:decisions-changed");
  const [selected, setSelected] = useState<string>();
  const [visible, setVisible] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<DecisionExtraction>();
  const active = useRef<AbortController | undefined>(undefined);
  const command = useRef<ExtractionCommand | undefined>(undefined);
  useEffect(() => {
    command.current = undefined; setSelected(undefined); setProgress(undefined); setError(""); setBusy(false);
    const invalidate = () => { active.current?.abort(); command.current = undefined; setSelected(undefined); setProgress(undefined); setBusy(false); };
    window.addEventListener("omi:archive-changed", invalidate);
    return () => { active.current?.abort(); window.removeEventListener("omi:archive-changed", invalidate); };
  }, [day]);
  async function find() {
    if (busy) return;
    const controller = new AbortController(), scope = profileScope(); active.current = controller;
    command.current ??= { request_id: newId(), day };
    setBusy(true); setError("");
    try {
      while (command.current && !controller.signal.aborted) {
        const result: DecisionExtraction = await request<DecisionExtraction>("/v1/decision-extractions", { method: "POST", signal: controller.signal, body: JSON.stringify(command.current) }, scope);
        if (active.current !== controller) return;
        setProgress(result); resource.refresh();
        if (result.status === "stale" || result.status === "removed") { command.current = undefined; throw new Error(t("Recorded evidence changed. Refresh this day before finding decisions again.")); }
        command.current = result.next_run_id ? { request_id: newId(), run_id: result.next_run_id } : undefined;
      }
      if (!controller.signal.aborted && !scope.signal.aborted) window.dispatchEvent(new Event("omi:decisions-changed"));
    } catch (error) {
      if (!controller.signal.aborted && !scope.signal.aborted) setError(errorMessage(error));
    } finally { if (active.current === controller) setBusy(false); }
  }
  const decisions = resource.items.filter((item) => item.content && !item.successor_id && item.review_status !== "dismissed" && item.evidence_condition !== "removed" && (!focus || decisionMatchesTopic(item, focus)));
  return <section className="highlight-group decision-highlights" aria-label={t("Decisions")}>
    <div className="highlight-group-heading"><h3>{t("Decisions")}</h3><span className="caption">{decisions.length}</span>
      <button type="button" className="text-button" disabled={busy} onClick={() => void find()}>{busy ? t("Finding decisions…") : t("Find decisions")}</button>
    </div>
    <p className="caption">{t("Recorded choices, including tentative decisions. Review the evidence before confirming.")}</p>
    {resource.error || error ? <ErrorState message={resource.error || error} retry={resource.refresh} /> : null}
    {resource.loading && !resource.data || resource.loadingMore ? <Loading>{t("Loading decisions…")}</Loading> : null}
    {progress && busy ? <p className="caption" role="status">{t("Reviewed {value0} of {value1} recordings.", { value0: progress.processed_sources, value1: progress.total_sources })}</p> : null}
    {progress && !busy && !error && progress.pending_chunks ? <p className="caption">{t("Some recordings are still awaiting transcription.")}</p> : null}
    {decisions.slice(0, visible).map((decision) => <article className="highlight-decision" key={decision.id}>
      <div><p>{decision.content!.choice.text}</p><p className="caption">{dateTimeLabel(decision.started_at, timezone)} · {statusLabel(decision.display_state)} · {statusLabel(decision.review_status)}</p>
        {decision.evidence_condition !== "current" ? <p className="caption action-point-warning">{statusLabel(decision.evidence_condition)}</p> : null}</div>
      <button className="button small" onClick={() => setSelected(decision.id)}>{t("Review decision")}</button>
    </article>)}
    {decisions.length > visible ? <button className="text-button highlight-show-more" onClick={() => setVisible((value) => value + 10)}>{t("Show more")}</button> : null}
    {!resource.loading && !resource.loadingMore && !resource.error && !decisions.length ? <p className="caption">{focus ? t("No decisions linked to an identified passage in this topic.") : t("No decisions saved for this day.")}</p> : null}
    {selected ? <DecisionDetail key={selected} id={selected} timezone={timezone} onClose={() => setSelected(undefined)} onNavigate={setSelected} /> : null}
  </section>;
}
