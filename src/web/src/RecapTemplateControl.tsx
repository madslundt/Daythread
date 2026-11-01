import { useEffect, useRef, useState } from "react";
import { errorMessage, newId, request } from "./api";
import { t } from "./i18n";
import { ErrorState } from "./UI";
import { profileScope } from "./profiles";
import "./recapTemplates.css";

type Choice = { template: string; instructions: string };
type Catalog = { presets: { id: string; label: string }[]; selection: Choice };
type Job = { id: string; state: string; error?: string };

export function RecapTemplateControl({ day, onGenerated }: { day: string; onGenerated: () => void }) {
  const scope = profileScope();
  const identity = `${scope.id}:${day}`;
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<Catalog>();
  const [choice, setChoice] = useState<Choice>({ template: "general", instructions: "" });
  const [makeDefault, setMakeDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const operation = useRef<AbortController | undefined>(undefined);
  const requestID = useRef<string | undefined>(undefined);
  useEffect(() => {
    setCatalog(undefined); setBusy(false); setError(""); setMakeDefault(false);
    requestID.current = undefined;
    return () => operation.current?.abort();
  }, [day, scope.id, scope.signal]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const key = identity;
    setError("");
    request<Catalog>(`/v1/summary-templates?day=${encodeURIComponent(day)}`, { signal: controller.signal }, scope)
      .then((value) => {
        if (controller.signal.aborted || scope.signal.aborted || key !== currentIdentity.current) return;
        setCatalog(value); setChoice(value.selection);
      }).catch((cause) => {
        if (!controller.signal.aborted && !scope.signal.aborted && key === currentIdentity.current)
          setError(errorMessage(cause));
      });
    return () => controller.abort();
  }, [day, open, reload, scope.id, scope.signal]);

  function edit(value: Choice) {
    setChoice(value); requestID.current = undefined;
  }
  async function generate() {
    if (busy || !catalog) return;
    const controller = new AbortController();
    const scope = profileScope();
    const key = identity;
    operation.current = controller;
    requestID.current ??= newId();
    setBusy(true); setError("");
    try {
      if (makeDefault) {
        await request("/v1/settings", { method: "PATCH", signal: controller.signal,
          body: JSON.stringify({ summary_template: choice.template, summary_template_instructions: choice.instructions }) }, scope);
        window.dispatchEvent(new Event("omi:settings-changed"));
      }
      scope.signal.throwIfAborted();
      if (key !== currentIdentity.current) return;
      let job = await request<Job>("/v1/jobs", { method: "POST", signal: controller.signal,
        body: JSON.stringify({ id: requestID.current, kind: "recap", parameters: { day, ...choice } }) }, scope);
      while (job.state === "queued" || job.state === "running") {
        await new Promise<void>((resolve) => setTimeout(resolve, 1000));
        controller.signal.throwIfAborted();
        job = await request<Job>(`/v1/jobs/${job.id}`, { signal: controller.signal }, scope);
      }
      if (controller.signal.aborted || scope.signal.aborted || key !== currentIdentity.current) return;
      if (job.state !== "completed") {
        requestID.current = undefined;
        throw new Error(job.error || t("The recap could not be generated."));
      }
      requestID.current = undefined; onGenerated();
      window.dispatchEvent(new Event("omi:archive-changed"));
    } catch (cause) {
      if (!controller.signal.aborted && !scope.signal.aborted && key === currentIdentity.current)
        setError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && !scope.signal.aborted && key === currentIdentity.current)
        setBusy(false);
    }
  }

  return <details className="recap-sources" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{t("Summary template")}</summary>
    {open ? <div className="recap-template-fields">
      <p className="caption">{t("Choose how this day is summarized. Original recordings and citations stay available.")}</p>
      {catalog ? <>
        <label>{t("Summary template")}<select aria-label={t("Summary template")} value={choice.template} disabled={busy}
          onChange={(event) => edit({ ...choice, template: event.target.value })}>
          {catalog.presets.map((preset) => <option key={preset.id} value={preset.id}>{t(preset.label)}</option>)}
        </select></label>
        <label>{t("Presentation preferences")}<textarea aria-label={t("Presentation preferences")} value={choice.instructions} maxLength={2000} rows={3}
          disabled={busy} onChange={(event) => edit({ ...choice, instructions: event.target.value })} /></label>
        <label className="checkbox-label"><input type="checkbox" checked={makeDefault} disabled={busy}
          onChange={(event) => setMakeDefault(event.target.checked)} />{t("Use for new days")}</label>
        <button className="button" disabled={busy} onClick={() => void generate()}>
          {busy ? t("Generating recap…") : t("Generate with template")}
        </button>
      </> : !error ? <p role="status">{t("Loading templates…")}</p> : null}
      {error ? <ErrorState message={error} retry={catalog ? () => void generate() : () => setReload((value) => value + 1)} /> : null}
    </div> : null}
  </details>;
}
