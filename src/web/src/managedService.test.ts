import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MANAGED_SERVICE_ORIGIN, ManagedSession, isManagedOrigin,
  loadManagedAccount, managedMutationHeaders, managedServiceUrl,
} from "./managedService";
import { request, uploadRecording } from "./api";
import { streamChat } from "./chatStream";

const account = {
  id: "account-a", email: "a@example.com", name: "Alice", hosted_until: 1790000000,
  hosting_active: true, credits: { available_units: 12345, reserved_units: 200 },
};
const descriptor = {
  managed: true, public_url: MANAGED_SERVICE_ORIGIN,
  authentication: "cookie", account_url: "/account",
};

function browser(origin = MANAGED_SERVICE_ORIGIN, cookie = "daythread_csrf=csrf-test") {
  vi.stubGlobal("window", { location: { origin }, dispatchEvent: vi.fn() });
  vi.stubGlobal("document", { cookie });
}
function provider(value: unknown = account, status = 200) {
  const fetch = vi.fn(async (path: string, _options?: RequestInit) => new Response(
    JSON.stringify(path.endsWith("/client") ? descriptor : value),
    { status: path.endsWith("/client") ? 200 : status },
  ));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => vi.unstubAllGlobals());

describe("fixed managed service boundary", () => {
  it.each([
    "http://localhost:8787", "http://daythread.elcc.dk", "https://daythread.elcc.dk.attacker.test",
    "https://attacker.test", "https://daythread.elcc.dk:444", "",
  ])("never sends service requests or CSRF to another origin (%s)", async (origin) => {
    browser(origin);
    const fetch = provider();
    expect(isManagedOrigin()).toBe(false);
    expect(await loadManagedAccount()).toBeUndefined();
    expect(managedMutationHeaders("/v1/events", "POST").has("X-CSRF-Token")).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
    expect(managedServiceUrl("/signin")).toBe(`${MANAGED_SERVICE_ORIGIN}/signin`);
  });

  it("uses cookie credentials and rejects untrusted service descriptors", async () => {
    browser();
    const fetch = provider();
    await expect(loadManagedAccount()).resolves.toEqual(account);
    expect(fetch).toHaveBeenCalledWith("/service/v1/account", expect.objectContaining({
      credentials: "same-origin", mode: "same-origin", redirect: "error", cache: "no-store",
    }));
    expect(managedServiceUrl("/account")).toBe(`${MANAGED_SERVICE_ORIGIN}/account`);
    expect(() => managedServiceUrl("https://attacker.test" as "/signin")).toThrow();
    fetch.mockImplementation(async () => new Response(JSON.stringify({
      ...descriptor, public_url: "https://attacker.test",
    })));
    await expect(loadManagedAccount()).rejects.toThrow();
  });

  it("validates account balances before displaying them", async () => {
    browser();
    provider({ ...account, credits: { available_units: -1, reserved_units: 0 } });
    await expect(loadManagedAccount()).rejects.toThrow();
  });

  it("adds CSRF only to same-origin managed mutations, including archive requests", async () => {
    browser();
    const fetch = provider();
    await request("/v1/events", { method: "POST", body: "{}" });
    expect(new Headers(fetch.mock.calls[0][1]?.headers).get("X-CSRF-Token")).toBe("csrf-test");
    expect(managedMutationHeaders("/v1/status", "GET").has("X-CSRF-Token")).toBe(false);
    expect(managedMutationHeaders("https://attacker.test/v1/events", "POST").has("X-CSRF-Token")).toBe(false);
    expect(managedMutationHeaders("//attacker.test/v1/events", "POST").has("X-CSRF-Token")).toBe(false);
  });

  it("expires the account provider when an archive endpoint rejects its session", async () => {
    browser();
    provider({}, 401);
    await expect(request("/v1/status")).rejects.toThrow();
    expect(window.dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: "daythread:session-expired" }));
  });

  it("authenticates browser recording transfers with the same CSRF boundary", async () => {
    browser();
    const xhr = {
      open: vi.fn(), setRequestHeader: vi.fn(), send: vi.fn(), abort: vi.fn(), upload: {},
      onload: undefined as undefined | (() => void), onerror: undefined,
      status: 200, responseText: JSON.stringify({ id: "receipt" }),
    };
    vi.stubGlobal("XMLHttpRequest", class { constructor() { return xhr; } });
    const result = uploadRecording("/v1/audio/part", new Blob(["audio"]), vi.fn(), new AbortController().signal);
    expect(xhr.setRequestHeader).toHaveBeenCalledWith("x-csrf-token", "csrf-test");
    xhr.onload?.();
    await expect(result).resolves.toEqual({ id: "receipt" });
  });

  it("authenticates streamed chat and expires a rejected session", async () => {
    browser();
    const fetch = provider({}, 401);
    await expect(streamChat({}, vi.fn(), new AbortController().signal)).rejects.toThrow();
    expect(new Headers(fetch.mock.calls[0][1]?.headers).get("X-CSRF-Token")).toBe("csrf-test");
    expect(window.dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: "daythread:session-expired" }));
  });
});

