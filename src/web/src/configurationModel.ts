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
  dimension?: number | null;
  query_prefix?: string | null;
  document_prefix?: string | null;
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
      value = routeChanges(draft.routes[field.key] || [], field.key === "llm_routes", field.key === "embedding_routes");
      previous = routeChanges(Object.entries(previous as Record<string, ModelOverride>)
        .map(([name, route]) => ({ ...route, name, secret: keepSecret() })), field.key === "llm_routes", field.key === "embedding_routes");
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

// Curate administration controls explicitly; new backend fields do not become UI automatically.
const advancedKeys = new Set([
  "stt_base_url", "stt_api_key", "stt_model", "stt_routes", "stt_chunk_seconds", "stt_filter_speech",
  "stt_enhancement_mode", "stt_enhancement_timeout_seconds", "stt_segment_timestamps",
  "llm_base_url", "llm_api_key", "llm_model", "llm_revision", "llm_routes", "llm_enable_thinking",
  "inference_token", "inference_timeout_seconds", "chat_query_expansion", "chat_query_expansion_timeout_seconds",
  "embedding_enabled", "embedding_base_url", "embedding_token", "embedding_model", "embedding_revision",
  "embedding_dimension", "embedding_timeout_seconds", "embedding_min_score",
  "embedding_routes", "embedding_query_prefix", "embedding_document_prefix",
  "embedding_chunking_strategy", "embedding_target_tokens", "embedding_max_tokens", "embedding_overlap_tokens",
  "embedding_max_input_tokens", "embedding_special_tokens", "embedding_tokenizer", "embedding_tokenizer_revision",
  "embedding_query_format", "embedding_query_instruction", "embedding_send_dimensions",
  "retrieval_candidate_limit", "retrieval_rrf_k", "retrieval_lexical_weight", "retrieval_semantic_weight",
  "retrieval_matches_per_source", "retrieval_evidence_character_budget",
  "reranker_enabled", "reranker_base_url", "reranker_model", "reranker_token", "reranker_timeout_seconds",
  "reranker_max_candidates", "reranker_max_passage_chars", "reranker_max_query_chars",
  "diarization_enabled", "diarization_base_url", "diarization_timeout_seconds",
  "voice_recognition_enabled", "voice_recognition_report", "omi_api_key", "pocket_api_key",
  "audio_forwarding_token", "worker_enabled",
]);

export function advancedConfigurationFields(fields: ConfigurationField[], values: Record<string, unknown>): ConfigurationField[] {
  return fields.filter(({ key }) => {
    if (!advancedKeys.has(key)) return false;
    if (key.startsWith("embedding_") && key !== "embedding_enabled") return values.embedding_enabled === true;
    if (key.startsWith("reranker_") && key !== "reranker_enabled") return values.reranker_enabled === true;
    if (key.startsWith("diarization_") && key !== "diarization_enabled") return values.diarization_enabled === true;
    if (key === "voice_recognition_enabled") return values.diarization_enabled === true;
    if (key === "voice_recognition_report") return values.diarization_enabled === true && values.voice_recognition_enabled === true;
    if (key === "chat_query_expansion_timeout_seconds") return values.chat_query_expansion === true;
    if (key === "stt_enhancement_timeout_seconds") return values.stt_enhancement_mode !== "off";
    return true;
  });
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

export function routeChanges(rows: Omit<RouteRow, "id">[], llm: boolean, embedding = false): Record<string, ModelOverride> {
  const routes: Record<string, ModelOverride> = {};
  for (const row of rows) {
    const name = row.name.trim().toLowerCase();
    if ((embedding ? !/^[a-z]{2,3}$/.test(name) : !/^[a-z-]{2,32}$/.test(name)) ||
        (llm && !tasks.includes(name)))
      throw new Error(t("Enter a valid language code for each embedding route, a language code/name for each STT route, or select an LLM task."));
    if (Object.hasOwn(routes, name)) throw new Error(t("The {value0} route is listed twice.", { value0: name }));
    const token = secretChange(row.secret);
    routes[name] = {
      model: row.model?.trim() || null,
      base_url: row.base_url?.trim() || null,
      ...(llm || embedding ? { revision: row.revision?.trim() || null } : {}),
      ...(embedding && row.dimension ? { dimension: Number(row.dimension) } : {}),
      ...(embedding ? {
        query_prefix: row.query_prefix?.trim() || null,
        document_prefix: row.document_prefix?.trim() || null,
      } : {}),
      ...(token !== undefined ? { token } : {}),
    };
  }
  return routes;
}

