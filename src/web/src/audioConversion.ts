import { t } from "./i18n";

const sampleRate = 16000;
const samplesPerPart = sampleRate * 120;
const maximumSamples = sampleRate * 12 * 60 * 60;
type PartConsumer = (part: Blob, index: number) => Promise<void>;
export type RecordingReader = (file: File, consume: PartConsumer, signal: AbortSignal) => Promise<number>;
export type ConvertedRecording = {
  sampleCount: number;
  digests: string[];
  readParts: (consume: PartConsumer, signal: AbortSignal) => Promise<void>;
};

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

const digest = async (part: Blob): Promise<string> => {
  const { sha256 } = await import("@noble/hashes/sha2.js");
  return Array.from(sha256(new Uint8Array(await part.arrayBuffer())), (value) => value.toString(16).padStart(2, "0")).join("");
};

// Two passes keep both source audio and converted parts off the JS heap between
// reads. The first establishes the complete duplicate/retry plan; the second
// uploads with backpressure. No temporary audio survives cancellation or reload.
export async function prepareRecording(file: File, onStage: (stage: string) => void, signal: AbortSignal,
  read: RecordingReader = readRecording): Promise<ConvertedRecording> {
  const digests: string[] = [];
  onStage(t("Converting audio for upload…"));
  const sampleCount = await read(file, async (part) => {
    signal.throwIfAborted();
    digests.push(await digest(part));
  }, signal);
  return { sampleCount, digests, readParts: async (consume, signal) => {
    let count = 0;
    const actualSamples = await read(file, async (part, index) => {
      signal.throwIfAborted();
      if (index !== count || await digest(part) !== digests[index])
        throw new Error(t("The converted audio changed. Retry this recording."));
      count++;
      await consume(part, index);
    }, signal);
    if (count !== digests.length || actualSamples !== sampleCount)
      throw new Error(t("The converted audio changed. Retry this recording."));
  } };
}

function validateDuration(duration: number): number {
  if (!Number.isFinite(duration) || duration <= 0) throw new Error(t("The recording contains no audio."));
  const count = Math.ceil(duration * sampleRate - 1e-6);
  if (count > maximumSamples) throw new Error(t("Split recordings longer than twelve hours before uploading."));
  return count;
}

// Accept arbitrary-sized decoder blocks, but hold at most one two-minute mono
// part. Await the consumer before reusing its buffer (hashing/network backpressure).
class AudioParts {
  private pending = new Float32Array(samplesPerPart);
  private length = 0;
  private total = 0;
  private decoded = 0;
  private index = 0;
  private silence = new Float32Array(4096);
  constructor(private consume: PartConsumer, private signal: AbortSignal, private expected: number) {}
  async appendAt(samples: Float32Array, timestamp: number): Promise<void> {
    const position = Math.round(timestamp * sampleRate);
    if (!Number.isFinite(position) || position < 0 || position > this.expected)
      throw new Error(t("The decoded audio does not match the recording duration."));
    // Preserve timestamp gaps as silence in place, without allocating the gap.
    while (this.total < position) await this.append(this.silence.subarray(0, Math.min(this.silence.length, position - this.total)));
    await this.append(samples.subarray(Math.min(samples.length, this.total - position)));
  }
  async append(samples: Float32Array): Promise<void> {
    this.signal.throwIfAborted();
    this.decoded += samples.length;
    if (this.decoded > maximumSamples + 1024)
      throw new Error(t("Split recordings longer than twelve hours before uploading."));
    if (this.decoded > this.expected + 1024)
      throw new Error(t("The decoded audio does not match the recording duration."));
    samples = samples.subarray(0, Math.max(0, this.expected - this.total));
    for (let offset = 0; offset < samples.length;) {
      const length = Math.min(samples.length - offset, samplesPerPart - this.length);
      const slice = samples.subarray(offset, offset + length);
      if (!slice.every(Number.isFinite)) throw new Error(t("Your browser could not decode this recording. Try a WAV, M4A, or MP3 file."));
      this.pending.set(slice, this.length);
      this.length += length;
      this.total += length;
      offset += length;
      if (this.length === samplesPerPart) await this.flush();
    }
  }
  private async flush(): Promise<void> {
    this.signal.throwIfAborted();
    await this.consume(new Blob([encodeWav(this.pending.subarray(0, this.length))], { type: "audio/wav" }), this.index++);
    this.length = 0;
    this.signal.throwIfAborted();
  }
  async finish(expected: number): Promise<number> {
    // A small codec tail may be rounded away. Preserve capture bounds, but reject
    // damaged/truncated input rather than silently uploading a shortened file.
    if (!this.total || Math.abs(this.total - expected) > 1024)
      throw new Error(t("The decoded audio does not match the recording duration."));
    if (this.total < expected) await this.append(new Float32Array(expected - this.total));
    if (this.length) await this.flush();
    return this.total;
  }
}

const unsupportedStreaming = () => new Error(t("This browser cannot stream this recording. Try HTTPS, another browser, or import it on iPhone."));

