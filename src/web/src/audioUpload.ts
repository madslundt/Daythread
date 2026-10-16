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

export async function decodeRecording(file: File): Promise<Float32Array> {
  if (file.size > 100_000_000)
    throw new Error("Choose a recording smaller than 100 MB.");
  const decoder = new AudioContext();
  try {
    const decoded = await decoder.decodeAudioData(await file.arrayBuffer());
    if (decoded.duration > 7200)
      throw new Error(
        "Split recordings longer than two hours before uploading.",
      );
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
