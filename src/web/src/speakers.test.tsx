import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SpeakersView, VoiceProfilesManager } from "./Speakers";
import { SpeakersRequests, VoiceProfilesRequests, type SpeakersData, type VoiceProfilesData } from "./speakersRequests";
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
const voices: VoiceProfilesData = {
  revision: "directory-v1", recognition: { state: "unvalidated", message: "Collect confirmed examples; recognition is not validated yet." },
  profiles: [{ id: "voice-1", name: "Mads", example_count: 1, conversation_count: 1,
    examples: [{ id: "example-1", scope: { kind: "meeting", id: "meeting-1" }, speaker_id: "s1", seconds: 15, created_at: "2026-10-17T10:00:00Z" }] }],
};

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

describe("confirmed voice learning requests", () => {
  it("keeps profile reads independent from in-flight speaker actions and does not learn on read", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify(voices)));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const speakers = new SpeakersRequests("meeting", "meeting");
    const directory = new VoiceProfilesRequests();
    const detecting = speakers.run("POST");
    await expect(directory.run("GET")).resolves.toEqual(voices);
    finish(new Response(JSON.stringify(data)));
    await expect(detecting).resolves.toEqual(data);
    expect(fetch.mock.calls[1][0]).toBe("/v1/voice-profiles");
    expect(fetch.mock.calls[1][1].method).toBe("GET");
    expect(fetch.mock.calls[1][1].body).toBeUndefined();
    expect(new Headers(fetch.mock.calls[1][1].headers).get("X-Omi-Profile")).toBe("alice");
  });

  it("sends an explicit new name or stable existing profile ID with the conversation version", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(data)));
    vi.stubGlobal("fetch", fetch);
    const speakers = new SpeakersRequests("source", "recording");
    await speakers.run("POST", "/s1/remember", { version: "v1", name: "Mads" });
    await speakers.run("POST", "/s2/remember", { version: "v2", profile_id: "voice-1" });
    await speakers.run("POST", "/s3/confirm", { version: "v3", profile_id: "voice-2" });
    expect(fetch.mock.calls.map(([url, options]) => [url, JSON.parse(options.body)])).toEqual([
      ["/v1/speakers/source/recording/s1/remember", { version: "v1", name: "Mads" }],
      ["/v1/speakers/source/recording/s2/remember", { version: "v2", profile_id: "voice-1" }],
      ["/v1/speakers/source/recording/s3/confirm", { version: "v3", profile_id: "voice-2" }],
    ]);
  });

  it("uses directory revisions and explicit profile/example IDs to forget learned voices", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(voices)));
    vi.stubGlobal("fetch", fetch);
    const directory = new VoiceProfilesRequests();
    await directory.forget("voice/id", "revision 1", "example/id");
    await directory.forget("voice/id", "revision 2");
    expect(fetch.mock.calls.map(([url, options]) => [url, options.method, options.body])).toEqual([
      ["/v1/voice-profiles/voice%2Fid/examples/example%2Fid?revision=revision+1", "DELETE", undefined],
      ["/v1/voice-profiles/voice%2Fid?revision=revision+2", "DELETE", undefined],
    ]);
  });

  it("discards old directory responses after a newer load, cancellation or profile switch", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...voices, revision: "new" })));
    vi.stubGlobal("fetch", fetch);
    selectProfile("alice");
    const directory = new VoiceProfilesRequests();
    const pending = directory.run("GET");
    await expect(directory.run("GET")).resolves.toMatchObject({ revision: "new" });
    finish(new Response(JSON.stringify(voices)));
    await expect(pending).resolves.toBeUndefined();
    selectProfile("bob");
    await expect(directory.forget("voice-1", "new")).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("surfaces sample quality errors and directory conflicts to the user", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: "At least 10 seconds of clean speech is required" }), { status: 422 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Saved voices changed; refresh before removing examples" }), { status: 409 })));
    await expect(new SpeakersRequests("source", "recording").run("POST", "/s1/remember", { version: "v1", name: "Mads" }))
      .rejects.toThrow("10 seconds of clean speech");
    await expect(new VoiceProfilesRequests().forget("voice", "old")).rejects.toThrow("Saved voices changed");
  });
});

