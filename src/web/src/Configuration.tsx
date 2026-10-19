import { statusLabel } from "./statusLabels";
import { t, source } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, newId, request } from "./api";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";
import { ModelPicker } from "./ModelPicker";
import { EmbeddingStatus } from "./EmbeddingStatus";
import {
  advancedConfigurationFields, canRebaseConfiguration, configurationChanges, configurationDraft, configurationIsDirty,
  keepSecret, matchingConfigurationFields, routeChanges, routeRows, tasks,
  type ConfigurationData, type ConfigurationDraft, type ConfigurationField, type ModelOverride,
  type RouteRow, type SecretEdit,
} from "./configurationModel";

function SecretControl({ id, label, configured, edit, onChange, route = false, nullable = false, description }: {
  id: string; label: string; configured: boolean; edit: SecretEdit;
  onChange: (edit: SecretEdit) => void; route?: boolean; nullable?: boolean; description?: string;
}) {
  return <div className="setting-field">
    <label htmlFor={id}>{t(label)} · {configured ? t("Configured") : t("Not set")}</label>
    <select id={id} value={edit.mode} aria-describedby={description ? `${id}-hint` : undefined} onChange={(event) =>
      onChange({ mode: event.target.value as SecretEdit["mode"], value: "" })}>
      <option value="keep">{configured ? t("Keep current key") : route || nullable ? t("Keep current setting") : t("Leave key unset")}</option>
      <option value="replace">{configured ? t("Replace key") : t("Add key")}</option>
      {route || nullable ? <option value="inherit">{route ? t("Use provider key") : t("Use shared key")}</option> : null}
      <option value="clear">{route || nullable ? t("No authentication") : t("Clear key")}</option>
    </select>
    {edit.mode === "replace" ? <input
      type="password" aria-label={t("New {value0}", { value0: label })} value={edit.value}
      autoComplete="new-password" spellCheck={false}
      onChange={(event) => onChange({ ...edit, value: event.target.value })}
    /> : null}
    {description ? <small id={`${id}-hint`}>{t(description)}</small> : null}
  </div>;
}

