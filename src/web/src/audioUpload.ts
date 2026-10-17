import { localDateTimeInput } from "./format";
import { request, uploadRecording } from "./api";
import type { ProfileScope } from "./profiles";

export function encodeWav(samples: Float32Array): ArrayBuffer {
  const result = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(result);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++)
      view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, result.byteLength - 8, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(
      44 + i * 2,
      Math.round(value < 0 ? value * 32768 : value * 32767),
      true,
    );
  }
  return result;
}

export function selectRecording(files: ArrayLike<File>): File {
  if (files.length !== 1)
    throw new Error("Choose one recording at a time.");
  const file = files[0];
  if (!file.type.startsWith("audio/") && !/\.(wav|wave|m4a|mp3|mp4|ogg|oga|opus|flac|aac|aiff?|caf|webm)$/i.test(file.name))
    throw new Error("Choose an audio file, such as WAV, M4A, or MP3.");
  if (!file.size) throw new Error("The recording is empty. Choose another file.");
  if (file.size > 100_000_000)
    throw new Error("Choose a recording no larger than 100 MB.");
  if ([...file.name].length > 255)
    throw new Error("Rename this file to a name of 255 characters or fewer before uploading.");
  return file;
}

export function selectRecordings(files: ArrayLike<File>): File[] {
  if (!files.length) throw new Error("Choose one or more audio files.");
  return Array.from(files, (file) => {
    try { return selectRecording([file]); }
    catch (error) {
      throw new Error(`${file.name}: ${error instanceof Error ? error.message : "Invalid recording."}`);
    }
  });
}

export type RecordingSelection = { file: File; recordedAt: string; timeReference: RecordingTimeReference };
export type UploadProgress = {
  sent: number; total: number; completed: number; parts: number;
  attemptStarted: number; attemptBytes: number; updated: number; confirming: boolean;
};
type UploadPlan = {
  startedAt: string; endedAt: string; sourceId: string; recordingId: string; conversationSourceId: string;
  sampleCount: number; ids: string[]; uploaded: boolean[];
};

export type PrepareRecording = {
  file_digest: string; digests: string[]; sample_count: number; source_id: string;
  started_at: string; ended_at: string; conversation_source_id: string | null;
};
export type PreparedRecording = { status: "duplicate"; source_id: string } | {
  status: "upload"; source_id: string; recording_id: string; started_at: string; ended_at: string;
  conversation_source_id: string | null; parts: { id: string; digest: string; uploaded: boolean }[];
};
export type RecordingUploadAPI = {
  lookup: (digest: string, signal: AbortSignal, scope: ProfileScope) => Promise<{ duplicate: boolean; source_id?: string }>;
  prepare: (body: PrepareRecording, signal: AbortSignal, scope: ProfileScope) => Promise<PreparedRecording>;
};
const recordingUploadAPI: RecordingUploadAPI = {
  lookup: (digest, signal, scope) => request("/v1/recording-uploads/check", {
    method: "POST", body: JSON.stringify({ digest }), signal,
  }, scope),
  prepare: (body, signal, scope) => request("/v1/recording-uploads/prepare", {
    method: "POST", body: JSON.stringify(body), signal,
  }, scope),
};

// Pure JS hashing works on LAN HTTP too. Read bounded slices and never retain
// every original file or every converted WAV in memory during a bulk upload.
const fileDigests = new WeakMap<File, string>();
function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}
export async function recordingFileDigest(file: File, signal: AbortSignal): Promise<string> {
  signal.throwIfAborted();
  const cached = fileDigests.get(file);
  if (cached) return cached;
  const { sha256 } = await import("@noble/hashes/sha2.js");
  const hash = sha256.create();
  for (let offset = 0; offset < file.size; offset += 1_048_576) {
    const bytes = await file.slice(offset, offset + 1_048_576).arrayBuffer();
    signal.throwIfAborted();
    hash.update(new Uint8Array(bytes));
  }
  const digest = bytesToHex(hash.digest());
  fileDigests.set(file, digest);
  return digest;
}

// Server plans share stable part IDs across clients, dialogs and retries. Only
// confirmed complete recordings can be skipped; partial recordings are resumed.
export class RecordingUploadQueue {
  private plans = new Map<File, UploadPlan>();
  readonly completed = new Set<File>();
  readonly skipped = new Map<File, string>();
  get started(): boolean { return this.plans.size > 0 || this.completed.size > 0; }
  hasPlan(file: File): boolean { return this.plans.has(file) || this.completed.has(file) || this.skipped.has(file); }

