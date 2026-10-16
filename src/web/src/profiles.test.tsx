import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { audioUrl, request, rewindAudioUrl } from "./api";
import { profileResourceUrl, profileScope, profileStorageKey, savedProfile, selectProfile } from "./profiles";
import { ProfileSelector } from "./ProfileSelector";

afterEach(() => { selectProfile("default"); vi.unstubAllGlobals(); });

function mockFetch() {
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ ok: true })));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("profile isolation", () => {
  it("selects a profile for each API call, including writes", async () => {
    const fetch = mockFetch();
    selectProfile("alice");
    await request("/v1/transcripts");
    selectProfile("bob");
    await request("/v1/events", { method: "POST", body: "{}", headers: { Accept: "application/json" } });
    const first = fetch.mock.calls[0][1] as RequestInit;
    const second = fetch.mock.calls[1][1] as RequestInit;
    expect(new Headers(first.headers).get("X-Omi-Profile")).toBe("alice");
    expect(new Headers(second.headers).get("X-Omi-Profile")).toBe("bob");
    expect(new Headers(second.headers).get("Content-Type")).toBe("application/json");
    expect(new Headers(second.headers).get("Accept")).toBe("application/json");
    expect(first.cache).toBe("no-store");
    expect(second.cache).toBe("no-store");
  });

  it("keeps settings, model configuration and the directory global", async () => {
    const fetch = mockFetch();
    selectProfile("alice");
    for (const path of ["/v1/settings", "/v1/configuration", "/v1/configuration/models", "/v1/profiles"]) {
      await request(path, { headers: { "X-Omi-Profile": "removed-profile" } });
    }
    for (const [, options] of fetch.mock.calls) expect(new Headers(options.headers).has("X-Omi-Profile")).toBe(false);
  });

  it("puts the profile on playback and exports, preserving other query parameters", () => {
    selectProfile("alice");
    expect(audioUrl("source/id")).toBe("/v1/audio/source%2Fid?profile_id=alice");
    const rewind = new URL(rewindAudioUrl(15, "2026-10-16T18:30:00Z"), "http://local");
    expect(Object.fromEntries(rewind.searchParams)).toEqual({ minutes: "15", end: "2026-10-16T18:30:00Z", profile_id: "alice" });
    expect(profileResourceUrl("/v1/diary/2026-10-16.md")).toBe("/v1/diary/2026-10-16.md?profile_id=alice");
    expect(profileResourceUrl("/v1/weekly-reviews/2026-10-12.md?version_id=old")).toBe("/v1/weekly-reviews/2026-10-12.md?version_id=old&profile_id=alice");
    selectProfile("bob");
    expect(audioUrl("source/id")).toContain("profile_id=bob");
  });

  it("rejects stale responses even when a transport ignores cancellation", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    selectProfile("alice");
    const pending = request("/v1/transcripts");
    selectProfile("bob");
    finish(new Response(JSON.stringify({ items: ["alice's private content"] })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("prevents a delayed upload from moving into the newly selected profile", async () => {
    const fetch = mockFetch();
    selectProfile("alice");
    const upload = profileScope();
    selectProfile("bob");
    await expect(request("/v1/audio/recording", { method: "PUT", body: new Blob(["audio"]) }, upload)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("stores selection per browser and isolates remembered chat and recap state", () => {
    const storage = new Map<string, string>();
    vi.stubGlobal("window", { localStorage: { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) } });
    selectProfile("default");
    expect(profileStorageKey("omi.chat-session.v1")).toBe("omi.chat-session.v1");
    selectProfile("alice");
    expect(savedProfile()).toBe("alice");
    const aliceKey = profileStorageKey("omi.chat-session.v1");
    selectProfile("bob");
    expect(profileStorageKey("omi.chat-session.v1")).not.toBe(aliceKey);
    expect(savedProfile()).toBe("bob");
  });

  it("shows a named accessible selector and profile creation action", () => {
    const html = renderToStaticMarkup(<ProfileSelector profiles={[{ id: "default", name: "Default" }, { id: "alice", name: "Alice" }]} selected="alice" onSelect={() => {}} onCreate={() => {}} />);
    expect(html).toContain('<label for="active-profile">Profile</label>');
    expect(html).toContain('<option value="alice" selected="">Alice</option>');
    expect(html).toContain("Create profile");
  });
});
