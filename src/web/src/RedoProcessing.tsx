import { useState } from "react";
import { errorMessage, post } from "./api";
import { t } from "./i18n";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";

export type RedoKind = "transcripts" | "summaries";
export type RedoBounds = { start: string | null; end: string | null; count: number };
export type RedoJob = {
  id: string; kind: RedoKind; start: string; end: string;
  state: "queued" | "transcribing" | "summarizing" | "completed" | "failed";
  transcript_total: number; transcript_completed: number;
  summary_total: number; summary_completed: number; error: string | null;
};
export type RedoAvailability = Record<RedoKind, RedoBounds> & { job: RedoJob | null };
type RedoRequest = { kind: RedoKind; start?: string; end?: string };

export function redoRequest(kind: RedoKind, scope: "all" | "range", start: string, end: string, bounds: RedoBounds): RedoRequest {
  if (!bounds.count || !bounds.start || !bounds.end) throw new Error(t("No available data to redo."));
  if (scope === "all") return { kind };
  if (!start || !end || start < bounds.start || end > bounds.end || start > end)
    throw new Error(t("Choose a date range within the available dates."));
  return { kind, start, end };
}

function running(job: RedoJob | null): boolean {
  return Boolean(job && !["completed", "failed"].includes(job.state));
}

export function RedoProgress({ job }: { job: RedoJob }) {
  const completed = job.transcript_completed + job.summary_completed;
  const total = job.transcript_total + job.summary_total;
  const labels = {
    queued: t("Redo queued."), transcribing: t("Redoing transcripts…"), summarizing: t("Redoing summaries…"),
    completed: t("Redo completed."), failed: t("Redo failed. Please try again."),
  };
  return <div className="redo-progress" role="status">
    <p>{labels[job.state]} {job.start} – {job.end}</p>
    {running(job) ? <progress max={Math.max(1, total)} value={completed} aria-label={t("Redo progress")} /> : null}
    {job.kind === "transcripts" ? <p className="caption">{t("{completed} of {total} transcripts redone", {
      completed: job.transcript_completed, total: job.transcript_total,
    })}</p> : null}
    <p className="caption">{t("{completed} of {total} days summarized", {
      completed: job.summary_completed, total: job.summary_total,
    })}</p>
    {job.error ? <ErrorState message={job.error} /> : null}
  </div>;
}

export function RedoForm({ availability, onStart }: {
  availability: RedoAvailability;
  onStart: (body: RedoRequest) => Promise<void>;
}) {
  const [kind, setKind] = useState<RedoKind>("transcripts");
  const [scope, setScope] = useState<"all" | "range">("all");
  const [range, setRange] = useState({ start: "", end: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bounds = availability[kind];
  const clamp = (day: string, fallback: string | null) => bounds.start && bounds.end
    ? [bounds.start, day || fallback || bounds.start, bounds.end].sort()[1] : "";
  const start = clamp(range.start, bounds.start);
  const end = clamp(range.end, bounds.end);
  const locked = busy || running(availability.job);
  const unavailable = !bounds.count || !bounds.start || !bounds.end;
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (locked || unavailable) return;
    setBusy(true); setError("");
    try { await onStart(redoRequest(kind, scope, start, end, bounds)); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  return <form className="redo-processing" onSubmit={submit} aria-busy={locked}>
    <fieldset disabled={locked}>
      <legend>{t("Redo transcripts & summaries")}</legend>
      <p className="settings-description muted">{t("Use your current models and settings to regenerate saved content.")}</p>
      <div className="settings-grid">
        <label className="setting-field" htmlFor="redo-kind">
          <span id="redo-kind-label">{t("What to redo")}</span>
          <select id="redo-kind" aria-labelledby="redo-kind-label" aria-describedby="redo-kind-hint" value={kind} onChange={(event) => { setKind(event.target.value as RedoKind); setError(""); }}>
            <option value="transcripts">{t("Transcripts and summaries")}</option>
            <option value="summaries">{t("Summaries only")}</option>
          </select>
          <small id="redo-kind-hint">{kind === "transcripts"
            ? t("Replaces transcripts using retained audio, then regenerates summaries. Transcript corrections will be replaced.")
            : t("Regenerates summaries from saved transcripts.")}</small>
        </label>
        <label className="setting-field" htmlFor="redo-scope">
          <span id="redo-scope-label">{t("Date range")}</span>
          <select id="redo-scope" aria-labelledby="redo-scope-label" value={scope} onChange={(event) => setScope(event.target.value as "all" | "range")}>
            <option value="all">{t("All available dates")}</option>
            <option value="range">{t("Date range")}</option>
          </select>
          {!unavailable ? <small>{t("Available: {start} – {end}", { start: bounds.start, end: bounds.end })}</small> : null}
        </label>
        {scope === "range" ? <>
          <label className="setting-field" htmlFor="redo-start">
            <span>{t("Start date")}</span>
            <input id="redo-start" type="date" required disabled={unavailable} min={bounds.start || undefined} max={end || bounds.end || undefined}
              value={start} onChange={(event) => setRange({ start: event.target.value, end })} />
          </label>
          <label className="setting-field" htmlFor="redo-end">
            <span>{t("End date")}</span>
            <input id="redo-end" type="date" required disabled={unavailable} min={start || bounds.start || undefined} max={bounds.end || undefined}
              value={end} onChange={(event) => setRange({ start, end: event.target.value })} />
          </label>
        </> : null}
      </div>
      {unavailable ? <p className="caption">{kind === "transcripts"
        ? t("No recordings with retained audio are available to transcribe again.")
        : t("No transcripts or summaries are available to summarize again.")}</p> : null}
      {error ? <ErrorState message={error} /> : null}
      <button type="submit" className="button" disabled={locked || unavailable || (scope === "range" && start > end)}>
        {busy ? t("Starting redo…") : kind === "transcripts" ? t("Redo transcripts and summaries") : t("Redo summaries")}
      </button>
    </fieldset>
    {availability.job ? <RedoProgress job={availability.job} /> : null}
  </form>;
}

export function RedoProcessing({ profileSelected }: { profileSelected: boolean }) {
  const resource = useResource<RedoAvailability>(profileSelected ? "/v1/reprocessing" : null, 5000);
  return <section aria-label={t("Redo transcripts & summaries")}>
    {!profileSelected ? <p className="caption">{t("Choose a profile in Settings → Profiles first.")}</p> : null}
    {resource.data ? <RedoForm availability={resource.data} onStart={async (body) => {
      const job = await post<RedoJob>("/v1/reprocessing", body);
      resource.setData((current) => current ? { ...current, job } : current);
      resource.refresh();
    }} /> : resource.loading ? <Loading /> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
  </section>;
}