export function ModelRoutes({ field, rows, secrets, onChange, connectionDraft, revision }: {
  field: ConfigurationField; rows: RouteRow[]; secrets: Record<string, boolean>;
  onChange: (rows: RouteRow[]) => void;
  connectionDraft: ConfigurationDraft; revision: string;
}) {
  const llm = field.key === "llm_routes";
  const embedding = field.key === "embedding_routes";
  function update(id: string, changes: Partial<RouteRow>) {
    onChange(rows.map((row) => row.id === id ? { ...row, ...changes } : row));
  }
  return <div className="model-routes">
    <p className="caption">{t(field.description)}</p>
    {rows.map((row, index) => <fieldset className="model-route" key={row.id}>
      <legend>{llm ? t("Task") : t("Language")} {t("route")}{" "}{index + 1}</legend>
      <div className="settings-grid">
        <label className="setting-field">
          <span>{llm ? t("Task") : t("Language code or detector name")}</span>
          {llm ? <select value={row.name} onChange={(event) => update(row.id, { name: event.target.value })}>
            {tasks.map((task) => <option key={task} value={task}>{statusLabel(task)}</option>)}
          </select> : <input value={row.name} placeholder={t("da, en, danish, auto…")} autoCapitalize="none"
            spellCheck={false} onChange={(event) => update(row.id, { name: event.target.value })} />}
        </label>
        <label className="setting-field"><span>{t("Endpoint override")}</span>
          <input type="url" value={row.base_url || ""} placeholder={t("Use default endpoint")}
            onChange={(event) => update(row.id, { base_url: event.target.value })} />
        </label>
        {llm || embedding ? <label className="setting-field"><span>{t("Revision override")}</span>
          <input value={row.revision || ""} placeholder={t("Use default revision")}
            onChange={(event) => update(row.id, { revision: event.target.value })} />
        </label> : null}
        {embedding ? <label className="setting-field"><span>{t("Vector dimension override")}</span>
          <input type="number" min={1} max={8192} value={row.dimension || ""} placeholder={t("Use default dimension")}
            onChange={(event) => update(row.id, { dimension: event.target.value ? Number(event.target.value) : null })} />
        </label> : null}
        {embedding ? <>
          <label className="setting-field"><span>{t("Query prefix override")}</span>
            <input value={row.query_prefix || ""} placeholder={t("Use default prefix")}
              onChange={(event) => update(row.id, { query_prefix: event.target.value })} />
          </label>
          <label className="setting-field"><span>{t("Document prefix override")}</span>
            <input value={row.document_prefix || ""} placeholder={t("Use default prefix")}
              onChange={(event) => update(row.id, { document_prefix: event.target.value })} />
          </label>
        </> : null}
        <SecretControl id={`route-key-${row.id}`} label={t("Route key")} route
          configured={Boolean(secrets[`${field.key}.${row.name.toLowerCase()}.token`])}
          edit={row.secret} onChange={(secret) => update(row.id, { secret })} />
        <ModelPicker id={`route-model-${row.id}`} label={t("Model for {value0}", { value0: row.name || t("route {value0}", { value0: index + 1 }) })}
          value={row.model || ""} provider={embedding ? "embedding" : llm ? "llm" : "stt"}
          draft={connectionDraft} row={row} revision={revision}
          onChange={(model) => update(row.id, { model })} />
      </div>
      <button className="text-button" type="button" onClick={() => {
        if (embedding && !window.confirm(t(
          "After restart, this language will use the default embedding model. Vectors from the removed model cannot be reused or mixed with default-model vectors; the default index backfills any missing transcripts, and the removed index is cleaned up afterward. Remove this route?",
        ))) return;
        onChange(rows.filter((value) => value.id !== row.id));
      }}>
        {t("Remove route")}{" "}{row.name || index + 1}
      </button>
    </fieldset>)}
    <button type="button" className="button" disabled={llm && rows.length >= tasks.length}
      onClick={() => onChange([...rows, {
        id: newId(), name: llm ? tasks.find((task) => !rows.some((row) => row.name === task)) || "chat" : "",
        model: "", base_url: "", revision: "", secret: keepSecret(),
      }])}>{llm ? t("Add task route") : t("Add language route")}</button>
  </div>;
}

const groupDescriptions: Record<string, string> = {
  "Transcription": source("Turn recordings into text with your chosen speech model."),
  "Language models & chat": source("Choose models for answers, summaries, and other tasks."),
  "Semantic search": source("Find related conversations by meaning."),
  "Local inference": source("Shared connection settings for your model services."),
  "Omi connections": source("Credentials for optional Omi imports and audio forwarding."),
  "Archive & processing": source("Control storage locations and background processing."),
};

const modelConnections = [
  { provider: "stt", key: "stt_api_key", title: source("Transcription"), description: source("Transcribes your recordings into text.") },
  { provider: "llm", key: "llm_api_key", title: source("Summaries & chat"), description: source("Answers questions and generates summaries.") },
  { provider: "embedding", key: "embedding_token", title: source("Semantic search"), description: source("Finds related conversations using your embedding model.") },
] as const;
const connectionKeys = modelConnections.flatMap(({ provider, key }) =>
  [`${provider}_base_url`, key, `${provider}_model`]);
const optionsTitle = (group: string) => group === "Transcription" ? t("Transcription options & language routes") :
  group === "Language models & chat" ? t("Language model options & task routes") : group;

