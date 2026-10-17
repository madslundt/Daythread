import { useEffect, useState } from "react";
import { errorMessage, post } from "./api";
import { t } from "./i18n";
import { useResource } from "./useResource";
import { ErrorState, Loading, Notice } from "./UI";
import { TranscriptExportImport } from "./TranscriptExportImport";
import type { Vendor } from "./vendors";

type ImportResult = {
  imported: number; existing: number; skipped: number; has_more: boolean;
  next_offset: number; next_cursor: string | null;
};

export function VendorConnections({ onSaved, onConfigure, refreshRevision, search, profileSelected }: {
  onSaved: () => void; onConfigure: (label?: string) => void; refreshRevision: number;
  search: string; profileSelected: boolean;
}) {
  const resource = useResource<{ items: Vendor[] }>("/v1/vendors");
  useEffect(() => { resource.refresh(); }, [refreshRevision, resource.refresh]);
  return <section aria-label={t("Recording devices and brands")}>
    <h3>{t("More recording devices")}</h3>
    <p className="caption">{t("Upload exported audio from your device. Available cloud connections import transcripts into the selected profile.")}</p>
    {resource.loading ? <Loading>{t("Loading recording devices…")}</Loading> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.data?.items.filter((vendor) => vendor.id !== "manual" && vendor.id !== "omi").map((vendor) =>
      <VendorConnection key={vendor.id} vendor={vendor} onSaved={onSaved} onConfigure={onConfigure}
        profileSelected={profileSelected} expanded={Boolean(search.trim())} />)}
  </section>;
}

export function VendorConnection({ vendor, onSaved, onConfigure, profileSelected, expanded }: {
  vendor: Vendor; onSaved: () => void; onConfigure: (label?: string) => void;
  profileSelected: boolean; expanded: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult>();
  const [nextPage, setNextPage] = useState<{ end: string; offset: number; cursor: string | null }>();
  const cloud = vendor.capabilities.includes("transcript_import");
  const unavailable = busy || !profileSelected || !vendor.configured;
  async function importPage(next = false) {
    if (unavailable || (next && !nextPage)) return;
    const page = next && nextPage ? nextPage : { end: new Date().toISOString(), offset: 0, cursor: null };
    setBusy(true);
    setError("");
    setResult(undefined);
    if (!next) setNextPage(undefined);
    try {
      const imported = await post<ImportResult>(`/v1/vendors/${encodeURIComponent(vendor.id)}/import`, {
        end_date: page.end, offset: page.offset, cursor: page.cursor, limit: 25,
      });
      setResult(imported);
      setNextPage(imported.has_more ? { end: page.end, offset: imported.next_offset, cursor: imported.next_cursor } : undefined);
      window.dispatchEvent(new Event("omi:archive-changed"));
      onSaved();
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <details className="settings-disclosure" open={expanded ? true : undefined}>
    <summary>{t(vendor.label)}{" "}<span>{cloud ? t("Optional · transcripts only") : t("Export and upload")}</span></summary>
    <div className="settings-disclosure-body">
      <p className="caption">{t(vendor.import_instructions)}</p>
      {vendor.setup_url ? <a className="text-link" href={vendor.setup_url} target="_blank" rel="noreferrer">{t("Device setup and export instructions")}</a> : null}
      {vendor.capabilities.includes("file_upload") ? <p className="caption">{t("Use Upload recordings on Today and choose this brand to keep its source information.")}</p> : null}
      {cloud ? <>
        <p className="caption">{t("This connection imports transcripts only. Original audio remains with the vendor.")}</p>
        {!profileSelected ? <p className="caption">{t("Choose a profile in Settings → Profiles to import conversations.")}</p> : null}
        {!vendor.configured ? <>
          <p className="caption">{t("Save the vendor API key in server settings. Keys stay on your server.")}</p>
          <button type="button" className="text-button" onClick={() => onConfigure(vendor.label)}>{t("Configure {vendor} connection", { vendor: t(vendor.label) })}</button>
        </> : null}
        {result ? <Notice tone="success">{t("{imported} imported · {existing} already in your archive · {skipped} skipped.", result)}{" "}
          {result.has_more ? t("More conversations are available.") : t("No more conversations in this import.")}</Notice> : null}
        <div className="button-row">
          <button type="button" className="button" disabled={unavailable} onClick={() => void importPage()}>{busy ? t("Importing…") : t("Import latest conversations")}</button>
          {nextPage ? <button type="button" className="button" disabled={unavailable} onClick={() => void importPage(true)}>{t("Load next 25")}</button> : null}
        </div>
      </> : null}
      {vendor.capabilities.includes("transcript_export") ? <TranscriptExportImport vendor={vendor} profileSelected={profileSelected} onSaved={onSaved} /> : null}
      {error ? <ErrorState message={error} /> : null}
    </div>
  </details>;
}
