import { useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, request } from "./api";
import type { ConfigurationData } from "./configurationModel";
import { t } from "./i18n";
import { ErrorState, Loading, Notice } from "./UI";
import { useResource } from "./useResource";
import { copySummary } from "./CopySummary";

export function generateParrotSecret(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

export function ParrotReceiver({ onSaved, refreshRevision = 0 }: {
  onSaved: () => void; refreshRevision?: number;
}) {
  const resource = useResource<ConfigurationData>("/v1/configuration");
  const [baseline, setBaseline] = useState(resource.data);
  const [enabled, setEnabled] = useState(Boolean(resource.data?.values.parrot_webhook_enabled));
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [copied, setCopied] = useState(false);
  const previousRefresh = useRef(refreshRevision);
  const configured = Boolean(baseline?.secrets.parrot_webhook_secret);
  const dirty = Boolean(baseline && (enabled !== baseline.values.parrot_webhook_enabled || secret));

  function load(data: ConfigurationData) {
    setBaseline(data);
    setEnabled(Boolean(data.values.parrot_webhook_enabled));
    setSecret("");
    setShowSecret(false);
    setCopied(false);
    setConflict(false);
    setError("");
    setSaved(false);
  }

  useEffect(() => {
    if (previousRefresh.current === refreshRevision) return;
    previousRefresh.current = refreshRevision;
    resource.refresh();
  }, [refreshRevision, resource.refresh]);

  useEffect(() => {
    if (!resource.data || resource.data.revision === baseline?.revision) return;
    if (dirty) setConflict(true);
    else load(resource.data);
  }, [resource.data, baseline?.revision, dirty]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!baseline || busy || resource.loading || conflict || !dirty || (enabled && !configured && !secret.trim())) return;
    setBusy(true);
    setError("");
    setSaved(false);
    const changes: Record<string, unknown> = {};
    if (enabled !== baseline.values.parrot_webhook_enabled) changes.parrot_webhook_enabled = enabled;
    // Empty means keep the write-only secret; toggling the receiver must never clear it.
    if (secret) changes.parrot_webhook_secret = secret;
    try {
      const result = await request<ConfigurationData>("/v1/configuration", {
        method: "PATCH", body: JSON.stringify({ revision: baseline.revision, changes }),
      });
      load(result);
      resource.setData(result);
      setSaved(true);
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      else setError(errorMessage(error));
    } finally { setBusy(false); }
  }

  return <>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {!baseline && resource.loading ? <Loading>{t("Loading server settings…")}</Loading> : null}
    {baseline ? <form onSubmit={save} aria-label={t("Save Parrot settings")} aria-busy={busy}>
      <p className="caption" role="status">{t("Receiver:")}{" "}{baseline.values.parrot_webhook_enabled
        ? configured ? t("enabled; waiting for deliveries") : t("missing signing secret") : t("disabled")}</p>
      <fieldset disabled={busy || resource.loading || Boolean(resource.error) || conflict}>
        <div className="settings-grid">
          <label className="setting-field" htmlFor="parrot-receiver-enabled">
            <span id="parrot-receiver-label">{t("Parrot webhook enabled")}</span>
            <select id="parrot-receiver-enabled" aria-labelledby="parrot-receiver-label" value={String(enabled)} onChange={(event) => {
              setEnabled(event.target.value === "true"); setSaved(false); setError("");
            }}>
              <option value="false">{t("Disabled")}</option><option value="true">{t("Enabled")}</option>
            </select>
          </label>
          <div className="setting-field">
            <span id="parrot-secret-label"><label htmlFor="parrot-signing-secret">{t("Parrot webhook secret")}</label></span>
            <input id="parrot-signing-secret" aria-labelledby="parrot-secret-label" type={showSecret ? "text" : "password"} autoComplete="new-password" spellCheck={false}
              autoCapitalize="none" maxLength={4096} required={enabled && !configured} value={secret}
              aria-describedby="parrot-secret-hint" onChange={(event) => {
                setSecret(event.target.value); setSaved(false); setError(""); setCopied(false);
              }} />
            <small id="parrot-secret-hint">{configured
              ? t("Signing secret saved. Leave blank to keep it, or enter a replacement.")
              : t("Enter the same signing secret used in Parrot.")}</small>
            <div className="button-row">
              <button type="button" className="text-button" onClick={() => {
                setSecret(generateParrotSecret()); setSaved(false); setError(""); setCopied(false);
              }}>{t("Generate signing secret")}</button>
              {secret ? <>
                <button type="button" className="text-button" onClick={async () => {
                  try { await copySummary(secret); setCopied(true); }
                  catch { setError(t("Could not copy the signing secret. Show it and copy it manually.")); }
                }}>{copied ? t("Copied") : t("Copy signing secret")}</button>
                <button type="button" className="text-button" aria-pressed={showSecret} onClick={() => setShowSecret((value) => !value)}>
                  {showSecret ? t("Hide signing secret") : t("Show signing secret")}
                </button>
              </> : null}
            </div>
            {secret ? <small role="status">{t("Copy this secret into Parrot, then save these settings.")}</small> : null}
          </div>
        </div>
      </fieldset>
      <p className="caption">{t("These settings apply to all profiles. The webhook address below selects the destination profile.")}</p>
      {error ? <ErrorState message={error} /> : null}
      {conflict ? <ErrorState message={t("Server settings changed while you were editing. Your edits are still here. Discard changes to load the current settings before making further changes.")} /> : null}
      {saved ? <Notice tone="success">{t("Parrot settings saved. Changes apply immediately.")}</Notice> : null}
      <div className="button-row">
        <button type="submit" className="button" disabled={busy || resource.loading || Boolean(resource.error) || conflict || !dirty || (enabled && !configured && !secret.trim())}>
          {busy ? t("Saving…") : t("Save Parrot settings")}
        </button>
        {dirty || conflict ? <button type="button" className="text-button" disabled={busy || resource.loading} onClick={() => {
          load(resource.data || baseline); resource.refresh();
        }}>{t("Discard changes")}</button> : null}
      </div>
    </form> : null}
  </>;
}
