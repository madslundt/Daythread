import { keepSecret, routeChanges, secretChange, type ConfigurationDraft, type RouteRow } from "./configurationModel";

export type ModelProvider = "stt" | "llm";
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
  if (provider === "llm" && includeModel && draft.values.llm_enable_thinking !== undefined)
    changes.llm_enable_thinking = draft.values.llm_enable_thinking;
  const sharedKey = secretChange(draft.secrets.inference_token || keepSecret());
  if (sharedKey !== undefined) changes.inference_token = sharedKey;
  const providerKey = secretChange(draft.secrets[`${provider}_api_key`] || keepSecret());
  if (providerKey !== undefined) changes[`${provider}_api_key`] = providerKey;
  if (!row) return { provider, changes };
  const route = row.name.trim().toLowerCase();
  // Omitted tokens keep the saved route key; null inherits the provider key;
  // an empty string intentionally sends no authentication.
  const routes = routeChanges([{ ...row, ...(includeModel ? {} : { model: null }), revision: null }], provider === "llm");
  changes[`${provider}_routes`] = routes;
  return { provider, route, changes };
}
