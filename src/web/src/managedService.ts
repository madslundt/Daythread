// A trusted build-time destination. Account addresses are never user-entered.
export const MANAGED_SERVICE_ORIGIN = "https://app.daythread.elcc.dk";

export const managedKinds = ["stt", "llm", "embedding"] as const;
export type ManagedKind = typeof managedKinds[number];
export type ManagedMode = "managed" | "own" | "off";
export type ManagedConnection = { mode: ManagedMode; provider: string; model: string; has_key: boolean; options: Record<string, string | number> };
export type ManagedConnectionUpdate = { mode: ManagedMode; provider?: string; model?: string; key?: string; key_from?: ManagedKind; options?: Record<string, string | number> };
export type ManagedProvider = { id: string; label: string; kinds: ManagedKind[] };

export type ManagedAccount = {
  id: string;
  email: string;
  name: string;
  hosted_until: number;
  hosting_active: boolean;
  archive_access?: boolean;
  credits: { available_units: number; reserved_units: number };
  inference?: Record<ManagedKind, ManagedConnection>;
  providers?: ManagedProvider[];
  managed_available?: Partial<Record<ManagedKind, boolean>>;
  rates?: { models: Partial<Record<ManagedKind, { model: string }>> };
};
export function managedArchiveAvailable(account?: ManagedAccount): boolean {
  return Boolean(account && (account.archive_access ?? account.hosted_until > 0));
}
export type ManagedSnapshot = {
  managed: boolean;
  status: "local" | "loading" | "signed-in" | "signed-out" | "unavailable";
  account?: ManagedAccount;
  signingOut?: boolean;
  updatingModels?: boolean;
};

class ManagedServiceError extends Error {
  constructor(readonly code: "signed-out" | "unavailable") { super(code); }
}

export function isManagedOrigin(): boolean {
  return typeof window !== "undefined" && window.location.origin === MANAGED_SERVICE_ORIGIN;
}

export function managedServiceUrl(path: "/signin" | "/account" | "/pricing"): string {
  if (path !== "/signin" && path !== "/account" && path !== "/pricing") throw new ManagedServiceError("unavailable");
  return MANAGED_SERVICE_ORIGIN + path;
}

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const item = document.cookie.split(";").map((value) => value.trim())
    .find((value) => value.startsWith("daythread_csrf="));
  try { return item ? decodeURIComponent(item.slice("daythread_csrf=".length)) : ""; }
  catch { return ""; }
}

