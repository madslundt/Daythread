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
      Receiver: {status.ready ? "enabled; waiting for deliveries" : status.enabled ? "missing server token" : "disabled"}.
      {" "}{status.accepted_requests} deliveries durably accepted.
      {" "}Last new audio received: {status.last_received_at ? new Date(status.last_received_at).toLocaleString() : "never"}.
      {" "}Receipt confirms only that chunk arrived; it does not prove continuous capture or a connected pendant.
    </p>
    {status.last_source_id ? <p className="caption">Latest source: <code>{status.last_source_id}</code>. Open Rewind to play and inspect its transcription.</p> : null}
    {!status.worker_enabled ? <p className="caption">Transcription worker is disabled. Set OMI_WORKER_ENABLED=true and restart to process accepted audio.</p> : null}
    {status.last_failure ? <p className="caption" role="alert">
      Last receiver failure ({status.last_failure.status}) at {new Date(status.last_failure.at).toLocaleString()}: {status.last_failure.detail}.
      {status.last_received_at && status.last_received_at > status.last_failure.at ? " New audio has arrived since this failure." : ""}
    </p> : null}
  </>;
}

export function AudioForwarding({ onSaved }: { onSaved: () => void }) {
  const resource = useResource<ForwardingStatus>("/v1/integrations/omi/audio/status", 5000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [base, setBase] = useState(() => window.location.origin);
  const template = `${base.replace(/\/$/, "")}/v1/integrations/omi/audio?token=REPLACE_WITH_SERVER_TOKEN`;
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
  return <section aria-label="Official app audio forwarding">
    <h3>Official app audio forwarding</h3>
    <p className="muted settings-description">
      Optional raw audio receiver for Omi Settings → Developer Mode → Realtime audio bytes.
      Current upstream sends from Omi’s backend and blocks private destinations: a phone on your LAN or VPN alone cannot deliver this webhook.
      Forwarding does not establish cloud-free operation. No network exposure is configured here.
    </p>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.data ? <ForwardingDetails status={resource.data} /> : null}
    <p className="caption">
      Set <code>OMI_AUDIO_FORWARDING_TOKEN</code> on the server to a random URL-safe secret of at least 32 characters and restart.
      The secret is never returned here. Use a dedicated HTTPS ingress that permits only this POST route if you later choose hosted delivery;
      keep the archive UI and other API routes private.
    </p>
    <label className="setting-field" htmlFor="forwarding-base">
      <span>Receiver origin reachable by the sender</span>
      <input id="forwarding-base" type="url" value={base} onChange={(event) => setBase(event.target.value)} />
    </label>
    <p className="caption">URL template (replace the token privately in the official app; do not add sample_rate or uid):</p>
    <code style={{ overflowWrap: "anywhere" }}>{template}</code>
    <p className="caption">
      Save the URL, set Every x seconds to 10, and enable Realtime audio bytes in Omi.
      Upstream may send one-second requests in bursts. Supported input is PCM16 mono at 8 or 16 kHz, up to 120 seconds per request.
      Webhook times are receipt estimates; missing capture, ordering, offline replay and device identity remain unknown.
      Speak a test phrase, refresh status, then check playback and transcript in Rewind.
      If nothing arrives, check the sender’s network route, Omi’s toggle, saved URL/token, and server/proxy errors. Repeated failures may disable upstream delivery.
    </p>
    <p className="caption">Capture pause also rejects new webhooks. Previously accepted audio keeps processing. Native capture and cloud transcript import remain available separately.</p>
    {error ? <ErrorState message={error} /> : null}
    <div className="button-row">
      <button type="button" className="button" disabled={busy || !resource.data || (!resource.data.configured && !resource.data.enabled)} onClick={() => void toggle()}>
        {busy ? "Saving…" : resource.data?.enabled ? "Disable audio receiver" : "Enable audio receiver"}
      </button>
      <button type="button" className="text-button" onClick={resource.refresh}>Refresh audio status</button>
    </div>
  </section>;
}
