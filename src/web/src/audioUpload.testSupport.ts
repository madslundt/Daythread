import { encodeWav, prepareRecording } from "./audioConversion";

/** Replace only the codec in queue tests, retaining real hashes and part framing. */
export function prepareSamples(decode: (file: File) => Promise<Float32Array>) {
  return async (file: File, onStage: (stage: string) => void, signal: AbortSignal) => {
    const samples = await decode(file);
    return prepareRecording(file, onStage, signal, async (_file, consume, signal) => {
      const length = 16000 * 120;
      for (let offset = 0, index = 0; offset < samples.length; offset += length, index++) {
        signal.throwIfAborted();
        await consume(new Blob([encodeWav(samples.subarray(offset, offset + length))]), index);
      }
      return samples.length;
    });
  };
}
