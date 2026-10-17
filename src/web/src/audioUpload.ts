import { localDateTimeInput } from "./format";

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
