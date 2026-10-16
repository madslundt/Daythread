import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SpeakersView } from "./Speakers";
import { SpeakersRequests, type SpeakersData } from "./speakersRequests";
import { selectProfile } from "./profiles";

const data: SpeakersData = {
  state: "ready", configured: true, version: "v1", audio_available: true,
  scope: { kind: "meeting", id: "meeting" }, source_count: 3,
  speakers: [{ id: "s1", label: "A", name: "Mads", seconds: 30 }, { id: "s2", label: "B", name: null, seconds: 15 }],
  turns: [{ id: "t1", source_id: "recording", start: 10, end: 20, speaker_id: "s1", text: "Hello from Mads" },
    { id: "t2", source_id: "recording", start: 20, end: 25, speaker_id: "s2" }],
};
const view = { loading: false, busy: false, error: "", onDetect: () => {}, onRetry: () => {},
  onRename: () => {}, onListen: () => {}, onMerge: () => {}, onReassign: () => {} };

afterEach(() => { selectProfile("default"); vi.unstubAllGlobals(); });

describe("speaker scope requests", () => {
  it("loads without inference and explicitly detects with an independent POST", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new SpeakersRequests("meeting", "meeting/id");
    await expect(requests.run("GET")).resolves.toEqual(data);
    expect(fetch.mock.calls[0][0]).toBe("/v1/speakers/meeting/meeting%2Fid");
    expect(fetch.mock.calls[0][1].method).toBe("GET");
    expect(fetch.mock.calls[0][1].body).toBeUndefined();
    expect(new Headers(fetch.mock.calls[0][1].headers).get("X-Omi-Profile")).toBe("alice");
    await requests.run("POST");
    expect(fetch.mock.calls[1][1].method).toBe("POST");
    expect(fetch.mock.calls[1][1].body).toBeUndefined();
  });

  it("sends optimistic versions for naming, reassigning and merging speakers", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    const requests = new SpeakersRequests("source", "recording");
    await requests.run("PATCH", "/s1", { name: "Mads", version: "v1" });
    await requests.run("PATCH", "/turns/t1", { speaker_id: "s2", version: "v2" });
    await requests.run("POST", "/merge", { source_id: "s1", target_id: "s2", version: "v3" });
    expect(fetch.mock.calls.map(([url, options]) => [url, options.method, JSON.parse(options.body)])).toEqual([
      ["/v1/speakers/source/recording/s1", "PATCH", { name: "Mads", version: "v1" }],
      ["/v1/speakers/source/recording/turns/t1", "PATCH", { speaker_id: "s2", version: "v2" }],
      ["/v1/speakers/source/recording/merge", "POST", { source_id: "s1", target_id: "s2", version: "v3" }],
    ]);
  });

  it("discards detection completing after closure or refresh", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const requests = new SpeakersRequests("source", "recording");
    const pending = requests.run("POST");
    requests.cancel();
    finish(new Response(JSON.stringify(data)));
    await expect(pending).resolves.toBeUndefined();
  });

  it("ignores old mutation results after a newer lookup", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...data, version: "v2" }))));
    const requests = new SpeakersRequests("source", "recording");
    const pending = requests.run("PATCH", "/s1", { name: "Mads", version: "v1" });
    await expect(requests.run("GET")).resolves.toMatchObject({ version: "v2" });
    finish(new Response(JSON.stringify(data)));
    await expect(pending).resolves.toBeUndefined();
  });

  it("pins playback and requests to the original profile and blocks mutations after switching", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const requests = new SpeakersRequests("source", "recording");
    selectProfile("bob");
    await expect(requests.run("PATCH", "/s1", { name: "Mads", version: "v1" })).resolves.toBeUndefined();
    expect(fetch).not.toHaveBeenCalled();
    expect(requests.audioUrl("speaker/a", "v1")).toBe("/v1/speakers/source/recording/speaker%2Fa/audio?version=v1&profile_id=alice");
  });

  it("keeps service and version conflicts actionable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Speaker labels changed. Refresh and try again." }), { status: 409 })));
    await expect(new SpeakersRequests("source", "recording").run("PATCH", "/s1", { name: "Mads", version: "old" }))
      .rejects.toThrow("Speaker labels changed");
  });
});

describe("speaker display", () => {
  it("uses scoped names for playback and turns and exposes correction controls", () => {
    const html = renderToStaticMarkup(<SpeakersView {...view} data={data} />);
    expect(html).toContain("2 speakers detected");
    expect(html).toContain("Listen to Mads only");
    expect(html).toContain("Listen to B only");
    expect(html).not.toContain("Listen to A only");
    expect(html).toContain("<strong>Mads</strong>");
    expect(html).toContain("Hello from Mads");
    expect(html).toContain("meeting across 3 recordings");
    expect(html).toContain("Reset to A");
    expect(html).toContain("Merge turns");
    expect(html).toContain("Speaker for turn at 0:10");
  });

  it("retains speaker labels while disabling playback and edits for stale evidence", () => {
    const html = renderToStaticMarkup(<SpeakersView {...view} data={{ ...data, state: "stale", turns: data.turns.map(({ text: _, ...turn }) => turn) }} />);
    expect(html).toContain("<strong>Mads</strong>");
    expect(html).toContain("Detecting again replaces speaker labels, names and turn assignments");
    expect(html).toContain('disabled="">Listen to Mads only');
    expect(html).not.toContain("Hello from Mads");
    expect(html).toContain("The recordings or transcript changed");
  });

  it("explains local setup and unavailable original audio without running inference", () => {
    const html = renderToStaticMarkup(<SpeakersView {...view} data={{ ...data, state: "idle", configured: false, audio_available: false, speakers: [], turns: [] }} />);
    expect(html).toContain("Settings → Models &amp; server → Speaker detection");
    expect(html).toContain('href="#settings/server"');
    expect(html).toContain("Original audio is unavailable");
    expect(html).toContain('disabled="">Detect speakers');
  });

  it("keeps saved names available when audio expires but disables speaker playback", () => {
    const html = renderToStaticMarkup(<SpeakersView {...view} data={{ ...data, audio_available: false }} />);
    expect(html).toContain("Name for Mads");
    expect(html).toContain('disabled="">Listen to Mads only');
    expect(html).toContain("Save name");
  });

  it("reports empty detection and long-running inference", () => {
    const html = renderToStaticMarkup(<SpeakersView {...view} data={{ ...data, speakers: [], turns: [] }} />);
    expect(html).toContain("No speakers detected");
    const pending = renderToStaticMarkup(<SpeakersView {...view} data={data} busy detecting />);
    expect(pending).toContain("Detecting speakers…");
    expect(pending).toContain("This may take a few minutes");
  });
});
