import { describe, expect, it, vi } from "vitest";
import { encodeWav, prepareRecording, readRecording } from "./audioConversion";
import { RecordingUploadQueue, selectRecording, type PrepareRecording } from "./audioUpload";

function wave(seconds: number, rate = 44100): File {
  // Reuse one second of stereo float data to make a real >100 MB file without
  // a whole-file source allocation. Each channel contributes to the mono mix.
  const frames = new Float32Array(rate * 2);
  for (let i = 0; i < rate; i++) {
    frames[i * 2] = Math.sin(i * 2 * Math.PI * 440 / rate) * 0.4;
    frames[i * 2 + 1] = frames[i * 2] * 0.5;
  }
  const header = encodeWav(new Float32Array()).slice(0, 44);
  const view = new DataView(header);
  const bytes = frames.byteLength * seconds;
  view.setUint32(4, bytes + 36, true);
  view.setUint16(20, 3, true); // IEEE float
  view.setUint16(22, 2, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 8, true);
  view.setUint16(32, 8, true);
  view.setUint16(34, 32, true);
  view.setUint32(40, bytes, true);
  return new File([header, ...Array.from({ length: seconds }, () => new Blob([frames]))], "long.wav", { type: "audio/wav" });
}

describe("streamed recording conversion", () => {
  it("uploads a real >100 MB file with bounded reads, exact parts and stable hashes", async () => {
    const file = wave(301);
    expect(file.size).toBeGreaterThan(100_000_000);
    expect(selectRecording([file])).toBe(file);
    const wholeFileRead = vi.spyOn(file, "arrayBuffer").mockRejectedValue(new Error("Whole file read"));
    const originalSlice = file.slice.bind(file);
    let largestRead = 0;
    vi.spyOn(file, "slice").mockImplementation((start, end, type) => {
      const result = originalSlice(start, end, type);
      const arrayBuffer = result.arrayBuffer.bind(result);
      vi.spyOn(result, "arrayBuffer").mockImplementation(() => {
        largestRead = Math.max(largestRead, result.size);
        return arrayBuffer();
      });
      // Slicing a Blob is a lazy view, including a whole-file view. Measure
      // actual allocations and stream chunks rather than those view lengths.
      const stream = result.stream.bind(result);
      vi.spyOn(result, "stream").mockImplementation(() => stream().pipeThrough(new TransformStream({
        transform(chunk, controller) {
          largestRead = Math.max(largestRead, chunk.byteLength);
          controller.enqueue(chunk);
        },
      })));
      return result;
    });
    const signal = new AbortController().signal;
    const scope = { id: "test", signal };
    let plan: PrepareRecording | undefined;
    const sizes: number[] = [];
    const starts: string[] = [];
    const queue = new RecordingUploadQueue(undefined, async (path, body) => {
      sizes.push(body.size);
      const url = new URL(path, "http://localhost");
      const started_at = url.searchParams.get("started_at")!;
      starts.push(started_at);
      return { id: url.pathname.split("/").at(-1), device_id: "manual-web", started_at,
        ended_at: new Date(Date.parse(started_at) + (body.size - 44) / 32).toISOString() };
    }, {
      lookup: async () => ({ duplicate: false }),
      prepare: async (body) => {
        plan = body;
        return { ...body, status: "upload", recording_id: "recording",
          parts: body.digests.map((digest, i) => ({ id: `part-${i}`, digest, uploaded: false })) };
      },
    });
    await queue.upload({ file, recordedAt: "2026-10-15T12:00:00Z", timeReference: "start" }, "", signal, scope, vi.fn(), vi.fn());
    expect(plan?.sample_count).toBe(16000 * 301);
    expect(plan?.digests).toHaveLength(3);
    expect(sizes).toEqual([120, 120, 61].map((seconds) => seconds * 32000 + 44));
    expect(starts).toEqual(["2026-10-15T12:00:00.000Z", "2026-10-15T12:02:00.000Z", "2026-10-15T12:04:00.000Z"]);
    expect(wholeFileRead).not.toHaveBeenCalled();
    expect(largestRead).toBeLessThanOrEqual(4 * 1024 * 1024);
    expect(queue.completed.has(file)).toBe(true);
  }, 20_000);

  it("retains mono audio values through resampling and produces identical parts on both passes", async () => {
    const signal = new AbortController().signal;
    const converted = await prepareRecording(wave(1), vi.fn(), signal);
    expect(converted.sampleCount).toBe(16000);
    await converted.readParts(async (part) => {
      const view = new DataView(await part.arrayBuffer());
      expect(view.getUint32(24, true)).toBe(16000);
      expect(view.getUint16(22, true)).toBe(1);
      expect(view.getInt16(44 + 20 * 2, true)).toBeCloseTo(Math.sin(20 * 2 * Math.PI * 440 / 16000) * 0.3 * 32767, -2);
    }, signal);
  });

  it("stops conversion after a consumer cancels, without emitting another part", async () => {
    const controller = new AbortController();
    const consume = vi.fn(async () => { controller.abort(); });
    await expect(readRecording(wave(121, 16000), consume, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(consume).toHaveBeenCalledOnce();
  });

  it("preserves consumer failures instead of reporting them as decoding errors", async () => {
    const failure = new Error("Connection lost");
    await expect(readRecording(wave(1), async () => { throw failure; }, new AbortController().signal)).rejects.toBe(failure);
  });

  function slowWave(seconds: number): File {
    // One frame per second keeps duration-boundary fixtures small while driving
    // the real parser, duration check and streaming decoder.
    const bytes = encodeWav(new Float32Array(seconds));
    const view = new DataView(bytes);
    view.setUint32(24, 1, true);
    view.setUint32(28, 2, true);
    return new File([bytes], "long.wav");
  }

  it.each([3, 12])("starts streaming a %i-hour recording instead of rejecting its duration", async (hours) => {
    const controller = new AbortController();
    const consume = vi.fn(async (_part: Blob) => { controller.abort(); });
    await expect(readRecording(slowWave(hours * 3600), consume, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(consume).toHaveBeenCalledOnce();
    expect(consume.mock.calls[0][0].size).toBe(120 * 32000 + 44);
  });

  it("rejects over-twelve-hour audio before emitting any part", async () => {
    const consume = vi.fn();
    await expect(readRecording(slowWave(12 * 3600 + 1), consume, new AbortController().signal)).rejects.toThrow("twelve hours");
    expect(consume).not.toHaveBeenCalled();
  });

  it("refuses changed converted bytes before transferring that part", async () => {
    let pass = 0;
    const signal = new AbortController().signal;
    const converted = await prepareRecording(new File(["x"], "memo.wav"), vi.fn(), signal, async (_file, consume) => {
      await consume(new Blob([encodeWav(new Float32Array([pass++]))]), 0);
      return 1;
    });
    const consume = vi.fn();
    await expect(converted.readParts(consume, signal)).rejects.toThrow("converted audio changed");
    expect(consume).not.toHaveBeenCalled();
  });

  it("keeps a long internal timestamp gap as bounded silence in its original place", async () => {
    const { Output, MkvOutputFormat, BufferTarget, EncodedAudioPacketSource, EncodedPacket } = await import("mediabunny");
    const target = new BufferTarget();
    const output = new Output({ format: new MkvOutputFormat(), target });
    const source = new EncodedAudioPacketSource("pcm-s16");
    output.addAudioTrack(source);
    await output.start();
    const pcm = new Int16Array(16000).fill(8192);
    for (const timestamp of [-1, 0, 130]) {
      await source.add(new EncodedPacket(new Uint8Array(pcm.buffer), "key", timestamp, 1), {
        decoderConfig: { codec: "pcm-s16", sampleRate: 16000, numberOfChannels: 1 },
      });
    }
    source.close();
    await output.finalize();
    const file = new File([target.buffer!], "gap.mkv");
    const converted = await prepareRecording(file, vi.fn(), new AbortController().signal);
    expect(converted.sampleCount).toBe(131 * 16000);
    const parts: DataView[] = [];
    await converted.readParts(async (part) => { parts.push(new DataView(await part.arrayBuffer())); }, new AbortController().signal);
    expect(parts).toHaveLength(2);
    expect(parts[0].getInt16(44, true)).toBeCloseTo(8192, -1);
    expect(parts[0].getInt16(44 + 2 * 16000 * 2, true)).toBe(0);
    expect(parts[1].getInt16(44, true)).toBe(0);
    expect(parts[1].getInt16(44 + 10 * 16000 * 2, true)).toBeCloseTo(8192, -1);
  });

  it("budgets HTTP fallback against the actual context rate before loading low-rate compressed audio", async () => {
    const { InputAudioTrack } = await import("mediabunny");
    const canDecode = vi.spyOn(InputAudioTrack.prototype, "canDecode").mockResolvedValue(false);
    const close = vi.fn();
    class Decoder { sampleRate = 48000; close = close; }
    vi.stubGlobal("AudioContext", Decoder);
    const file = wave(1800, 8);
    const read = vi.spyOn(file, "arrayBuffer");
    try {
      await expect(readRecording(file, vi.fn(), new AbortController().signal)).rejects.toThrow("Try HTTPS");
      expect(read).not.toHaveBeenCalled();
      expect(close).toHaveBeenCalledOnce();
    } finally { canDecode.mockRestore(); vi.unstubAllGlobals(); }
  });
});
