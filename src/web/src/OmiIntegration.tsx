import { t } from "./i18n";
import { useEffect, useState } from "react";
import { errorMessage, post } from "./api";
import { VendorConnections } from "./VendorConnections";
import { AudioForwarding } from "./AudioForwarding";
import { Icon } from "./Icon";
import type { Source } from "./types";
import { useResource } from "./useResource";
import { ErrorState, Loading, Notice } from "./UI";

type OmiImportResult = {
  imported: number;
  existing: number;
  skipped: number;
  has_more: boolean;
  next_offset: number;
  items: Source[];
};

export function OmiIntegration({ onSaved, onConfigure, refreshRevision, search, profileSelected = true }: {
  profileSelected?: boolean;
  onSaved: () => void; onConfigure: (label?: string) => void; refreshRevision: number; search: string;
}) {
  const resource = useResource<{ configured: boolean }>(profileSelected ? "/v1/integrations/omi" : null);
  useEffect(() => { resource.refresh(); }, [refreshRevision, resource.refresh]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<OmiImportResult>();
  const [nextPage, setNextPage] = useState<{ end: string; offset: number }>();
  const unavailable = !profileSelected || busy || resource.loading || Boolean(resource.error) || !resource.data?.configured;
  async function importPage(next = false) {
    if (unavailable || (next && !nextPage)) return;
    const page = next && nextPage ? nextPage : { end: new Date().toISOString(), offset: 0 };
    setBusy(true);
    setError("");
    setResult(undefined);
    if (!next) setNextPage(undefined);
    try {
      const imported = await post<OmiImportResult>("/v1/integrations/omi/import", {
        end_date: page.end,
        limit: 25,
        offset: page.offset,
      });
      setResult(imported);
      setNextPage(imported.has_more ? { end: page.end, offset: imported.next_offset } : undefined);
      window.dispatchEvent(new Event("omi:archive-changed"));
      onSaved();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="connection-options" aria-label={t("Recording devices and brands")}>
      <p>{t("Choose how recordings reach your private archive.")}</p>
      <section className="connection-local">
      <span className="settings-badge">{t("Local capture")}</span>
      <h3>{t("Omi + iPhone companion")}</h3>
      <p className="muted settings-description">
        {t("Connect your Omi to the iPhone app over Bluetooth for local audio capture. This path does not need an Omi API key.")}{" "}</p>
      <p className="caption">{t("Pair your pendant in the iPhone app’s Capture tab. Set your server address in the app’s Settings.")}</p>
      </section>
      <details className="settings-disclosure" open={search.trim() ? true : undefined}>
        <summary>{t("Import from Omi cloud")}{" "}<span>{t("Optional · transcripts only")}</span></summary>
        <div className="settings-disclosure-body">
      <p className="muted settings-description">
        {t("Optionally import existing conversations from Omi’s cloud into your local archive. Imports include transcripts only, without original audio.")}{" "}</p>
      {!profileSelected ? <p className="caption">{t("Choose a profile in Settings → Account & profiles to import conversations.")}</p> : null}
      {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
      {resource.loading ? <Loading>{t("Checking Omi API connection…")}</Loading> : resource.data ? (
        <p className="caption">
          {resource.data.configured
            ? t("Omi API is configured on your server. Import up to 25 conversations at a time, newest first.")
            : <>{t("To enable cloud import, add your Omi API key in Advanced → Omi connections. Saved keys stay on your server and are never displayed here.")}</>}
        </p>
      ) : null}
      {!resource.data?.configured ? <button type="button" className="text-button" onClick={() => onConfigure()}>{t("Set up an Omi API key")}{" "}<Icon name="chevron-right" size={14} /></button> : null}
      {error ? <ErrorState message={error} /> : null}
      {result ? (
        <Notice tone="success">
          {t("Last page:")}{" "}{result.imported} {t("imported,")}{" "}{result.existing} {t("already in your archive,")}{" "}{" "}{result.skipped} {t("skipped.")}{" "}{!result.has_more ? t("No more conversations in this import.") : t("More conversations are available.")}
        </Notice>
      ) : null}
      <div className="button-row">
        <button type="button" className="button" disabled={unavailable}
          onClick={() => void importPage()}>
          {busy ? t("Importing…") : t("Import latest conversations")}
        </button>
        {nextPage ? (
          <button type="button" className="button" disabled={unavailable}
            onClick={() => void importPage(true)}>
            {t("Load next 25")}{" "}</button>
        ) : null}
        {!resource.loading && !resource.error && !resource.data?.configured ? (
          <button type="button" className="text-button" onClick={resource.refresh}>{t("Check connection again")}</button>
        ) : null}
      </div>
        </div>
      </details>
      <details className="settings-disclosure" open={search.trim() ? true : undefined}>
        <summary>{t("Forward audio from the official app")}{" "}<span>{t("Advanced · optional")}</span></summary>
        <div className="settings-disclosure-body">{profileSelected ? <AudioForwarding onSaved={onSaved} /> : <p className="caption">{t("Choose a profile in Settings → Account & profiles to set up an audio receiver for its archive.")}</p>}</div>
      </details>
      <VendorConnections onSaved={onSaved} onConfigure={onConfigure} refreshRevision={refreshRevision}
        search={search} profileSelected={profileSelected} />
    </section>
  );
}