export function Configuration({ onSaved, hiddenFields = [], search: controlledSearch,
  onSearchResults, onDirtyChange, refreshRevision = 0, selectedProfile = null }: {
  selectedProfile?: string | null;
  onSaved: () => void;
  hiddenFields?: readonly string[];
  search?: string;
  onSearchResults?: (count: number) => void;
  onDirtyChange?: (dirty: boolean) => void;
  refreshRevision?: number;
}) {
  const resource = useResource<ConfigurationData>("/v1/configuration");
  const [baseline, setBaseline] = useState<ConfigurationData>();
  const [draft, setDraft] = useState<ConfigurationDraft>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState("");
  const [saved, setSaved] = useState(false);
  const [localSearch, setLocalSearch] = useState("");
  const search = controlledSearch ?? localSearch;
  const dirty = Boolean(baseline && draft && configurationIsDirty(baseline, draft, hiddenFields));
  const previousRefresh = useRef(refreshRevision);
  const ownRefresh = useRef(false);
  const handledData = useRef<ConfigurationData | undefined>(undefined);

  function loadDraft(data: ConfigurationData) {
    setBaseline(data);
    setDraft(configurationDraft(data));
    setConflict("");
  }

  useEffect(() => {
    if (previousRefresh.current === refreshRevision) return;
    previousRefresh.current = refreshRevision;
    ownRefresh.current = true;
    resource.refresh();
  }, [refreshRevision, resource.refresh]);

  useEffect(() => {
    const latest = resource.data;
    if (!latest || handledData.current === latest) return;
    handledData.current = latest;
    const mayRebase = ownRefresh.current;
    ownRefresh.current = false;
    if (!baseline || !draft) {
      loadDraft(latest);
    } else if (latest.revision !== baseline.revision) {
      if (!configurationIsDirty(baseline, draft, hiddenFields)) {
        loadDraft(latest);
        setSaved(false);
      } else if (mayRebase && canRebaseConfiguration(baseline, latest, hiddenFields)) {
        // Keep every visible edit, including incomplete keys and routes.
        setBaseline(latest);
        setConflict("");
      } else {
        setError("");
        setConflict(t("Server settings changed while you were editing. Your edits are still here. Discard changes to load the current settings before making further changes."));
      }
    }
  }, [resource.data, baseline, draft, hiddenFields]);

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  function update(key: string, value: unknown) {
    setDraft((current) => current ? { ...current, values: { ...current.values, [key]: value } } : current);
    setSaved(false);
    setError("");
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!baseline || !draft || busy || resource.loading || conflict || !dirty) return;
    setError(""); setSaved(false);
    try {
      const changes = configurationChanges(baseline, draft, hiddenFields);
      if (!Object.keys(changes).length) return;
      const rebuildKeys = new Set([
        "embedding_enabled",
        "embedding_model", "embedding_base_url", "embedding_revision", "embedding_dimension",
        "embedding_query_prefix", "embedding_document_prefix", "embedding_passage_chars",
        "embedding_chunking_strategy", "embedding_target_tokens", "embedding_max_tokens",
        "embedding_overlap_tokens", "embedding_max_input_tokens", "embedding_special_tokens",
        "embedding_tokenizer", "embedding_tokenizer_revision", "embedding_query_format",
        "embedding_query_instruction", "embedding_send_dimensions",
      ]);
      const routeContract = (routes: Record<string, object>) => JSON.stringify(
        Object.fromEntries(Object.entries(routes).sort(([a], [b]) => a.localeCompare(b)).map(([name, route]) =>
          [name, Object.fromEntries(Object.entries(route).filter(([key]) => key !== "token"))])),
      );
      const oldRoutes = routeChanges(routeRows(
        (baseline.values.embedding_routes || {}) as Record<string, ModelOverride>,
      ), false, true);
      const newRoutes = routeChanges(draft.routes.embedding_routes || [], false, true);
      const rebuildRequired = Object.keys(changes).some((key) => rebuildKeys.has(key)) ||
        routeContract(oldRoutes) !== routeContract(newRoutes);
      const disablingEmbeddings = baseline.values.embedding_enabled === true &&
        changes.embedding_enabled === false;
      if (rebuildRequired && !window.confirm(t(disablingEmbeddings
        ? "Turning off embeddings deletes the stored search vectors and disables semantic search. If you enable embeddings later, transcripts need to be embedded again because vectors from different models cannot be compared safely. Turn off embeddings?"
        : "Changing an embedding model or route requires rebuilding its search index. Vectors from different models cannot be compared safely. After restart, search uses the new model's growing index with keyword results while background indexing runs; the previous vectors stay stored separately until the rebuild finishes. Save these changes?"))) return;
      setBusy(true);
      const result = await request<ConfigurationData>("/v1/configuration", {
        method: "PATCH", body: JSON.stringify({ revision: baseline.revision, changes }),
      });
      loadDraft(result);
      resource.setData(result); setSaved(true); onSaved();
      window.dispatchEvent(new Event("omi:settings-changed"));
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status === 409) resource.refresh();
    }
    finally { setBusy(false); }
  }
  function discard() {
    const latest = resource.data || baseline;
    if (latest) loadDraft(latest);
    setError(""); setSaved(false);
  }
  function control(field: ConfigurationField) {
    const id = `config-${field.key}`;
    if (["stt_model", "llm_model", "embedding_model"].includes(field.key)) return <ModelPicker
      id={id} label={t(field.label)} description={t(field.description)} value={String(draft!.values[field.key] || "")}
      provider={field.key === "stt_model" ? "stt" : field.key === "llm_model" ? "llm" : "embedding"} draft={draft!} revision={baseline!.revision}
      onChange={(value) => update(field.key, value)} />;
    if (field.kind === "routes") return <ModelRoutes field={field} rows={draft!.routes[field.key] || []}
      secrets={baseline!.secrets} connectionDraft={draft!} revision={baseline!.revision} onChange={(rows) => {
        setDraft((current) => current ? { ...current, routes: { ...current.routes, [field.key]: rows } } : current);
        setSaved(false); setError("");
      }} />;
    if (field.kind === "secret") return <SecretControl id={id} label={t(field.label)}
      nullable={field.nullable}
      configured={Boolean(baseline!.secrets[field.key])} edit={draft!.secrets[field.key] || keepSecret()}
      description={t(field.description)} onChange={(edit) => {
        setDraft((current) => current ? { ...current, secrets: { ...current.secrets, [field.key]: edit } } : current);
        setSaved(false); setError("");
      }} />;
    return <label className="setting-field" htmlFor={id}>
      <span id={`${id}-label`}>{t(field.label)}{field.restart_required ? t("· restart required") : ""}</span>
      {field.kind === "boolean" ? field.nullable ? <select id={id}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        value={draft!.values[field.key] == null ? "default" : String(draft!.values[field.key])}
        onChange={(event) => update(field.key, event.target.value === "default" ? null : event.target.value === "true")}>
        <option value="default">{t("Model default")}</option><option value="true">{t("Enabled")}</option><option value="false">{t("Disabled")}</option>
      </select> : <select id={id} value={String(draft!.values[field.key] ?? false)}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        onChange={(event) => update(field.key, event.target.value === "true")}>
        <option value="true">{t("Enabled")}</option><option value="false">{t("Disabled")}</option>
      </select> : <input id={id} type={field.kind === "integer" || field.kind === "number" ? "number" : field.key.endsWith("_base_url") ? "url" : "text"}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        step={field.kind === "integer" ? 1 : "any"} min={field.minimum} max={field.maximum}
        value={String(draft!.values[field.key] ?? "")} spellCheck={false} autoCapitalize="none"
        onChange={(event) => update(field.key, event.target.value)} />}
      {field.description ? <small id={`${id}-hint`}>{t(field.description)}</small> : null}
    </label>;
  }
  const fields = matchingConfigurationFields(advancedConfigurationFields(baseline?.fields || [], draft?.values || {}), search, hiddenFields);
  const otherFields = fields.filter((field) => !connectionKeys.includes(field.key));
  const groupOrder = [source("Transcription"), source("Language models & chat"), source("Local inference"), source("Omi connections"), source("Semantic search"), source("Archive & processing")];
  const groups = [...new Set(otherFields.map((field) => field.group))].sort((a, b) => {
    const rank = (group: string) => groupOrder.includes(group) ? groupOrder.indexOf(group) : groupOrder.length;
    return rank(a) - rank(b);
  });
  useEffect(() => {
    onSearchResults?.(baseline && !resource.error ? fields.length : -1);
  }, [Boolean(baseline), resource.error, search, fields.length, onSearchResults]);
  return <section aria-label={t("Server configuration")} className="server-configuration">
    <h2>{t("Advanced")}</h2>
    <p className="muted">{t("Manage your server and the services that transcribe, summarize, and search recordings. Transcription and summaries can use different providers. Changes apply to the iPhone app and web. Saved keys stay on your server.")}</p>
    {resource.loading && !baseline ? <Loading>{t("Loading server settings…")}</Loading> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {baseline && draft ? <form onSubmit={save} aria-busy={busy}>
      {baseline.pending_restart.length ? <p role="status" className="notice">{t("Restart your server to apply:")}{" "}{baseline.pending_restart.map((key) =>
        t(baseline.fields.find((field) => field.key === key)?.label || key.replaceAll("_", " "))).join(", ")}.</p> : null}
      {controlledSearch === undefined ? <label className="setting-field"><span>{t("Find a server setting")}</span>
        <input type="search" value={localSearch} placeholder={t("Search models, keys, storage…")}
          onChange={(event) => setLocalSearch(event.target.value)} />
      </label> : null}
      {search.trim() ? <p className="caption" role="status">{fields.length ?
        t("{value0} matching server {value1}.", { value0: fields.length, value1: fields.length === 1 ? t("setting") : t("settings") }) :
        t("No server settings match your search. Try a model name, connection, or storage setting.")}</p> : null}
      {!search.trim() || fields.some((field) => field.group === "Semantic search") ?
        <EmbeddingStatus key={selectedProfile || "none"} profileSelected={Boolean(selectedProfile)} /> : null}
      <div className="model-connections">
        {modelConnections.map(({ provider, key, title, description }) => {
          const connectionFields = [`${provider}_base_url`, key, `${provider}_model`]
            .flatMap((key) => fields.filter((field) => field.key === key));
          if (!connectionFields.length) return null;
          return <fieldset key={provider} disabled={busy} className="model-connection">
            <legend>{t(title)}</legend>
            <p className="caption">{t(description)} {t("Enter a local or hosted endpoint and its credentials.")}</p>
            {connectionFields.map((field) => <div key={field.key}>{control(field)}</div>)}
          </fieldset>;
        })}
      </div>
      {groups.map((group) => {
        const groupFields = otherFields.filter((field) => field.group === group);
        return <details key={group} open={search.trim() ? true : undefined}>
          <summary>
            <span className="configuration-group-heading">{t(optionsTitle(group))} <span className="caption">{groupFields.length} {groupFields.length === 1 ? t("setting") : t("settings")}</span></span>
            <small className="configuration-group-description">{t(groupDescriptions[group] || t("Additional server preferences."))}</small>
          </summary>
          <fieldset disabled={busy} className="configuration-fields">
            {groupFields.map((field) => <div key={field.key} className={field.kind === "routes" ? "configuration-routes" : ""}>
              {field.kind === "routes" ? <h3>{t(field.label)}</h3> : null}{control(field)}
            </div>)}
          </fieldset>
        </details>;
      })}
      <p className="caption">{t("Settings marked “restart required” apply the next time your server starts. Other changes apply when saved.")}</p>
      {error ? <ErrorState message={error} /> : null}
      {conflict ? <ErrorState message={conflict} /> : null}
      <div className={`settings-actions${dirty || busy ? " has-changes" : ""}`}>
        <p className="caption settings-save-status" role="status">{busy ? t("Saving server settings…") : resource.loading ?
          t("Checking current server settings…") : saved ? t("Server settings saved.") : dirty ?
          t("You have unsaved server changes.") : t("Server settings are up to date.")}
          {dirty && search.trim() ? t("Saving includes changes outside your search.") : ""}</p>
        {dirty || conflict ? <button type="button" className="button" disabled={busy || resource.loading} onClick={discard}>{t("Discard changes")}</button> : null}
        <button className="button dark" disabled={busy || resource.loading || !dirty || Boolean(conflict)}>{busy ? t("Saving…") : t("Save server settings")}</button>
      </div>
    </form> : null}
  </section>;
}