describe("managed account lifecycle", () => {
  it("clears a previously loaded account when its cookie expires", async () => {
    browser();
    provider();
    const session = new ManagedSession();
    await session.refresh();
    expect(session.getSnapshot().account?.name).toBe("Alice");
    provider({ detail: "Session ended" }, 401);
    await session.refresh();
    expect(session.getSnapshot().status).toBe("signed-out");
    expect(session.getSnapshot().account).toBeUndefined();
  });

  it("clears cached balances on unavailable or invalid responses", async () => {
    browser();
    provider();
    const session = new ManagedSession();
    await session.refresh();
    provider({ detail: "Internal failure" }, 500);
    await session.refresh();
    expect(session.getSnapshot().status).toBe("unavailable");
    expect(session.getSnapshot().account).toBeUndefined();
  });

  it("signs out with a cookie-bound CSRF token and clears account state", async () => {
    browser();
    const fetch = provider();
    const session = new ManagedSession();
    await session.refresh();
    await session.signOut();
    expect(fetch).toHaveBeenCalledWith("/service/v1/auth/logout", expect.objectContaining({
      method: "POST", body: "{}", credentials: "same-origin",
    }));
    const call = fetch.mock.calls.find(([path]) => path.endsWith("/logout"));
    expect(new Headers(call?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-test");
    expect(session.getSnapshot().status).toBe("signed-out");
    expect(session.getSnapshot().account).toBeUndefined();
  });

  it("blocks a late account response from restoring a signed-out session", async () => {
    browser();
    let resolve!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn((path: string) => path.endsWith("/account")
      ? new Promise<Response>((done) => { resolve = done; })
      : Promise.resolve(new Response(JSON.stringify(path.endsWith("/client") ? descriptor : {})))));
    const session = new ManagedSession();
    const refresh = session.refresh();
    await session.signOut();
    resolve(new Response(JSON.stringify(account)));
    await refresh;
    expect(session.getSnapshot().status).toBe("signed-out");
    expect(session.getSnapshot().account).toBeUndefined();
  });

  it("obtains a fresh CSRF cookie before logout when necessary", async () => {
    browser(MANAGED_SERVICE_ORIGIN, "");
    const fetch = vi.fn(async (path: string, _options?: RequestInit) => {
      if (path.endsWith("/csrf")) document.cookie = "daythread_csrf=fresh-csrf";
      return new Response(JSON.stringify({}));
    });
    vi.stubGlobal("fetch", fetch);
    const session = new ManagedSession();
    await session.signOut();
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(["/service/v1/csrf", "/service/v1/auth/logout"]);
    expect(new Headers(fetch.mock.calls[1][1]?.headers).get("X-CSRF-Token")).toBe("fresh-csrf");
    expect(session.getSnapshot().status).toBe("signed-out");
  });
});
