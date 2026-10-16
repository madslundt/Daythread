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
export const keepSecret = (): SecretEdit => ({ mode: "keep", value: "" });
export const tasks = ["chat", "summary", "extraction", "preparation", "weekly", "retrieval"];

export function routeRows(value: Record<string, ModelOverride>): RouteRow[] {
  return Object.entries(value).map(([name, route]) => ({
    ...route, name, id: newId(), secret: keepSecret(),
  }));
}

export function secretChange(edit: SecretEdit): string | null | undefined {
  if (edit.mode === "keep") return undefined;
  if (edit.mode === "inherit") return null;
  if (edit.mode === "clear") return "";
  if (!edit.value) throw new Error("Enter the replacement key or choose another key action.");
  return edit.value;
}

export function routeChanges(rows: RouteRow[], llm: boolean): Record<string, ModelOverride> {
  const routes: Record<string, ModelOverride> = {};
  for (const row of rows) {
    const name = row.name.trim().toLowerCase();
    if (!/^[a-z-]{2,32}$/.test(name) || (llm && !tasks.includes(name)))
      throw new Error("Enter a language code/name for every STT route, or select an LLM task.");
    if (Object.hasOwn(routes, name)) throw new Error(`The ${name} route is listed twice.`);
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
import { newId } from "./api";

