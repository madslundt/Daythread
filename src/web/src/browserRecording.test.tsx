import { afterEach, describe, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { renderToStaticMarkup } from "react-dom/server";
import { BrowserRecorder } from "./BrowserRecorder";
import { BrowserRecordingStore, capturedRecordingFile, microphoneAvailable, startMicrophoneCapture,
  type CapturedRecording } from "./browserRecording";
import { RecordingUploadQueue, type RecordingUploadAPI, type PreparedRecording, type PrepareRecording } from "./audioUpload";
import { setLanguagePreference } from "./i18n";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); setLanguagePreference("en"); });

const stored: CapturedRecording = {
  recording: { id: "recording-1", owner: "account-a/profile-a", startedAt: "2026-10-16T12:00:00.123Z",
    stoppedAt: "2026-10-16T12:00:02.123Z", name: "browser-recording-recording-1.webm", type: "audio/webm", size: 5 },
  audio: new Blob(["audio"], { type: "audio/webm" }),
};

describe("local browser recording outbox", () => {
  it("restores original audio, filename, timestamps and owner through a new store instance", async () => {
    const factory = new IDBFactory();
    await new BrowserRecordingStore(factory).save(stored);
    const reopened = new BrowserRecordingStore(factory);
    expect(await reopened.list(stored.recording.owner)).toEqual([stored.recording]);
    const restored = await reopened.load(stored.recording.owner, stored.recording.id);
    expect(await restored?.audio.text()).toBe("audio");
    const file = capturedRecordingFile(restored!);
    expect(file.name).toBe(stored.recording.name);
    expect(file.lastModified).toBe(Date.parse(stored.recording.stoppedAt));
    expect(file.type).toBe(stored.recording.type);
  });
  it("keeps saved recordings isolated across profiles and accounts, including deletion", async () => {
    const store = new BrowserRecordingStore(new IDBFactory());
    await store.save(stored);
    for (const owner of ["account-a/profile-b", "account-b/profile-a"]) {
      expect(await store.list(owner)).toEqual([]);
      expect(await store.load(owner, stored.recording.id)).toBeUndefined();
      await store.remove(owner, stored.recording.id);
    }
    expect(await store.load(stored.recording.owner, stored.recording.id)).toBeDefined();
    await store.remove(stored.recording.owner, stored.recording.id);
    expect(await store.list(stored.recording.owner)).toEqual([]);
    expect(await store.load(stored.recording.owner, stored.recording.id)).toBeUndefined();
  });
  it("retains failed uploads and resumes the same server part IDs after browser reload", async () => {
    const factory = new IDBFactory();
    const store = new BrowserRecordingStore(factory);
    await store.save(stored);
    let plan: Extract<PreparedRecording, { status: "upload" }> | undefined;
    const backend: RecordingUploadAPI = {
      lookup: async () => ({ duplicate: false }),
      prepare: vi.fn(async (body: PrepareRecording) => plan ??= { ...body, status: "upload", recording_id: "stable-recording",
        parts: body.digests.map((digest: string, index: number) => ({ id: `stable-part-${index}`, digest, uploaded: false })) }),
    };
    const send = vi.fn().mockRejectedValueOnce(new Error("Offline")).mockImplementation(async (path: string, body: Blob) => {
      const url = new URL(path, "https://localhost");
      return { id: url.pathname.split("/").at(-1), device_id: "manual-web", started_at: url.searchParams.get("started_at"),
        ended_at: new Date(Date.parse(url.searchParams.get("started_at")!) + (body.size - 44) / 32).toISOString() };
    });
    const scope = { id: "profile-a", signal: new AbortController().signal };
    async function attempt() {
      const value = await new BrowserRecordingStore(factory).load(stored.recording.owner, stored.recording.id);
      const queue = new RecordingUploadQueue(async () => new Float32Array(32_000), send, backend);
      await queue.upload({ file: capturedRecordingFile(value!), recordedAt: value!.recording.startedAt, timeReference: "start" },
        "", scope.signal, scope, vi.fn(), vi.fn());
      await store.remove(stored.recording.owner, stored.recording.id);
    }
    await expect(attempt()).rejects.toThrow("Offline");
    expect(await store.list(stored.recording.owner)).toHaveLength(1);
    await attempt();
    expect(send.mock.calls[0][0]).toBe(send.mock.calls[1][0]);
    const query = new URL(send.mock.calls[1][0], "https://localhost").searchParams;
    expect(query.get("started_at")).toBe(stored.recording.startedAt);
    expect(query.get("recording_ended_at")).toBe(stored.recording.stoppedAt);
    expect(await store.list(stored.recording.owner)).toEqual([]);
  });
});

