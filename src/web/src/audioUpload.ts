import { t } from "./i18n";
import { localDateTimeInput } from "./format";
import { request, uploadRecording } from "./api";
import type { ProfileScope } from "./profiles";
import { prepareRecording, type ConvertedRecording } from "./audioConversion";
export { encodeWav } from "./audioConversion";

export function selectRecording(files: ArrayLike<File>): File {
  if (files.length !== 1)
    throw new Error(t("Choose one recording at a time."));
  const file = files[0];
  if (!file.type.startsWith("audio/") && !/\.(wav|wave|m4a|mp3|mp4|ogg|oga|opus|flac|aac|aiff?|caf|webm)$/i.test(file.name))
    throw new Error(t("Choose an audio file, such as WAV, M4A, or MP3."));
  if (!file.size) throw new Error(t("The recording is empty. Choose another file."));
  if ([...file.name].length > 255)
    throw new Error(t("Rename this file to a name of 255 characters or fewer before uploading."));
  return file;
}

export function selectRecordings(files: ArrayLike<File>): File[] {
  if (!files.length) throw new Error(t("Choose one or more audio files."));
  return Array.from(files, (file) => {
    try { return selectRecording([file]); }
    catch (error) {
      throw new Error(`${file.name}: ${error instanceof Error ? error.message : t("Invalid recording.")}`);
    }
  });
}

export type RecordingSelection = { file: File; recordedAt: string; timeReference: RecordingTimeReference; vendorId?: string };
export type UploadProgress = {
  sent: number; total: number; completed: number; parts: number;
  attemptStarted: number; attemptBytes: number; updated: number; confirming: boolean;
};
type UploadPlan = {
  startedAt: string; endedAt: string; sourceId: string; recordingId: string; conversationSourceId: string;
  vendorId: string; sampleCount: number; ids: string[]; uploaded: boolean[];
};

