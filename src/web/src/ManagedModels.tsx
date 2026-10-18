import { useState } from "react";
import { t } from "./i18n";
import { useManagedAccount } from "./ManagedAccount";
import { managedKinds, type ManagedAccount, type ManagedConnectionUpdate, type ManagedKind, type ManagedMode } from "./managedService";

export function modelTaskLabel(kind: ManagedKind): string {
  return kind === "stt" ? t("Transcription") : kind === "llm" ? t("Language models") : t("Semantic search");
}
function offEffect(kind: ManagedKind): string {
  return kind === "stt" ? t("Off pauses new audio transcription. Existing recordings and transcripts stay available.")
    : kind === "llm" ? t("Off pauses generated summaries, chat answers, and other language tasks. Existing content stays available.")
      : t("Off pauses semantic indexing and search by meaning. Keyword search stays available.");
}

type Draft = { provider: string; model: string; key: string; keySource: string; first: string; second: string };
function optionNames(kind: ManagedKind): [string, string] {
  return kind === "llm" ? ["temperature", "max_tokens"] : kind === "stt" ? ["language", "prompt"] : ["dimensions", "revision"];
}
export function ownModelUpdate(kind: ManagedKind, draft: Draft): ManagedConnectionUpdate {
  if (kind === "embedding" && (!draft.first.trim() || !Number.isInteger(Number(draft.first)) || Number(draft.first) < 1 || Number(draft.first) > 8192))
    throw new Error(t("Choose embedding dimensions from 1 to 8192."));
  const options: Record<string, string | number> = {};
  const names = optionNames(kind);
  for (const [index, value] of [draft.first, draft.second].entries()) {
    if (!value.trim()) continue;
    options[names[index]] = ["temperature", "max_tokens", "dimensions"].includes(names[index]) ? Number(value) : value;
  }
  return { mode: "own", provider: draft.provider, model: draft.model.trim(), options,
    ...(draft.keySource === "new" ? { key: draft.key } : draft.keySource === "keep" ? {} : { key_from: draft.keySource as ManagedKind }) };
}

export function ManagedModels() {
  const session = useManagedAccount();
  if (!session.managed || !session.account) return null;
  return <ManagedModelsPanel account={session.account} busy={Boolean(session.updatingModels)} onUpdate={session.updateConnection} />;
}

export function ManagedModelsPanel({ account, busy = false, onUpdate }: {
  account: ManagedAccount;
  busy?: boolean;
  onUpdate: (kind: ManagedKind, update: ManagedConnectionUpdate) => Promise<void>;
}) {
  return <section className="managed-models" aria-labelledby="managed-models-title">
    <div className="settings-section-heading"><h2 id="managed-models-title">{t("AI models")}</h2>
      <p>{t("Choose Daythread, your own provider, or Off separately for each task.")}</p></div>
    <p className="caption">{t("Turning all models Off keeps your hosting, storage, and existing archive available.")}</p>
    {!account.hosting_active ? <p className="notice">{t("Hosting is not active. Hosted AI processing is paused, including your own providers.")}</p> : null}
    {account.inference ? <div className="managed-model-cards">{managedKinds.map((kind) =>
      <ManagedModelCard key={`${account.id}:${kind}`} kind={kind} account={account} busy={busy} onUpdate={onUpdate} />)}</div>
      : <p role="status">{t("Model settings are unavailable. Refresh your account and try again.")}</p>}
  </section>;
}

