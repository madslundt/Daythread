import { useEffect, useState } from "react";
import { errorMessage, newId, request } from "./api";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";
import {
  keepSecret, routeChanges, routeRows, secretChange, tasks,
  type ConfigurationData, type ConfigurationField, type ModelOverride,
  type RouteRow, type SecretEdit,
} from "./configurationModel";

function SecretControl({ id, label, configured, edit, onChange, route = false }: {
  id: string; label: string; configured: boolean; edit: SecretEdit;
  onChange: (edit: SecretEdit) => void; route?: boolean;
}) {
  return <div className="setting-field">
    <label htmlFor={id}>{label} · {configured ? "Configured" : "Not set"}</label>
    <select id={id} value={edit.mode} onChange={(event) =>
      onChange({ mode: event.target.value as SecretEdit["mode"], value: "" })}>
      <option value="keep">Keep current key</option>
      <option value="replace">Replace key</option>
      {route ? <option value="inherit">Use shared key</option> : null}
      <option value="clear">{route ? "No authentication" : "Clear key"}</option>
    </select>
    {edit.mode === "replace" ? <input
      type="password" aria-label={`New ${label}`} value={edit.value}
      autoComplete="new-password" spellCheck={false}
      onChange={(event) => onChange({ ...edit, value: event.target.value })}
    /> : null}
  </div>;
}

export function ModelRoutes({ field, rows, secrets, onChange }: {
  field: ConfigurationField; rows: RouteRow[]; secrets: Record<string, boolean>;
  onChange: (rows: RouteRow[]) => void;
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
        <label className="setting-field"><span>Model override</span>
          <input value={row.model || ""} placeholder="Use default model"
            onChange={(event) => update(row.id, { model: event.target.value })} />
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

export function Configuration({ onSaved, hiddenFields = [] }: {
  onSaved: () => void; hiddenFields?: readonly string[];
}) {
  const resource = useResource<ConfigurationData>("/v1/configuration");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [routes, setRoutes] = useState<Record<string, RouteRow[]>>({});
  const [secrets, setSecrets] = useState<Record<string, SecretEdit>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [search, setSearch] = useState("");
  useEffect(() => {
    if (!resource.data) return;
    setValues(resource.data.values);
    setRoutes(Object.fromEntries(resource.data.fields.filter((field) => field.kind === "routes")
      .map((field) => [field.key, routeRows(resource.data!.values[field.key] as Record<string, ModelOverride>)])));
    setSecrets({});
  }, [resource.data]);

  function update(key: string, value: unknown) {
    setValues((current) => ({ ...current, [key]: value }));
    setSaved(false);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!resource.data || busy) return;
    setError(""); setSaved(false);
    try {
      const changes: Record<string, unknown> = {};
      for (const field of resource.data.fields) {
        let value = values[field.key];
        if (field.kind === "secret") {
          value = secretChange(secrets[field.key] || keepSecret());
          if (value === undefined) continue;
        } else if (field.kind === "routes") {
          value = routeChanges(routes[field.key] || [], field.key === "llm_routes");
        } else if (field.kind === "number" || field.kind === "integer") {
          if (value === "" || !Number.isFinite(Number(value))) throw new Error(`Enter a number for ${field.label}.`);
          value = Number(value);
        } else if (field.nullable && value === "") value = null;
        if (JSON.stringify(value) !== JSON.stringify(resource.data.values[field.key])) changes[field.key] = value;
      }
      if (!Object.keys(changes).length) { setSaved(true); return; }
      setBusy(true);
      const result = await request<ConfigurationData>("/v1/configuration", {
        method: "PATCH", body: JSON.stringify({ revision: resource.data.revision, changes }),
      });
      resource.setData(result); setSaved(true); onSaved();
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  function control(field: ConfigurationField) {
    const id = `config-${field.key}`;
    if (field.kind === "routes") return <ModelRoutes field={field} rows={routes[field.key] || []}
      secrets={resource.data!.secrets} onChange={(rows) => { setRoutes((current) => ({ ...current, [field.key]: rows })); setSaved(false); }} />;
    if (field.kind === "secret") return <SecretControl id={id} label={field.label}
      configured={Boolean(resource.data!.secrets[field.key])} edit={secrets[field.key] || keepSecret()}
      onChange={(edit) => { setSecrets((current) => ({ ...current, [field.key]: edit })); setSaved(false); }} />;
    return <label className="setting-field" htmlFor={id}>
      <span id={`${id}-label`}>{field.label}{field.restart_required ? " · restart required" : ""}</span>
      {field.kind === "boolean" ? field.nullable ? <select id={id}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        value={values[field.key] == null ? "default" : String(values[field.key])}
        onChange={(event) => update(field.key, event.target.value === "default" ? null : event.target.value === "true")}>
        <option value="default">Model default</option><option value="true">Enabled</option><option value="false">Disabled</option>
      </select> : <select id={id} value={String(values[field.key] ?? false)}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        onChange={(event) => update(field.key, event.target.value === "true")}>
        <option value="true">Enabled</option><option value="false">Disabled</option>
      </select> : <input id={id} type={field.kind === "integer" || field.kind === "number" ? "number" : "text"}
        aria-labelledby={`${id}-label`} aria-describedby={field.description ? `${id}-hint` : undefined}
        step={field.kind === "integer" ? 1 : "any"} min={field.minimum} max={field.maximum}
        value={String(values[field.key] ?? "")} spellCheck={false} autoCapitalize="none"
        onChange={(event) => update(field.key, event.target.value)} />}
      {field.description ? <small id={`${id}-hint`}>{field.description}</small> : null}
    </label>;
  }
  const groups = [...new Set(resource.data?.fields.map((field) => field.group) || [])];
  return <section aria-label="Server configuration" className="server-configuration">
    <h2>Models, keys & server settings</h2>
    <p className="muted">Shared by the app and web. Keys are write-only and stay on home-core.
      Settings marked “restart required” take effect the next time the backend starts.</p>
    {resource.loading ? <Loading>Loading server settings…</Loading> : null}
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.data ? <form onSubmit={save} aria-busy={busy}>
      {resource.data.pending_restart.length ? <p role="status" className="notice">Saved changes awaiting backend restart: {resource.data.pending_restart.join(", ")}.</p> : null}
      <label className="setting-field"><span>Find a server setting</span>
        <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} />
      </label>
      {groups.map((group) => {
        const fields = resource.data!.fields.filter((field) => !hiddenFields.includes(field.key) && field.group === group &&
          `${field.label} ${field.key} ${field.description}`.toLowerCase().includes(search.toLowerCase()));
        return fields.length ? <details key={group} open={search ? true : undefined}>
          <summary>{group}</summary>
          <fieldset disabled={busy} className="configuration-fields">
            {fields.map((field) => <div key={field.key} className={field.kind === "routes" ? "configuration-routes" : ""}>
              {field.kind === "routes" ? <h3>{field.label}</h3> : null}{control(field)}
              {field.kind === "secret" && field.description ? <p className="caption">{field.description}</p> : null}
            </div>)}
          </fieldset>
        </details> : null;
      })}
      {error ? <ErrorState message={error} /> : null}
      {saved ? <p role="status">Server settings saved on home-core.</p> : null}
      <div className="button-row">
        <button className="button dark" disabled={busy || resource.loading}>{busy ? "Saving…" : "Save server settings"}</button>
        <button type="button" className="text-button" disabled={busy} onClick={() => { setError(""); setSaved(false); resource.refresh(); }}>Reload server settings</button>
      </div>
    </form> : null}
  </section>;
}
