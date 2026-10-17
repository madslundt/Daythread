// A trusted build-time destination. Account addresses are never user-entered.
export const MANAGED_SERVICE_ORIGIN = "https://daythread.elcc.dk";

export type ManagedAccount = {
  id: string;
  email: string;
  name: string;
  hosted_until: number;
  hosting_active: boolean;
  credits: { available_units: number; reserved_units: number };
};
export type ManagedSnapshot = {
  managed: boolean;
  status: "local" | "loading" | "signed-in" | "signed-out" | "unavailable";
  account?: ManagedAccount;
  signingOut?: boolean;
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

async function serviceRequest(path: string, signal?: AbortSignal, method = "GET"): Promise<unknown> {
  if (!isManagedOrigin()) throw new ManagedServiceError("unavailable");
  const response = await fetch(path, {
    method, credentials: "same-origin", mode: "same-origin", redirect: "error", cache: "no-store",
    signal, headers: managedMutationHeaders(path, method, {
      Accept: "application/json", ...(method !== "GET" ? { "Content-Type": "application/json" } : {}),
    }), ...(method !== "GET" ? { body: "{}" } : {}),
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
  const client = object(clientValue), account = object(accountValue), credits = object(account.credits);
  if (client.managed !== true || client.public_url !== MANAGED_SERVICE_ORIGIN
      || client.authentication !== "cookie" || client.account_url !== "/account"
      || ![account.id, account.email, account.name].every((value) => typeof value === "string" && value.length > 0)
      || typeof account.hosting_active !== "boolean" || !integer(account.hosted_until)
      || !integer(credits.available_units) || !integer(credits.reserved_units))
    throw new ManagedServiceError("unavailable");
  return {
    id: account.id as string, email: account.email as string, name: account.name as string,
    hosted_until: account.hosted_until, hosting_active: account.hosting_active,
    credits: { available_units: credits.available_units, reserved_units: credits.reserved_units },
  };
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
    if (!isManagedOrigin() || this.snapshot.signingOut) return;
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