  constructor(
    private decode = decodeRecording,
    private send: (path: string, body: Blob, progress: (sent: number) => void, signal: AbortSignal, scope: ProfileScope) => Promise<unknown> = uploadRecording,
    private backend: RecordingUploadAPI = recordingUploadAPI,
  ) {}

  async inspect(files: File[], signal: AbortSignal, scope: ProfileScope): Promise<void> {
    this.skipped.clear();
    const owners = new Map<string, File>();
    for (const file of files) {
      const digest = await recordingFileDigest(file, signal);
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      const owner = owners.get(digest);
      if (owner && owner !== file) {
        this.skipped.set(file, `Same file as ${owner.name}`);
        continue;
      }
      owners.set(digest, file);
      const result = await this.backend.lookup(digest, signal, scope);
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      if (result.duplicate) this.skipped.set(file, `Already uploaded as ${result.source_id || file.name}`);
    }
  }

  async upload(
    recording: RecordingSelection, target: string, signal: AbortSignal, scope: ProfileScope,
    onStage: (stage: string) => void, onProgress: (progress: UploadProgress) => void,
  ): Promise<void> {
    const { file } = recording;
    if (this.completed.has(file) || this.skipped.has(file)) return;
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    onStage("Checking for duplicate recordings…");
    const fileDigest = await recordingFileDigest(file, signal);
    const match = await this.backend.lookup(fileDigest, signal, scope);
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    if (match.duplicate) {
      this.skipped.set(file, `Already uploaded as ${match.source_id || file.name}`);
      return;
    }
    const chunkLength = 16000 * 120;
    const samples = await this.decode(file, onStage);
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    const previous = this.plans.get(file);
    const bounds = recordingTimes(recording.recordedAt, samples.length, recording.timeReference);
    onStage("Checking the converted audio for duplicates…");
    const { sha256 } = await import("@noble/hashes/sha2.js");
    const digests: string[] = [];
    for (let offset = 0; offset < samples.length; offset += chunkLength) {
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      digests.push(bytesToHex(sha256(new Uint8Array(encodeWav(samples.subarray(offset, offset + chunkLength))))));
      // Yield between parts so cancellation and preparation feedback stay responsive.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    const prepared = await this.backend.prepare({
      file_digest: fileDigest, digests, sample_count: samples.length, source_id: file.name,
      started_at: previous?.startedAt ?? bounds.startedAt.toISOString(),
      ended_at: previous?.endedAt ?? bounds.endedAt.toISOString(), conversation_source_id: target || null,
    }, signal, scope);
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    if (prepared.status === "duplicate") {
      this.skipped.set(file, `Already uploaded as ${prepared.source_id}`);
      this.plans.delete(file);
      return;
    }
    if (prepared.parts.length !== digests.length || prepared.parts.some((part, index) => part.digest !== digests[index]))
      throw new Error("The server returned a mismatched recording upload plan.");
    const current: UploadPlan = {
      startedAt: prepared.started_at, endedAt: prepared.ended_at, sourceId: prepared.source_id,
      recordingId: prepared.recording_id, conversationSourceId: prepared.conversation_source_id || "",
      sampleCount: samples.length, ids: prepared.parts.map((part) => part.id), uploaded: prepared.parts.map((part) => part.uploaded),
    };
    this.plans.set(file, current);
    const start = Date.parse(current.startedAt);
    const totalBytes = current.sampleCount * 2 + current.ids.length * 44;
    const partBytes = (index: number) => Math.min(chunkLength, samples.length - index * chunkLength) * 2 + 44;
    const initialBytes = current.uploaded.reduce((sum, done, index) => sum + (done ? partBytes(index) : 0), 0);
    const attemptStarted = performance.now();
    let confirmedBytes = initialBytes;
    for (let i = 0; i < current.ids.length; i++) {
      if (current.uploaded[i]) continue;
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      const offset = i * chunkLength;
      const partSamples = samples.subarray(offset, offset + chunkLength);
      const partStartedAt = new Date(start + offset / 16).toISOString();
      const body = new Blob([encodeWav(partSamples)], { type: "audio/wav" });
      const reportProgress = (bytes: number) => {
        const sent = confirmedBytes + bytes;
        onProgress({ sent, total: totalBytes, completed: current.uploaded.filter(Boolean).length, parts: current.ids.length,
          attemptStarted, attemptBytes: sent - initialBytes, updated: performance.now(), confirming: bytes === body.size });
      };
      reportProgress(0);
      onStage(`Uploading part ${i + 1} of ${current.ids.length}… Keep this window open.`);
      const query = new URLSearchParams({
        device_id: "manual-web", started_at: partStartedAt, source_kind: "manual_upload", source_id: current.sourceId,
        recording_id: current.recordingId, part_index: String(i), part_count: String(current.ids.length),
        recording_started_at: current.startedAt, recording_ended_at: current.endedAt,
      });
      if (current.conversationSourceId) query.set("conversation_source_id", current.conversationSourceId);
      const receipt = await this.send(`/v1/audio/${current.ids[i]}?${query}`, body, reportProgress, signal, scope);
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      validateUploadReceipt(receipt, {
        id: current.ids[i], device_id: "manual-web", started_at: partStartedAt,
        ended_at: new Date(start + (offset + partSamples.length) / 16).toISOString(),
      });
      current.uploaded[i] = true;
      reportProgress(body.size);
      confirmedBytes += body.size;
    }
    this.completed.add(file);
    this.plans.delete(file);
  }
}

