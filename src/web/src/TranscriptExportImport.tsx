import { useId, useState } from "react";
import { errorMessage, request } from "./api";
import { localDateTimeInput } from "./format";
import { t } from "./i18n";
import { profileScope } from "./profiles";
import { ErrorState, Notice } from "./UI";
import type { Vendor } from "./vendors";

type ExportResult = { imported: number; existing: number; skipped: number };

export async function transcriptExportBody(file: File, startedAt: string, endedAt: string) {
  if (!/\.(srt|txt)$/i.test(file.name) || !file.size || file.size > 1_000_000)
    throw new Error(t("Choose an SRT or TXT transcript export no larger than 1 MB."));
  const start = new Date(startedAt), end = new Date(endedAt);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end)
    throw new Error(t("Choose the original recording start and end times, with the end after the start."));
  if (end.getTime() > Date.now() + 30_000)
    throw new Error(t("Choose when the recording stopped, at or before now."));
  return { content: await file.text(), format: /\.srt$/i.test(file.name) ? "srt" : "txt",
    started_at: start.toISOString(), ended_at: end.toISOString() };
}

export function TranscriptExportImport({ vendor, profileSelected, onSaved }: {
  vendor: Vendor; profileSelected: boolean; onSaved: () => void;
}) {
  const id = useId();
  const [file, setFile] = useState<File>();
  const [startedAt, setStartedAt] = useState("");
  const [endedAt, setEndedAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ExportResult>();
  async function importExport(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || busy || !profileSelected) return;
    const scope = profileScope();
    setBusy(true);
    setError("");
    setResult(undefined);
    try {
      const body = await transcriptExportBody(file, startedAt, endedAt);
      const imported = await request<ExportResult>(`/v1/vendors/${encodeURIComponent(vendor.id)}/import-export`, {
        method: "POST", body: JSON.stringify(body),
      }, scope);
      setResult(imported);
      window.dispatchEvent(new Event("omi:archive-changed"));
      onSaved();
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <form className="transcript-export-import" onSubmit={importExport} aria-label={t("Import {vendor} transcript export", { vendor: t(vendor.label) })}>
    <p className="caption">{t("Choose the original transcript export, not a summary. Enter the original recording’s start and end times. Original audio is not included.")}</p>
    {!profileSelected ? <p className="caption">{t("Choose a profile in Settings → Profiles to import conversations.")}</p> : null}
    <fieldset disabled={busy || !profileSelected}>
      <label className="transcript-export-field" htmlFor={`${id}-file`}>
        <span>{t("Transcript export (SRT or TXT)")}</span>
        <input id={`${id}-file`} type="file" accept=".srt,.txt,text/plain,application/x-subrip" required
          onChange={(event) => { setFile(event.target.files?.[0]); setError(""); setResult(undefined); }} />
      </label>
      <div className="transcript-export-times">
        <label className="transcript-export-field" htmlFor={`${id}-start`}>
          <span>{t("Recording started")}</span>
          <input id={`${id}-start`} type="datetime-local" required max={localDateTimeInput()} value={startedAt}
            onChange={(event) => setStartedAt(event.target.value)} />
        </label>
        <label className="transcript-export-field" htmlFor={`${id}-end`}>
          <span>{t("Recording ended")}</span>
          <input id={`${id}-end`} type="datetime-local" required max={localDateTimeInput()} value={endedAt}
            onChange={(event) => setEndedAt(event.target.value)} />
        </label>
      </div>
      <p className="caption">{t("Time on this device:")}{" "}{Intl.DateTimeFormat().resolvedOptions().timeZone}</p>
      <button type="submit" className="button" disabled={!file}>{busy ? t("Importing…") : t("Import transcript export")}</button>
    </fieldset>
    {error ? <ErrorState message={error} /> : null}
    {result ? <Notice tone="success">{t("{imported} imported · {existing} already in your archive · {skipped} skipped.", result)}</Notice> : null}
  </form>;
}
