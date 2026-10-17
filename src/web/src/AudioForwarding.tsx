import { statusLabel } from "./statusLabels";
import { displayLocale, t } from "./i18n";
import { profileResourceUrl } from "./profiles";
import { useState } from "react";
import { errorMessage, request } from "./api";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export type ForwardingStatus = {
  enabled: boolean;
  configured: boolean;
  ready: boolean;
  worker_enabled: boolean;
  accepted_requests: number;
  last_received_at: string | null;
  last_source_id: string | null;
  last_failure: { at: string; status: number; detail: string } | null;
};

export function ForwardingDetails({ status }: { status: ForwardingStatus }) {
  return <>
    <p className="caption" role="status">
      {t("Receiver:")}{" "}{status.ready ? t("enabled; waiting for deliveries") : status.enabled ? t("missing server token") : t("disabled")}.
      {" "}{status.accepted_requests} {t("deliveries durably accepted.")}{" "}{" "}{t("Last new audio received:")}{" "}{status.last_received_at ? new Date(status.last_received_at).toLocaleString(displayLocale()) : t("never")}.
      {" "}{t("Receipt confirms only that chunk arrived; it does not prove continuous capture or a connected pendant.")}{" "}</p>
    {status.last_source_id ? <p className="caption">{t("Latest source:")}{" "}<code>{status.last_source_id}</code>{t(". Open Rewind to play and inspect its transcription.")}</p> : null}
    {!status.worker_enabled ? <p className="caption">{t("Transcription worker is disabled. Enable Worker enabled in server settings and restart the backend to process accepted audio.")}</p> : null}
    {status.last_failure ? <p className="caption" role="alert">
      {t("Last receiver failure (")}{status.last_failure.status}{t(") at")}{" "}{new Date(status.last_failure.at).toLocaleString(displayLocale())}: {t(status.last_failure.detail)}.
      {status.last_received_at && status.last_received_at > status.last_failure.at ? t("New audio has arrived since this failure.") : ""}
    </p> : null}
  </>;
}

export function AudioForwarding({ onSaved }: { onSaved: () => void }) {
  const resource = useResource<ForwardingStatus>("/v1/integrations/omi/audio/status", 5000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [base, setBase] = useState(() => window.location.origin);
  const template = `${base.replace(/\/$/, "")}${profileResourceUrl("/v1/integrations/omi/audio?token=REPLACE_WITH_SERVER_TOKEN")}`;
  async function toggle() {
    if (!resource.data) return;
    setBusy(true);
    setError("");
    try {
      await request("/v1/settings", { method: "PATCH", body: JSON.stringify({ audio_forwarding_enabled: !resource.data.enabled }) });
      resource.refresh();
      onSaved();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return <section aria-label={t("Official app audio forwarding")}>
    <h3>{t("Official app audio forwarding")}</h3>
    <p className="muted settings-description">
      {t("Optional raw audio receiver for Omi Settings → Developer Mode → Realtime audio bytes. Current upstream sends from Omi’s backend and blocks private destinations: a phone on your LAN or VPN alone cannot deliver this webhook. Forwarding does not establish cloud-free operation. No network exposure is configured here.")}{" "}</p>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.data ? <ForwardingDetails status={resource.data} /> : null}
    <ol className="forwarding-steps">
    <li><strong>{t("Set up a server token")}</strong><p className="caption">
      {t("Save Audio forwarding token under Models & server → Omi connections. Use a random URL-safe secret of at least 32 characters. The secret is never returned here. Use a dedicated HTTPS ingress that permits only this POST route if you later choose hosted delivery; keep the archive UI and other API routes private.")}{" "}</p>
    </li>
    <li><strong>{t("Choose the receiver address")}</strong>
    <label className="setting-field" htmlFor="forwarding-base">
      <span>{t("Receiver origin reachable by the sender")}</span>
      <input id="forwarding-base" type="url" value={base} onChange={(event) => setBase(event.target.value)} />
    </label>
    <p className="caption">{t("URL template (replace the token privately in the official app; do not add sample_rate or uid):")}</p>
    <code style={{ overflowWrap: "anywhere" }}>{template}</code>
    <p className="caption">{t("This URL sends recordings to the selected profile. Save a different profile’s URL in the sender to change its destination. The receiver token and enable switch are shared by all profiles.")}</p>
    </li>
    <li><strong>{t("Connect the official Omi app")}</strong><p className="caption">
      {t("Save the URL, set Every x seconds to 10, and enable Realtime audio bytes in Omi. Upstream may send one-second requests in bursts. Supported input is PCM16 mono at 8 or 16 kHz, up to 120 seconds per request. Webhook times are receipt estimates; missing capture, ordering, offline replay and device identity remain unknown. Speak a test phrase, refresh status, then check playback and transcript in Rewind. If nothing arrives, check the sender’s network route, Omi’s toggle, saved URL/token, and server/proxy errors. Repeated failures may disable upstream delivery.")}{" "}</p>
    </li></ol>
    <p className="caption">{t("Capture pause also rejects new webhooks. Previously accepted audio keeps processing. Native capture and cloud transcript import remain available separately.")}</p>
    {error ? <ErrorState message={error} /> : null}
    <p className="caption">{t("Enabling or disabling the receiver saves immediately.")}</p>
    <div className="button-row">
      <button type="button" className="button" disabled={busy || !resource.data || (!resource.data.configured && !resource.data.enabled)} onClick={() => void toggle()}>
        {busy ? t("Saving…") : resource.data?.enabled ? t("Disable audio receiver") : t("Enable audio receiver")}
      </button>
      <button type="button" className="text-button" onClick={resource.refresh}>{t("Refresh audio status")}</button>
    </div>
  </section>;
}
