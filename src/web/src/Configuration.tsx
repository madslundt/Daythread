import { useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, newId, request } from "./api";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";
import { ModelPicker } from "./ModelPicker";
import {
  canRebaseConfiguration, configurationChanges, configurationDraft, configurationIsDirty,
  keepSecret, matchingConfigurationFields, tasks,
  type ConfigurationData, type ConfigurationDraft, type ConfigurationField,
  type RouteRow, type SecretEdit,
} from "./configurationModel";

function SecretControl({ id, label, configured, edit, onChange, route = false, description }: {
  id: string; label: string; configured: boolean; edit: SecretEdit;
  onChange: (edit: SecretEdit) => void; route?: boolean; description?: string;
}) {
  return <div className="setting-field">
    <label htmlFor={id}>{label} · {configured ? "Configured" : "Not set"}</label>
    <select id={id} value={edit.mode} aria-describedby={description ? `${id}-hint` : undefined} onChange={(event) =>
      onChange({ mode: event.target.value as SecretEdit["mode"], value: "" })}>
      <option value="keep">{configured ? "Keep current key" : route ? "Keep current setting" : "Leave key unset"}</option>
      <option value="replace">{configured ? "Replace key" : "Add key"}</option>
      {route ? <option value="inherit">Use shared key</option> : null}
      <option value="clear">{route ? "No authentication" : "Clear key"}</option>
    </select>
    {edit.mode === "replace" ? <input
      type="password" aria-label={`New ${label}`} value={edit.value}
      autoComplete="new-password" spellCheck={false}
      onChange={(event) => onChange({ ...edit, value: event.target.value })}
    /> : null}
    {description ? <small id={`${id}-hint`}>{description}</small> : null}
  </div>;
}

export function ModelRoutes({ field, rows, secrets, onChange, connectionDraft, revision }: {
  field: ConfigurationField; rows: RouteRow[]; secrets: Record<string, boolean>;
  onChange: (rows: RouteRow[]) => void;
  connectionDraft: ConfigurationDraft; revision: string;
}) {
  const llm = field.key === "llm_routes";
  function update(id: string, changes: Partial<RouteRow>) {
    onChange(rows.map((row) => row.id === id ? { ...row, ...changes } : row));
  }
  return <div className="model-routes">
    <p className="caption">{field.description}</p>
    {rows.map((row, index) => <fieldset className="model-route" key={row.id}>
      <legend>{llm ? "Task" : "Language"} route {index + 1}</legend>
      <div className="settings-grid">
        <label className="setting-field">
          <span>{llm ? "Task" : "Language code or detector name"}</span>
          {llm ? <select value={row.name} onChange={(event) => update(row.id, { name: event.target.value })}>
            {tasks.map((task) => <option key={task} value={task}>{task}</option>)}
          </select> : <input value={row.name} placeholder="da, en, danish, auto…" autoCapitalize="none"
            spellCheck={false} onChange={(event) => update(row.id, { name: event.target.value })} />}
        </label>
        <label className="setting-field"><span>Endpoint override</span>
          <input type="url" value={row.base_url || ""} placeholder="Use default endpoint"
            onChange={(event) => update(row.id, { base_url: event.target.value })} />
        </label>
        {llm ? <label className="setting-field"><span>Revision override</span>
          <input value={row.revision || ""} placeholder="Use default revision"
            onChange={(event) => update(row.id, { revision: event.target.value })} />
        </label> : null}
        <SecretControl id={`route-key-${row.id}`} label="Route key" route
          configured={Boolean(secrets[`${field.key}.${row.name.toLowerCase()}.token`])}
          edit={row.secret} onChange={(secret) => update(row.id, { secret })} />
        <ModelPicker id={`route-model-${row.id}`} label={`Model for ${row.name || `route ${index + 1}`}`}
          value={row.model || ""} provider={llm ? "llm" : "stt"} draft={connectionDraft} row={row}
          revision={revision} onChange={(model) => update(row.id, { model })} />
      </div>
      <button className="text-button" type="button" onClick={() => onChange(rows.filter((value) => value.id !== row.id))}>
        Remove route {row.name || index + 1}
      </button>
    </fieldset>)}
    <button type="button" className="button" disabled={llm && rows.length >= tasks.length}
      onClick={() => onChange([...rows, {
        id: newId(), name: llm ? tasks.find((task) => !rows.some((row) => row.name === task)) || "chat" : "",
        model: "", base_url: "", revision: "", secret: keepSecret(),
      }])}>Add {llm ? "task" : "language"} route</button>
  </div>;
}

const groupDescriptions: Record<string, string> = {
  "Transcription": "Turn recordings into text with your chosen speech model.",
  "Language models & chat": "Choose models for answers, summaries, and other tasks.",
  "Semantic search": "Find related conversations by meaning.",
  "Local inference": "Shared connection settings for your model services.",
  "Omi connections": "Credentials for optional Omi imports and audio forwarding.",
  "Capture & hints": "Fine-tune how flags and meeting gestures are interpreted.",
  "Archive & processing": "Control storage locations and background processing.",
};

