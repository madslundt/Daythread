import { t } from "./i18n";
import { newId } from "./api";

export type ConfigurationField = {
  key: string;
  label: string;
  group: string;
  kind: "string" | "integer" | "number" | "boolean" | "secret" | "routes";
  nullable: boolean;
  restart_required: boolean;
  description: string;
  minimum?: number;
  maximum?: number;
};
export type ModelOverride = {
  model?: string | null;
  base_url?: string | null;
  revision?: string | null;
  token?: string | null;
};
export type ConfigurationData = {
  fields: ConfigurationField[];
  values: Record<string, string | number | boolean | null | Record<string, ModelOverride>>;
  secrets: Record<string, boolean>;
  revision: string;
  pending_restart: string[];
};
export type SecretEdit = { mode: "keep" | "replace" | "clear" | "inherit"; value: string };
export type RouteRow = ModelOverride & { id: string; name: string; secret: SecretEdit };
export type ConfigurationDraft = {
  values: Record<string, unknown>;
  routes: Record<string, RouteRow[]>;
  secrets: Record<string, SecretEdit>;
};
export const keepSecret = (): SecretEdit => ({ mode: "keep", value: "" });
export const tasks = ["chat", "summary", "extraction", "preparation", "weekly", "retrieval"];

export function configurationDraft(data: ConfigurationData): ConfigurationDraft {
  return {
    values: { ...data.values },
    routes: Object.fromEntries(data.fields.filter((field) => field.kind === "routes")
      .map((field) => [field.key, routeRows(data.values[field.key] as Record<string, ModelOverride>)])),
    secrets: {},
  };
}

function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  const a = left as Record<string, unknown>;
  const b = right as Record<string, unknown>;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) =>
    Object.hasOwn(b, key) && sameValue(a[key], b[key]));
}

export function configurationChanges(
  data: ConfigurationData, draft: ConfigurationDraft, hiddenFields: readonly string[] = [],
): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const field of data.fields) {
    // These settings belong to another form, which may have saved since this draft loaded.
    if (hiddenFields.includes(field.key)) continue;
    let value = draft.values[field.key];
    let previous: unknown = data.values[field.key];
    if (field.kind === "secret") {
      value = secretChange(draft.secrets[field.key] || keepSecret());
      if (value === undefined) continue;
      // Reads redact every saved key to null; an explicit inherit action must
      // still be sent even though it matches that redacted value.
      changes[field.key] = value;
      continue;
    } else if (field.kind === "routes") {
      value = routeChanges(draft.routes[field.key] || [], field.key === "llm_routes");
      previous = routeChanges(Object.entries(previous as Record<string, ModelOverride>)
        .map(([name, route]) => ({ ...route, name, secret: keepSecret() })), field.key === "llm_routes");
    } else if (field.nullable && (value === "" || value == null)) {
      value = null;
    } else if (field.kind === "number" || field.kind === "integer") {
      if (value === "" || value == null || !Number.isFinite(Number(value)))
        throw new Error(t("Enter a number for {value0}.", { value0: t(field.label) }));
      value = Number(value);
    }
    if (!sameValue(value, previous)) changes[field.key] = value;
  }
  return changes;
}

export function configurationIsDirty(
  data: ConfigurationData, draft: ConfigurationDraft, hiddenFields: readonly string[] = [],
): boolean {
  try {
    return Object.keys(configurationChanges(data, draft, hiddenFields)).length > 0;
  } catch {
    // An incomplete replacement key or route is still an unsaved edit.
    return true;
  }
}

export function canRebaseConfiguration(
  previous: ConfigurationData, latest: ConfigurationData, hiddenFields: readonly string[],
): boolean {
  // Only a known save in the other settings form can advance a dirty draft's revision.
  // Any visible server change keeps the old revision and requires an explicit discard.
  if (!hiddenFields.some((key) => !sameValue(previous.values[key], latest.values[key]))) return false;
  const visible = (data: ConfigurationData) => data.fields.filter((field) => !hiddenFields.includes(field.key));
  const before = visible(previous);
  const after = visible(latest);
  if (before.length !== after.length || !before.every((field) =>
    after.some((candidate) => candidate.key === field.key && candidate.kind === field.kind) &&
    sameValue(previous.values[field.key], latest.values[field.key]))) return false;
  return [...new Set([...Object.keys(previous.secrets), ...Object.keys(latest.secrets)])]
    .filter((key) => !hiddenFields.some((hidden) => key === hidden || key.startsWith(`${hidden}.`)))
    .every((key) => previous.secrets[key] === latest.secrets[key]);
}

export function matchingConfigurationFields(
  fields: ConfigurationField[], search: string, hiddenFields: readonly string[] = [],
): ConfigurationField[] {
  const query = search.trim().toLowerCase();
  return fields.filter((field) => !hiddenFields.includes(field.key) &&
    `${field.group} ${field.label} ${field.key} ${field.description} ${t(field.group)} ${t(field.label)} ${t(field.description)}`.toLowerCase().includes(query));
}

export function routeRows(value: Record<string, ModelOverride>): RouteRow[] {
  return Object.entries(value).map(([name, route]) => ({
    ...route, name, id: newId(), secret: keepSecret(),
  }));
}

export function secretChange(edit: SecretEdit): string | null | undefined {
  if (edit.mode === "keep") return undefined;
  if (edit.mode === "inherit") return null;
  if (edit.mode === "clear") return "";
  if (!edit.value) throw new Error(t("Enter the replacement key or choose another key action."));
  return edit.value;
}

export function routeChanges(rows: Omit<RouteRow, "id">[], llm: boolean): Record<string, ModelOverride> {
  const routes: Record<string, ModelOverride> = {};
  for (const row of rows) {
    const name = row.name.trim().toLowerCase();
    if (!/^[a-z-]{2,32}$/.test(name) || (llm && !tasks.includes(name)))
      throw new Error(t("Enter a language code/name for every STT route, or select an LLM task."));
    if (Object.hasOwn(routes, name)) throw new Error(t("The {value0} route is listed twice.", { value0: name }));
    const token = secretChange(row.secret);
    routes[name] = {
      model: row.model?.trim() || null,
      base_url: row.base_url?.trim() || null,
      ...(llm ? { revision: row.revision?.trim() || null } : {}),
      ...(token !== undefined ? { token } : {}),
    };
  }
  return routes;
}