function ManagedModelCard({ account, kind, busy, onUpdate }: {
  account: ManagedAccount;
  kind: ManagedKind;
  busy: boolean;
  onUpdate: (kind: ManagedKind, update: ManagedConnectionUpdate) => Promise<void>;
}) {
  const connection = account.inference![kind];
  const providers = (account.providers ?? []).filter((provider) => provider.kinds.includes(kind));
  const names = optionNames(kind);
  const [draft, setDraft] = useState<Draft>(() => ({
    provider: connection.provider || providers[0]?.id || "", model: connection.model,
    key: "", keySource: connection.has_key ? "keep" : "new",
    first: String(connection.options[names[0]] ?? ""), second: String(connection.options[names[1]] ?? ""),
  }));
  const [view, setView] = useState<ManagedMode>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const mode = view ?? connection.mode;
  const disabled = busy || pending;
  const savedKey = connection.has_key && connection.provider === draft.provider;
  const keySources = managedKinds.filter((other) => other !== kind && account.inference?.[other].provider === draft.provider && account.inference[other].has_key);
  const keySource = draft.keySource === "keep" && !savedKey ? "new" : draft.keySource;
  const edit = (update: Partial<Draft>) => { setDraft((old) => ({ ...old, ...update })); setSaved(false); };
  async function persist(update: ManagedConnectionUpdate, nextView?: ManagedMode) {
    setPending(true); setError(""); setSaved(false);
    try {
      await onUpdate(kind, update);
      setView(nextView); setSaved(true);
      if (update.mode === "own" && update.provider !== undefined) setDraft((old) => ({ ...old, key: "", keySource: "keep" }));
    } catch {
      setError(t("Your model settings could not be saved. Check your connection, model, and key, then try again."));
    } finally { setPending(false); }
  }
  function choose(next: ManagedMode) {
    if (next === "own") {
      if (connection.provider && connection.model && connection.has_key) void persist({ mode: "own" });
      else void persist({ mode: "off" }, "own");
    } else void persist({ mode: next });
  }
  const unsavedOwn = mode === "own" && (draft.provider !== connection.provider || draft.model.trim() !== connection.model
    || draft.first !== String(connection.options[names[0]] ?? "") || draft.second !== String(connection.options[names[1]] ?? "")
    || keySource !== "keep");
  const label = modelTaskLabel(kind);
  return <article className="managed-model-card" aria-labelledby={`model-${kind}-title`}>
    <h3 id={`model-${kind}-title`}>{label}</h3>
    <fieldset className="managed-model-modes" disabled={disabled} aria-label={label}>
      {(["managed", "own", "off"] as const).map((value) => <label key={value}>
        <input type="radio" name={`model-${kind}-mode`} value={value} checked={mode === value} onChange={() => choose(value)} />
        <span>{value === "managed" ? t("Daythread") : value === "own" ? t("My own") : t("Off")}</span>
      </label>)}
    </fieldset>
    <p className="caption">{t("Currently active: {value0}", { value0: connection.mode === "managed" ? t("Daythread") : connection.mode === "own" ? t("My own") : t("Off") })}</p>
    {connection.mode === "own" ? <p className="caption">{t("Active model: {value0}", { value0: connection.model })}</p> : null}
    {mode === "managed" ? <div className="managed-model-fixed">
      <strong>{account.rates?.models[kind]?.model || t("Daythread model")}</strong>
      <p>{t("Daythread chooses this model and its settings. Use My own to configure your provider.")}</p>
      {account.managed_available?.[kind] === false ? <p role="status">{t("This Daythread model is currently unavailable.")}</p> : null}
    </div> : mode === "off" ? <p>{offEffect(kind)}</p> : <form onSubmit={(event) => {
      event.preventDefault(); void persist(ownModelUpdate(kind, { ...draft, keySource }));
    }}>
      <fieldset disabled={disabled}>
        <label>{t("Provider")}<select value={draft.provider} onChange={(event) => edit({ provider: event.target.value, key: "", keySource: event.target.value === connection.provider && connection.has_key ? "keep" : "new" })} required>
          {!providers.length ? <option value="">{t("No supported provider is configured.")}</option> : null}
          {providers.map((provider) => <option value={provider.id} key={provider.id}>{provider.label}</option>)}
        </select></label>
        <label>{t("Model")}<input value={draft.model} onChange={(event) => edit({ model: event.target.value })} maxLength={200} required autoComplete="off" /></label>
        <label>{t("Provider key")}<select value={keySource} onChange={(event) => edit({ keySource: event.target.value })}>
          {savedKey ? <option value="keep">{t("Keep saved key")}</option> : null}
          {keySources.map((other) => <option key={other} value={other}>{t("Use saved key from {value0}", { value0: modelTaskLabel(other) })}</option>)}
          <option value="new">{t("Use a new key")}</option>
        </select></label>
        {keySource === "new" ? <label>{t("API key")}<input type="password" autoComplete="new-password" value={draft.key} onChange={(event) => edit({ key: event.target.value })} maxLength={4096} required /></label> : null}
        <div className="managed-model-options">
          <label>{kind === "llm" ? t("Temperature (optional)") : kind === "stt" ? t("Language (optional)") : t("Embedding dimensions")}
            <input type={kind === "stt" ? "text" : "number"} value={draft.first} onChange={(event) => edit({ first: event.target.value })}
              required={kind === "embedding"} min={kind === "llm" ? 0 : 1} max={kind === "llm" ? 2 : 8192} step={kind === "llm" ? 0.1 : 1} maxLength={kind === "stt" ? 6 : undefined} /></label>
          <label>{kind === "llm" ? t("Maximum output tokens (optional)") : kind === "stt" ? t("Prompt (optional)") : t("Revision (optional)")}
            <input type={kind === "llm" ? "number" : "text"} value={draft.second} onChange={(event) => edit({ second: event.target.value })}
              min={1} max={8192} step={1} maxLength={kind === "stt" ? 4000 : kind === "embedding" ? 200 : undefined} /></label>
        </div>
        <p className="caption">{t("Your provider bills these requests directly. Saved keys are never displayed. Your own configuration is kept when switching modes.")}</p>
        {unsavedOwn ? <p className="caption" role="status">{t("Unsaved provider changes")}</p> : null}
        <button className="button accent" type="submit" disabled={!providers.length}>{t("Save my model")}</button>
      </fieldset>
      {connection.mode !== "own" ? <p className="caption">{t("Processing is off until you save your provider.")}</p> : null}
    </form>}
    {pending ? <p role="status">{t("Saving model settings…")}</p> : saved && !unsavedOwn ? <p role="status">{t("Model settings saved.")}</p> : null}
    {error ? <p className="error" role="alert">{error}</p> : null}
  </article>;
}
