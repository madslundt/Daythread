export type Profile = { id: string; name: string };
export type ProfileDirectory = { profiles: Profile[]; default_profile_id: string };
export type ProfileScope = { id: string; signal: AbortSignal };
const selectionKey = "omi.profile.v1";
let controller = new AbortController();
let selected = "default";

export function savedProfile(): string | null {
  try { return window.localStorage.getItem(selectionKey); } catch { return null; }
}

export function selectProfile(id: string): void {
  if (selected !== id) {
    controller.abort();
    controller = new AbortController();
    selected = id;
  }
  try { window.localStorage.setItem(selectionKey, id); } catch { /* Selection remains available for this visit. */ }
}

export function profileScope(): ProfileScope {
  return { id: selected, signal: controller.signal };
}

export function profileStorageKey(key: string): string {
  // Preserve existing browser preferences for the original archive.
  return selected === "default" ? key : `${key}:profile:${selected}`;
}

export function globalEndpoint(path: string): boolean {
  const route = path.split("?")[0];
  return ["/v1/profiles", "/v1/settings", "/v1/configuration"].some((base) => route === base || route.startsWith(`${base}/`));
}

export function profileRequestOptions(path: string, options: RequestInit, scope = profileScope()): RequestInit {
  const headers = new Headers(options.headers);
  if (globalEndpoint(path)) headers.delete("X-Omi-Profile");
  else headers.set("X-Omi-Profile", scope.id);
  return {
    ...options,
    cache: "no-store",
    headers,
    ...(!globalEndpoint(path) ? { signal: options.signal ? AbortSignal.any([scope.signal, options.signal]) : scope.signal } : {}),
  };
}

export function profileResourceUrl(path: string, id = selected): string {
  const [route, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  params.set("profile_id", id);
  return `${route}?${params}`;
}