class FakeRecorder {
  static supported = "audio/webm;codecs=opus";
  static isTypeSupported = (type: string) => type === FakeRecorder.supported;
  static latest: FakeRecorder;
  state = "inactive";
  mimeType: string;
  ondataavailable?: (event: { data: Blob }) => void;
  onstop?: () => void;
  onerror?: () => void;
  constructor(_stream: MediaStream, options?: { mimeType: string }) {
    this.mimeType = options?.mimeType ?? "audio/webm";
    FakeRecorder.latest = this;
  }
  start() { this.state = "recording"; }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["final"], { type: this.mimeType }) });
    this.onstop?.();
  }
}
function microphone() {
  const stop = vi.fn();
  const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] }));
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  return { stop, getUserMedia };
}

describe("microphone capture lifecycle", () => {
  it("combines the final stop data with earlier chunks, preserving UTC capture time and releasing the microphone", async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(stored.recording.startedAt));
    const { stop, getUserMedia } = microphone();
    const finished = vi.fn(), failed = vi.fn();
    const capture = await startMicrophoneCapture("owner", finished, failed);
    FakeRecorder.latest.ondataavailable?.({ data: new Blob(["first"]) });
    vi.setSystemTime(new Date(stored.recording.stoppedAt));
    capture.stop(); capture.stop();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(finished).toHaveBeenCalledOnce();
    const result = finished.mock.calls[0][0] as CapturedRecording;
    expect(await result.audio.text()).toBe("firstfinal");
    expect(result.recording.startedAt).toBe(stored.recording.startedAt);
    expect(result.recording.stoppedAt).toBe(stored.recording.stoppedAt);
    expect(result.recording.owner).toBe("owner");
    expect(result.recording.name).toMatch(/^browser-recording-.+\.webm$/);
    expect(stop).toHaveBeenCalledOnce();
    expect(failed).not.toHaveBeenCalled();
  });
  it("handles permission denial and unsupported insecure origins without starting capture", async () => {
    const { getUserMedia } = microphone();
    getUserMedia.mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    await expect(startMicrophoneCapture("owner", vi.fn(), vi.fn())).rejects.toMatchObject({ name: "NotAllowedError" });
    vi.stubGlobal("navigator", {});
    expect(microphoneAvailable()).toBe(false);
    await expect(startMicrophoneCapture("owner", vi.fn(), vi.fn())).rejects.toThrow("HTTPS or localhost");
  });
  it("keeps captured data when the microphone fails and automatically stops at the duration limit", async () => {
    vi.useFakeTimers();
    const { stop } = microphone();
    const finished = vi.fn(), failed = vi.fn();
    await startMicrophoneCapture("owner", finished, failed);
    FakeRecorder.latest.onerror?.();
    expect(failed).toHaveBeenCalledWith(expect.stringContaining("interrupted"));
    expect(finished).toHaveBeenCalledOnce();
    expect(stop).toHaveBeenCalledOnce();
    await startMicrophoneCapture("owner", finished, failed);
    vi.advanceTimersByTime(7_199_000);
    expect(finished).toHaveBeenCalledTimes(2);
    expect(failed).toHaveBeenCalledWith(expect.stringContaining("limit"));
    expect(stop).toHaveBeenCalledTimes(2);
  });
  it("shows recording and secure-origin guidance in English and Danish", () => {
    vi.stubGlobal("navigator", {});
    const props = { owner: "owner", timezone: "UTC", visible: true, onUpload: vi.fn(), onUploaded: vi.fn() };
    expect(renderToStaticMarkup(<BrowserRecorder {...props} />)).toContain("Start recording");
    expect(renderToStaticMarkup(<BrowserRecorder {...props} />)).toContain("Upload recordings");
    expect(renderToStaticMarkup(<BrowserRecorder {...props} />)).toContain("HTTPS or localhost");
    setLanguagePreference("da");
    const html = renderToStaticMarkup(<BrowserRecorder {...props} />);
    expect(html).toContain("Start optagelse");
    expect(html).toContain("Upload optagelser");
    expect(html).toContain("HTTPS eller localhost");
  });
});