export type RecordingTimeReference = "start" | "end";

export function recordingEndTime(
  file: { lastModified?: number },
  now = Date.now(),
): string {
  const timestamp = file.lastModified;
  if (timestamp === undefined || !Number.isFinite(timestamp) || timestamp <= 0 || timestamp > now)
    return "";
  const date = new Date(timestamp);
  return Number.isFinite(date.getTime()) ? localDateTimeInput(date) : "";
}

export function recordingTimes(
  timestamp: string,
  sampleCount: number,
  reference: RecordingTimeReference = "end",
  now = Date.now(),
): { startedAt: Date; endedAt: Date } {
  const selected = new Date(timestamp).getTime();
  if (!Number.isFinite(selected))
    throw new Error("Choose a valid recording date and time.");
  if (!Number.isFinite(sampleCount) || sampleCount <= 0)
    throw new Error("The recording contains no audio.");
  const duration = sampleCount / 16;
  const end = reference === "end" ? selected : selected + duration;
  if (end > now + 30_000)
    throw new Error(reference === "end"
      ? "Choose when the recording stopped, at or before now."
      : "This recording would end in the future. Set when it began, or choose Recording ended to enter when it stopped.");
  return { startedAt: new Date(end - duration), endedAt: new Date(end) };
}

export async function decodeRecording(file: File, onStage?: (stage: string) => void): Promise<Float32Array> {
  selectRecording([file]);
  const decoder = new AudioContext();
  try {
    onStage?.("Reading the recording on this device…");
    const bytes = await file.arrayBuffer();
    onStage?.("Decoding audio on this device…");
    const decoded = await decoder.decodeAudioData(bytes);
    if (decoded.duration > 7200)
      throw new Error(
        "Split recordings longer than two hours before uploading.",
      );
    onStage?.("Converting audio for upload…");
    const offline = new OfflineAudioContext(
      1,
      Math.ceil(decoded.duration * 16000),
      16000,
    );
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    return (await offline.startRendering()).getChannelData(0);
  } catch (error) {
    if (error instanceof Error && /two hours/.test(error.message)) throw error;
    throw new Error(
      "Your browser could not decode this recording. Try a WAV, M4A, or MP3 file.",
    );
  } finally {
    await decoder.close();
  }
}

export function progressTime(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.ceil(seconds))} sec`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} hr ${Math.ceil((seconds % 3600) / 60)} min`;
}

export function uploadEstimate(sent: number, total: number, elapsed: number): number | undefined {
  if (elapsed < 1 || sent <= 0 || sent >= total) return undefined;
  return (total - sent) * elapsed / sent;
}

export function validateUploadReceipt(
  receipt: unknown,
  expected: { id: string; device_id: string; started_at: string; ended_at: string },
): void {
  const actual = receipt as Record<string, unknown> | null;
  if (
    !actual ||
    actual.id !== expected.id ||
    actual.device_id !== expected.device_id ||
    typeof actual.started_at !== "string" ||
    Date.parse(actual.started_at) !== Date.parse(expected.started_at) ||
    typeof actual.ended_at !== "string" ||
    !Number.isFinite(Date.parse(actual.ended_at)) ||
    Date.parse(actual.ended_at) !== Date.parse(expected.ended_at)
  ) {
    throw new Error(
      "The server returned a mismatched upload receipt. Retry to confirm this part before continuing.",
    );
  }
}