export type PrepareRecording = {
  vendor_id?: string; file_digest: string; digests: string[]; sample_count: number; source_id: string;
  started_at: string; ended_at: string; conversation_source_id: string | null;
};
export type PreparedRecording = { status: "duplicate"; source_id: string } | {
  status: "upload"; vendor_id?: string; source_id: string; recording_id: string; started_at: string; ended_at: string;
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
    private decode: (file: File, onStage: (stage: string) => void, signal: AbortSignal) => Promise<ConvertedRecording> = prepareRecording,
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
        this.skipped.set(file, t("Same file as {value0}", { value0: owner.name }));
        continue;
      }
      owners.set(digest, file);
      const result = await this.backend.lookup(digest, signal, scope);
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      if (result.duplicate) this.skipped.set(file, t("Already uploaded as {value0}", { value0: result.source_id || file.name }));
    }
  }

  async upload(
    recording: RecordingSelection, target: string, signal: AbortSignal, scope: ProfileScope,
    onStage: (stage: string) => void, onProgress: (progress: UploadProgress) => void,
  ): Promise<void> {
    signal = AbortSignal.any([signal, scope.signal]);
    const { file } = recording;
    if (this.completed.has(file) || this.skipped.has(file)) return;
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    onStage(t("Checking for duplicate recordings…"));
    const fileDigest = await recordingFileDigest(file, signal);
    const match = await this.backend.lookup(fileDigest, signal, scope);
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    if (match.duplicate) {
      this.skipped.set(file, t("Already uploaded as {value0}", { value0: match.source_id || file.name }));
      return;
    }
    const audio = await this.decode(file, onStage, signal);
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    const previous = this.plans.get(file);
    const bounds = recordingTimes(recording.recordedAt, audio.sampleCount, recording.timeReference);
    const digests = audio.digests;
    const prepared = await this.backend.prepare({
      vendor_id: previous?.vendorId ?? recording.vendorId ?? "manual",
      file_digest: fileDigest, digests, sample_count: audio.sampleCount, source_id: file.name,
      started_at: previous?.startedAt ?? bounds.startedAt.toISOString(),
      ended_at: previous?.endedAt ?? bounds.endedAt.toISOString(), conversation_source_id: target || null,
    }, signal, scope);
    signal.throwIfAborted();
    scope.signal.throwIfAborted();
    if (prepared.status === "duplicate") {
      this.skipped.set(file, t("Already uploaded as {value0}", { value0: prepared.source_id }));
      this.plans.delete(file);
      return;
    }
    if (prepared.parts.length !== digests.length || prepared.parts.some((part, index) => part.digest !== digests[index]))
      throw new Error(t("The server returned a mismatched recording upload plan."));
    const current: UploadPlan = {
      vendorId: prepared.vendor_id ?? "manual",
      startedAt: prepared.started_at, endedAt: prepared.ended_at, sourceId: prepared.source_id,
      recordingId: prepared.recording_id, conversationSourceId: prepared.conversation_source_id || "",
      sampleCount: audio.sampleCount, ids: prepared.parts.map((part) => part.id), uploaded: prepared.parts.map((part) => part.uploaded),
    };
    this.plans.set(file, current);
    const start = Date.parse(current.startedAt);
    const totalBytes = current.sampleCount * 2 + current.ids.length * 44;
    const chunkLength = 16000 * 120;
    const partBytes = (index: number) => Math.min(chunkLength, audio.sampleCount - index * chunkLength) * 2 + 44;
    const initialBytes = current.uploaded.reduce((sum, done, index) => sum + (done ? partBytes(index) : 0), 0);
    const attemptStarted = performance.now();
    let confirmedBytes = initialBytes;
    await audio.readParts(async (body, i) => {
      if (current.uploaded[i]) return;
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      const offset = i * chunkLength;
      const sampleCount = (body.size - 44) / 2;
      const partStartedAt = new Date(start + offset / 16).toISOString();
      const reportProgress = (bytes: number) => {
        const sent = confirmedBytes + bytes;
        onProgress({ sent, total: totalBytes, completed: current.uploaded.filter(Boolean).length, parts: current.ids.length,
          attemptStarted, attemptBytes: sent - initialBytes, updated: performance.now(), confirming: bytes === body.size });
      };
      reportProgress(0);
      onStage(t("Uploading part {value0} of {value1}… Keep this window open.", { value0: i + 1, value1: current.ids.length }));
      const query = new URLSearchParams({
        device_id: "manual-web", started_at: partStartedAt, source_kind: "manual_upload", source_id: current.sourceId,
        recording_id: current.recordingId, part_index: String(i), part_count: String(current.ids.length),
        recording_started_at: current.startedAt, recording_ended_at: current.endedAt,
      });
      if (current.vendorId !== "manual") query.set("vendor_id", current.vendorId);
      if (current.conversationSourceId) query.set("conversation_source_id", current.conversationSourceId);
      const receipt = await this.send(`/v1/audio/${current.ids[i]}?${query}`, body, reportProgress, signal, scope);
      signal.throwIfAborted();
      scope.signal.throwIfAborted();
      validateUploadReceipt(receipt, {
        id: current.ids[i], device_id: "manual-web", started_at: partStartedAt,
        ended_at: new Date(start + (offset + sampleCount) / 16).toISOString(),
      });
      current.uploaded[i] = true;
      reportProgress(body.size);
      confirmedBytes += body.size;
    }, signal);
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
    throw new Error(t("Choose a valid recording date and time."));
  if (!Number.isFinite(sampleCount) || sampleCount <= 0)
    throw new Error(t("The recording contains no audio."));
  const duration = sampleCount / 16;
  const end = reference === "end" ? selected : selected + duration;
  if (end > now + 30_000)
    throw new Error(reference === "end"
      ? t("Choose when the recording stopped, at or before now.")
      : t("This recording would end in the future. Set when it began, or choose Recording ended to enter when it stopped."));
  return { startedAt: new Date(end - duration), endedAt: new Date(end) };
}

export function progressTime(seconds: number): string {
  if (seconds < 60) return t("{value0} sec", { value0: Math.max(1, Math.ceil(seconds)) });
  if (seconds < 3600) return t("{value0} min", { value0: Math.ceil(seconds / 60) });
  return t("{value0} hr {value1} min", { value0: Math.floor(seconds / 3600), value1: Math.ceil((seconds % 3600) / 60) });
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
      t("The server returned a mismatched upload receipt. Retry to confirm this part before continuing."),
    );
  }
}