export function Configuration({ onSaved, hiddenFields = [], search: controlledSearch,
  onSearchResults, onDirtyChange, refreshRevision = 0 }: {
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
        setConflict("Server settings changed while you were editing. Your edits are still here. Discard changes to load the current settings before making further changes.");
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
    if (field.key === "stt_model" || field.key === "llm_model") return <ModelPicker
      id={id} label={field.label} description={field.description} value={String(draft!.values[field.key] || "")}
      provider={field.key === "stt_model" ? "stt" : "llm"} draft={draft!} revision={baseline!.revision}
      onChange={(value) => update(field.key, value)} />;
    if (field.kind === "routes") return <ModelRoutes field={field} rows={draft!.routes[field.key] || []}
      secrets={baseline!.secrets} connectionDraft={draft!} revision={baseline!.revision} onChange={(rows) => {
        setDraft((current) => current ? { ...current, routes: { ...current.routes, [field.key]: rows } } : current);
        setSaved(false); setError("");
      }} />;
    if (field.kind === "secret") return <SecretControl id={id} label={field.label}
      configured={Boolean(baseline!.secrets[field.key])} edit={draft!.secrets[field.key] || keepSecret()}
      description={field.description} onChange={(edit) => {
        setDraft((current) => current ? { ...current, secrets: { ...current.secrets, [field.key]: edit } } : current);
        setSaved(false); setError("");
      }} />;
    return <label className="setting-field" htmlFor={id}>
      <span id={`${id}-label`}>{field.label}{field.restart_required ? " · restart required" : ""}</span>
      {field.kind === "boolean" ? field.nullable ? <select id={id}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        value={draft!.values[field.key] == null ? "default" : String(draft!.values[field.key])}
        onChange={(event) => update(field.key, event.target.value === "default" ? null : event.target.value === "true")}>
        <option value="default">Model default</option><option value="true">Enabled</option><option value="false">Disabled</option>
      </select> : <select id={id} value={String(draft!.values[field.key] ?? false)}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        onChange={(event) => update(field.key, event.target.value === "true")}>
        <option value="true">Enabled</option><option value="false">Disabled</option>
      </select> : <input id={id} type={field.kind === "integer" || field.kind === "number" ? "number" : "text"}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        step={field.kind === "integer" ? 1 : "any"} min={field.minimum} max={field.maximum}
        value={String(draft!.values[field.key] ?? "")} spellCheck={false} autoCapitalize="none"
        onChange={(event) => update(field.key, event.target.value)} />}
      {field.description ? <small id={`${id}-hint`}>{field.description}</small> : null}
    </label>;
  }
  const fields = matchingConfigurationFields(baseline?.fields || [], search, hiddenFields);
  const groupOrder = ["Transcription", "Language models & chat", "Local inference", "Omi connections", "Semantic search", "Archive & processing"];
  const groups = [...new Set(fields.map((field) => field.group))].sort((a, b) => {
    const rank = (group: string) => groupOrder.includes(group) ? groupOrder.indexOf(group) : groupOrder.length;
    return rank(a) - rank(b);
  });
  useEffect(() => {
    onSearchResults?.(baseline && !resource.error ? fields.length : -1);
  }, [Boolean(baseline), resource.error, search, fields.length, onSearchResults]);
  return <section aria-label="Server configuration" className="server-configuration">
    <h2>Models &amp; server</h2>
    <p className="muted">Choose the services that transcribe, summarize, and search your recordings.
      Changes are shared by the iPhone app and web. Keys stay on your server and are never shown after saving.</p>
    {resource.loading && !baseline ? <Loading>Loading server settings…</Loading> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {baseline && draft ? <form onSubmit={save} aria-busy={busy}>
      {baseline.pending_restart.length ? <p role="status" className="notice">Restart your server to apply: {baseline.pending_restart.map((key) =>
        baseline.fields.find((field) => field.key === key)?.label || key.replaceAll("_", " ")).join(", ")}.</p> : null}
      {controlledSearch === undefined ? <label className="setting-field"><span>Find a server setting</span>
        <input type="search" value={localSearch} placeholder="Search models, keys, storage…"
          onChange={(event) => setLocalSearch(event.target.value)} />
      </label> : null}
      {search.trim() ? <p className="caption" role="status">{fields.length ?
        `${fields.length} matching server ${fields.length === 1 ? "setting" : "settings"}.` :
        "No server settings match your search. Try a model name, connection, or storage setting."}</p> : null}
      {groups.map((group) => {
        const groupFields = fields.filter((field) => field.group === group);
        return <details key={group} open={search.trim() ? true : undefined}>
          <summary>
            <span className="configuration-group-heading">{group} <span className="caption">{groupFields.length} {groupFields.length === 1 ? "setting" : "settings"}</span></span>
            <small className="configuration-group-description">{groupDescriptions[group] || "Additional server preferences."}</small>
          </summary>
          <fieldset disabled={busy} className="configuration-fields">
            {groupFields.map((field) => <div key={field.key} className={field.kind === "routes" ? "configuration-routes" : ""}>
              {field.kind === "routes" ? <h3>{field.label}</h3> : null}{control(field)}
            </div>)}
          </fieldset>
        </details>;
      })}
      <p className="caption">Settings marked “restart required” apply the next time your server starts. Other changes apply when saved.</p>
      {error ? <ErrorState message={error} /> : null}
      {conflict ? <ErrorState message={conflict} /> : null}
      <div className={`settings-actions${dirty || busy ? " has-changes" : ""}`}>
        <p className="caption settings-save-status" role="status">{busy ? "Saving server settings…" : resource.loading ?
          "Checking current server settings…" : saved ? "Server settings saved." : dirty ?
          "You have unsaved server changes." : "Server settings are up to date."}
          {dirty && search.trim() ? " Saving includes changes outside your search." : ""}</p>
        {dirty || conflict ? <button type="button" className="button" disabled={busy || resource.loading} onClick={discard}>Discard changes</button> : null}
        <button className="button dark" disabled={busy || resource.loading || !dirty || Boolean(conflict)}>{busy ? "Saving…" : "Save server settings"}</button>
      </div>
    </form> : null}
  </section>;
}
