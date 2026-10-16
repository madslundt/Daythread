import { afterEach, describe, expect, it, vi } from "vitest";
import { newId, request } from "./api";

afterEach(() => vi.unstubAllGlobals());
describe("API failures", () => {
  it("retains the backend detail for a model failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: "Local inference is unavailable" }),
            { status: 502 },
          ),
        ),
    );
    await expect(request("/v1/rewind")).rejects.toThrow(
      "Local inference is unavailable",
    );
  });
  it("distinguishes an unreachable home server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(request("/v1/status")).rejects.toThrow(
      "local backend could not be reached",
    );
  });
  it("does not expose a server HTML error page", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>Bad gateway</html>", { status: 502 }),
        ),
    );
    await expect(request("/v1/rewind")).rejects.toThrow(
      "Your local model is unavailable",
    );
  });
  it("generates unique valid UUIDs without requiring secure-context randomUUID", () => {
    const first = newId();
    expect(first).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(first).not.toBe(newId());
  });
});

describe("decision privacy", () => {
  it("bypasses browser HTTP caches for online decision content", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0 })));
    vi.stubGlobal("fetch", fetch);
    await request("/v1/decisions?day=2026-10-16");
    expect(fetch).toHaveBeenCalledWith("/v1/decisions?day=2026-10-16", expect.objectContaining({ cache: "no-store" }));
  });
});
