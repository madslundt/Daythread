import { keepSecret, routeChanges, secretChange, type ConfigurationDraft, type RouteRow } from "./configurationModel";

export type ModelProvider = "stt" | "llm" | "embedding";
export type ModelConnectionRequest = {
  provider: ModelProvider;
  route?: string;
  changes: Record<string, unknown>;
};
export type ModelConnectionResult = { ok: boolean; message: string; latency_ms: number };

// Preview only the relevant connection, so an unfinished edit elsewhere does not
// block discovery or accidentally get included in a test request.
export function modelConnectionRequest(
  draft: ConfigurationDraft, provider: ModelProvider, row?: RouteRow, includeModel = true,
): ModelConnectionRequest {
  const changes: Record<string, unknown> = {
    [`${provider}_base_url`]: String(draft.values[`${provider}_base_url`] ?? "").trim(),
  };
  if (includeModel) changes[`${provider}_model`] = String(draft.values[`${provider}_model`] ?? "").trim();
  if (provider === "embedding" && includeModel && !row)
    changes.embedding_dimension = Number(draft.values.embedding_dimension || 0);
  if (provider === "llm" && includeModel && draft.values.llm_enable_thinking !== undefined)
    changes.llm_enable_thinking = draft.values.llm_enable_thinking;
  const sharedKey = provider === "embedding" ? undefined : secretChange(draft.secrets.inference_token || keepSecret());
  if (sharedKey !== undefined) changes.inference_token = sharedKey;
  const keyField = provider === "embedding" ? "embedding_token" : `${provider}_api_key`;
  const providerKey = secretChange(draft.secrets[keyField] || keepSecret());
  if (providerKey !== undefined) changes[keyField] = providerKey;
  if (!row) return { provider, changes };
  const route = row.name.trim().toLowerCase();
  // Omitted tokens keep the saved route key; null inherits the provider key;
  // an empty string intentionally sends no authentication.
  const routes = routeChanges(
    [{ ...row, ...(includeModel ? {} : { model: null })}],
    provider === "llm", provider === "embedding",
  );
  changes[`${provider}_routes`] = routes;
  return { provider, route, changes };
}