describe("confirmed voice learning display", () => {
  const learning = { ...view, onRemember: () => {}, onConfirm: () => {}, onForget: () => {} };

  it("shows a suggested identity separately without renaming the current speaker or playback", () => {
    const html = renderToStaticMarkup(<SpeakersView {...learning} voices={{ ...voices, recognition: { state: "ready", message: "Suggestions require confirmation." } }}
      data={{ ...data, speakers: [{ ...data.speakers[1], suggestion: { profile_id: "voice-1", name: "Mads" } }], recognition: { state: "ready", message: "Suggestions require confirmation." } }} />);
    expect(html).toContain("Suggested: Mads");
    expect(html).toContain("Confirm Mads");
    expect(html).toContain("Listen to B only");
    expect(html).not.toContain("Listen to Mads only");
    expect(html).toContain("Leave B unchanged");
    expect(html).toContain("Voice profiles stay in the selected archive");
  });

  it("permits collecting confirmed examples while readiness is unvalidated and explains the limitation", () => {
    const html = renderToStaticMarkup(<SpeakersView {...learning} voices={voices} data={{ ...data, speakers: [data.speakers[0]], recognition: voices.recognition }} />);
    expect(html).toContain(voices.recognition.message);
    expect(html).toContain("Remember speaker for future conversations");
    expect(html).toContain("Confirm and remember voice");
    expect(html).not.toContain('disabled="">Confirm and remember voice');
    expect(html).toContain("A new profile remains separate even if its name matches another profile");
  });

  it("disables learning when recognition is disabled while keeping saved voice management available", () => {
    const html = renderToStaticMarkup(<SpeakersView {...learning} voices={voices} data={{ ...data, recognition: { state: "disabled", message: "Enable Remember speakers in advanced settings." } }} />);
    expect(html).toContain('disabled="">Confirm and remember voice');
    expect(html).toContain("Forget voice");
    expect(html).not.toContain('disabled="">Forget voice');
    expect(html).toContain("Enable Remember speakers in advanced settings.");
  });

  it("uses scope-specific readiness over a ready directory when the current recording is unvalidated", () => {
    const html = renderToStaticMarkup(<SpeakersView {...learning} voices={{ ...voices, recognition: { state: "ready", message: "Directory is ready" } }}
      data={{ ...data, recognition: { state: "unvalidated", message: "This recording uses an unevaluated model; suggestions remain off." },
        speakers: [{ ...data.speakers[1], suggestion: { profile_id: "voice-1", name: "Mads" } }] }} />);
    expect(html).toContain("This recording uses an unevaluated model; suggestions remain off.");
    expect(html).not.toContain("Directory is ready");
    expect(html).toContain('disabled="">Confirm Mads');
  });

  it("requires retained audio for confirming or remembering a voice", () => {
    const html = renderToStaticMarkup(<SpeakersView {...learning} voices={voices}
      data={{ ...data, audio_available: false, recognition: { state: "ready", message: "Ready" },
        speakers: [{ ...data.speakers[1], name: "Someone", suggestion: { profile_id: "voice-1", name: "Mads" } }] }} />);
    expect(html).toContain('disabled="">Confirm Mads');
    expect(html).toContain('disabled="">Confirm and remember voice');
    expect(html).not.toContain('disabled="">Forget voice');
  });

  it("shows saved examples and warns that changing conversation assignments removes them", () => {
    const html = renderToStaticMarkup(<SpeakersView {...learning} voices={voices} data={{ ...data,
      speakers: [{ ...data.speakers[0], voice_profile_id: "voice-1", remembered_example_id: "example-1" }] }} />);
    expect(html).toContain("Saved as a voice example");
    expect(html).toContain("Editing names or turns removes saved voice examples from this conversation");
    expect(html).toContain("Remove example");
    expect(html).toContain("Conversation names stay in their conversations");
    expect(html).toContain("2026-10-17");
  });

  it("lists duplicate names as distinct profiles and provides an empty state for the current archive", () => {
    const html = renderToStaticMarkup(<VoiceProfilesManager voices={{ ...voices, profiles: [...voices.profiles, { ...voices.profiles[0], id: "voice-2" }] }} busy={false} onForget={() => {}} />);
    expect(html).toContain("Saved voices (2)");
    expect(html.match(/Forget voice/g)).toHaveLength(2);
    const empty = renderToStaticMarkup(<VoiceProfilesManager voices={{ ...voices, profiles: [] }} busy={false} onForget={() => {}} />);
    expect(empty).toContain("No saved voices in this archive");
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
    expect(html).toContain('href="/settings/server"');
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