export function managedMutationHeaders(path: string, method = "GET", initial?: HeadersInit): Headers {
  const headers = new Headers(initial);
  if (isManagedOrigin() && /^\/(?:v1|service\/v1)\//.test(path)
      && ["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase())) {
    const csrf = csrfToken();
    if (csrf) headers.set("x-csrf-token", csrf);
  }
  return headers;
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ManagedServiceError("unavailable");
  return value as Record<string, unknown>;
}

function integer(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

async function serviceRequest(path: string, signal?: AbortSignal, method = "GET", body: unknown = {}): Promise<unknown> {
  if (!isManagedOrigin()) throw new ManagedServiceError("unavailable");
  const response = await fetch(path, {
    method, credentials: "same-origin", mode: "same-origin", redirect: "error", cache: "no-store",
    signal, headers: managedMutationHeaders(path, method, {
      Accept: "application/json", ...(method !== "GET" ? { "Content-Type": "application/json" } : {}),
    }), ...(method !== "GET" ? { body: JSON.stringify(body) } : {}),
  });
  signal?.throwIfAborted();
  if (!response.ok || response.redirected)
    throw new ManagedServiceError(response.status === 401 ? "signed-out" : "unavailable");
  return response.status === 204 ? undefined : response.json();
}

export async function loadManagedAccount(signal?: AbortSignal): Promise<ManagedAccount | undefined> {
  if (!isManagedOrigin()) return;
  const [clientValue, accountValue] = await Promise.all([
    serviceRequest("/service/v1/client", signal), serviceRequest("/service/v1/account", signal),
  ]);
  signal?.throwIfAborted();
  const client = object(clientValue);
  if (client.managed !== true || client.public_url !== MANAGED_SERVICE_ORIGIN
      || client.authentication !== "cookie" || client.account_url !== "/account")
    throw new ManagedServiceError("unavailable");
  return parseManagedAccount(accountValue);
}

function parseManagedAccount(value: unknown): ManagedAccount {
  const account = object(value), credits = object(account.credits);
  if (![account.id, account.email, account.name].every((value) => typeof value === "string" && value.length > 0)
      || typeof account.hosting_active !== "boolean" || !integer(account.hosted_until)
      || (account.archive_access !== undefined && typeof account.archive_access !== "boolean")
      || !integer(credits.available_units) || !integer(credits.reserved_units))
    throw new ManagedServiceError("unavailable");
  return {
    id: account.id as string, email: account.email as string, name: account.name as string,
    hosted_until: account.hosted_until, hosting_active: account.hosting_active,
    archive_access: account.archive_access as boolean | undefined,
    credits: { available_units: credits.available_units, reserved_units: credits.reserved_units },
    ...modelMetadata(account),
  };
}

function modelMetadata(account: Record<string, unknown>): Partial<ManagedAccount> {
  if (account.inference === undefined) return {};
  const raw = object(account.inference);
  const inference = {} as Record<ManagedKind, ManagedConnection>;
  for (const kind of managedKinds) {
    const connection = object(raw[kind]), options = object(connection.options ?? {});
    if (!["managed", "own", "off"].includes(String(connection.mode))
        || typeof connection.provider !== "string" || typeof connection.model !== "string"
        || typeof connection.has_key !== "boolean"
        || Object.values(options).some((value) => typeof value !== "string" && (typeof value !== "number" || !Number.isFinite(value))))
      throw new ManagedServiceError("unavailable");
    inference[kind] = { mode: connection.mode as ManagedMode, provider: connection.provider,
      model: connection.model, has_key: connection.has_key, options: options as Record<string, string | number> };
  }
  const providers = Array.isArray(account.providers) ? account.providers.map((value) => {
    const provider = object(value);
    if (typeof provider.id !== "string" || typeof provider.label !== "string" || !Array.isArray(provider.kinds))
      throw new ManagedServiceError("unavailable");
    const kinds = provider.kinds;
    return { id: provider.id, label: provider.label, kinds: managedKinds.filter((kind) => kinds.includes(kind)) };
  }) : [];
  const available = account.managed_available === undefined ? {} : object(account.managed_available);
  const rawModels = account.rates === undefined ? {} : object(object(account.rates).models ?? {});
  const models: Partial<Record<ManagedKind, { model: string }>> = {};
  const managed_available: Partial<Record<ManagedKind, boolean>> = {};
  for (const kind of managedKinds) {
    if (typeof available[kind] === "boolean") managed_available[kind] = available[kind];
    if (rawModels[kind] !== undefined) {
      const model = object(rawModels[kind]);
      if (typeof model.model !== "string") throw new ManagedServiceError("unavailable");
      models[kind] = { model: model.model };
    }
  }
  return { inference, providers, managed_available, rates: { models } };
}

export async function saveManagedConnection(kind: ManagedKind, update: ManagedConnectionUpdate): Promise<ManagedAccount> {
  if (!managedKinds.includes(kind) || !["managed", "own", "off"].includes(update.mode))
    throw new ManagedServiceError("unavailable");
  return parseManagedAccount(await serviceRequest(`/service/v1/inference/${kind}`, undefined, "PUT", update));
}

/** One in-memory account per mounted app; never persisted alongside archive preferences. */
export class ManagedSession {
  private snapshot: ManagedSnapshot = {
    managed: isManagedOrigin(), status: isManagedOrigin() ? "loading" : "local",
  };
  private listeners = new Set<() => void>();
  private controller?: AbortController;
  private revision = 0;
  readonly getSnapshot = (): ManagedSnapshot => this.snapshot;
  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private publish(snapshot: ManagedSnapshot): void {
    this.snapshot = snapshot;
    this.listeners.forEach((listener) => listener());
  }
  readonly refresh = async (): Promise<void> => {
    if (!isManagedOrigin() || this.snapshot.signingOut || this.snapshot.updatingModels) return;
    this.controller?.abort();
    const controller = new AbortController(), revision = ++this.revision;
    this.controller = controller;
    try {
      const account = await loadManagedAccount(controller.signal);
      if (revision === this.revision && !controller.signal.aborted)
        this.publish({ managed: true, status: "signed-in", account });
    } catch (error) {
      if (revision === this.revision && !controller.signal.aborted)
        this.publish({ managed: true, status: error instanceof ManagedServiceError ? error.code : "unavailable" });
    }
  };
  readonly updateConnection = async (kind: ManagedKind, update: ManagedConnectionUpdate): Promise<void> => {
    if (this.snapshot.status !== "signed-in" || this.snapshot.updatingModels) throw new ManagedServiceError("unavailable");
    this.controller?.abort();
    const revision = ++this.revision, accountId = this.snapshot.account?.id;
    this.publish({ ...this.snapshot, updatingModels: true });
    try {
      const account = await saveManagedConnection(kind, update);
      if (revision === this.revision) {
        if (account.id !== accountId) throw new ManagedServiceError("unavailable");
        this.publish({ managed: true, status: "signed-in", account });
      }
    } catch (error) {
      if (revision === this.revision) {
        if (error instanceof ManagedServiceError && error.code === "signed-out") this.expire();
        else this.publish({ ...this.snapshot, updatingModels: false });
      }
      throw error;
    }
  };
  readonly expire = (): void => {
    this.dispose();
    this.publish({ managed: true, status: "signed-out" });
  };
  readonly signOut = async (): Promise<void> => {
    if (!isManagedOrigin() || this.snapshot.signingOut) return;
    this.dispose();
    const revision = this.revision;
    this.publish({ managed: true, status: "signed-out", signingOut: true });
    try {
      if (!csrfToken()) await serviceRequest("/service/v1/csrf");
      if (!csrfToken()) throw new ManagedServiceError("unavailable");
      await serviceRequest("/service/v1/auth/logout", undefined, "POST");
      if (revision === this.revision) this.publish({ managed: true, status: "signed-out" });
    } catch {
      if (revision === this.revision) this.publish({ managed: true, status: "unavailable" });
    }
  };
  readonly dispose = (): void => {
    ++this.revision;
    this.controller?.abort();
  };
}