export async function readRecording(file: File, consume: PartConsumer, signal: AbortSignal): Promise<number> {
  signal.throwIfAborted();
  const { Input, BlobSource, ALL_FORMATS, AudioSampleSink, AudioSampleSource, Output, WavOutputFormat, NullTarget } = await import("mediabunny");
  const input = new Input({ source: new BlobSource(file, { maxCacheSize: 4 * 1024 * 1024 }), formats: ALL_FORMATS });
  try {
    const track = await input.getPrimaryAudioTrack();
    if (!track) throw new Error(t("The recording contains no audio."));
    // Negative packets can be codec preroll hidden by an MP4 edit list. They
    // warm up the decoder, but must not lengthen the presented recording.
    const start = Math.max(0, await track.getFirstTimestamp());
    // Container duration honors presentation edits and accounts for final PCM
    // packets whose packet-level duration may be unknown. Decode to EOF below
    // and verify it, so incorrect metadata cannot silently truncate the import.
    const metadataEnd = await track.getDurationFromMetadata();
    const end = metadataEnd != null && Number.isFinite(metadataEnd) && metadataEnd > start
      ? metadataEnd : await track.computeDuration();
    const duration = end - start;
    signal.throwIfAborted();
    const expected = validateDuration(duration);
    if (!await track.canDecode()) {
      // WebCodecs isn't exposed on LAN HTTP or in some older browsers. Keep Web
      // Audio compatibility for small recordings whose decoded allocation is
      // known to fit; never send a large file through a whole-file fallback.
      return await readWithWebAudio(file, consume, signal, duration, await track.getNumberOfChannels());
    }
    const parts = new AudioParts(consume, signal, expected);
    const output = new Output({ format: new WavOutputFormat(), target: new NullTarget() });
    const source = new AudioSampleSource({
      codec: "pcm-s16",
      transform: { sampleRate, numberOfChannels: 1, sampleFormat: "f32", process: async (sample) => {
        signal.throwIfAborted();
        const floats = new Float32Array(sample.numberOfFrames);
        sample.copyTo(floats, { format: "f32", planeIndex: 0 });
        await parts.appendAt(floats, sample.timestamp);
        // Consume resampled audio directly. Avoid redundant encoding and its
        // automatic gap padding, which can allocate a whole long silence.
        return null;
      } },
    });
    // Supplying the format allows this throwaway output to finalize with no
    // encoded packets; finalization drains the continuous resampler's tail.
    output.addAudioTrack(source, { decoderConfig: { codec: "pcm-s16", sampleRate, numberOfChannels: 1 } });
    const cancel = () => {
      input.dispose();
      void output.cancel().catch(() => {});
    };
    signal.addEventListener("abort", cancel, { once: true });
    try {
      signal.throwIfAborted();
      await output.start();
      for await (const sample of new AudioSampleSink(track).samples(start)) {
        let trimmed = sample;
        try {
          signal.throwIfAborted();
          const skip = Math.max(0, Math.round((start - sample.timestamp) * sample.sampleRate));
          if (skip >= sample.numberOfFrames) continue;
          if (skip) trimmed = sample.trim(skip, sample.numberOfFrames);
          trimmed.setTimestamp(Math.max(0, trimmed.timestamp - start));
          await source.add(trimmed);
        } finally {
          trimmed.close();
          sample.close();
        }
      }
      source.close();
      await output.finalize();
      signal.throwIfAborted();
      return await parts.finish(expected);
    } catch (error) {
      signal.throwIfAborted();
      throw error;
    } finally {
      signal.removeEventListener("abort", cancel);
      if (output.state !== "finalized" && output.state !== "canceled") await output.cancel();
    }
  } finally { input.dispose(); }
}

async function readWithWebAudio(file: File, consume: PartConsumer, signal: AbortSignal, duration: number, channels: number): Promise<number> {
  const decoder = new AudioContext({ sampleRate });
  try {
    // decodeAudioData resamples to the context's rate, not the file's. Include
    // both that decoded buffer and the mono rendering in the allocation budget.
    const decodedBytes = duration * (decoder.sampleRate * channels + sampleRate) * 4;
    if (file.size > 32 * 1024 * 1024 || decodedBytes > 64 * 1024 * 1024) throw unsupportedStreaming();
    const bytes = await file.arrayBuffer();
    signal.throwIfAborted();
    const decoded = await decoder.decodeAudioData(bytes);
    signal.throwIfAborted();
    const expected = validateDuration(decoded.duration);
    if (decoded.length * decoded.numberOfChannels * 4 + expected * 4 > 64 * 1024 * 1024) throw unsupportedStreaming();
    const offline = new OfflineAudioContext(1, expected, sampleRate);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const samples = (await offline.startRendering()).getChannelData(0);
    signal.throwIfAborted();
    const parts = new AudioParts(consume, signal, expected);
    await parts.append(samples);
    return await parts.finish(expected);
  } finally { await decoder.close(); }
}
