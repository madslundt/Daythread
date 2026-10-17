import { afterEach, describe, expect, it, vi } from "vitest";
import { newId, request, uploadRecording } from "./api";

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

describe("recording transfers", () => {
  function transfer() {
    const xhr = {
      open: vi.fn(), setRequestHeader: vi.fn(), send: vi.fn(), abort: vi.fn(),
      upload: { onprogress: undefined as undefined | ((event: { loaded: number }) => void) },
      onload: undefined as undefined | (() => void), onerror: undefined as undefined | (() => void),
      status: 200, responseText: JSON.stringify({ id: "receipt" }),
    };
    vi.stubGlobal("XMLHttpRequest", class { constructor() { return xhr; } });
    return xhr;
  }
  it("reports bytes within a part while waiting for a valid receipt", async () => {
    const xhr = transfer(), progress = vi.fn();
    const body = new Blob(["0123456789"]);
    const pending = uploadRecording("/v1/audio/part", body, progress, new AbortController().signal,
      { id: "family", signal: new AbortController().signal });
    expect(xhr.setRequestHeader).toHaveBeenCalledWith("x-omi-profile", "family");
    expect(xhr.send).toHaveBeenCalledWith(body);
    xhr.upload.onprogress?.({ loaded: 4 });
    expect(progress).toHaveBeenCalledWith(4);
    xhr.onload?.();
    await expect(pending).resolves.toEqual({ id: "receipt" });
  });
  it("aborts an in-flight transfer when its profile changes", async () => {
    const xhr = transfer(), profile = new AbortController();
    const pending = uploadRecording("/v1/audio/part", new Blob(["audio"]), vi.fn(),
      new AbortController().signal, { id: "family", signal: profile.signal });
    profile.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(xhr.abort).toHaveBeenCalledOnce();
  });
  it("does not start transfers from an expired dialog", async () => {
    const xhr = transfer(), dialog = new AbortController();
    dialog.abort();
    await expect(uploadRecording("/v1/audio/part", new Blob(["audio"]), vi.fn(), dialog.signal))
      .rejects.toMatchObject({ name: "AbortError" });
    expect(xhr.send).not.toHaveBeenCalled();
  });
  it.each([
    [422, '{"detail":"Invalid recording"}', "Invalid recording"],
    [200, '<html>Error</html>', "invalid upload receipt"],
    [500, '<html>Error</html>', "could not be completed (500)"],
  ])("rejects unsuccessful or invalid receipts (%s)", async (status, responseText, message) => {
    const xhr = transfer();
    const pending = uploadRecording("/v1/audio/part", new Blob(["audio"]), vi.fn(), new AbortController().signal);
    xhr.status = status; xhr.responseText = responseText;
    xhr.onload?.();
    await expect(pending).rejects.toThrow(message);
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
