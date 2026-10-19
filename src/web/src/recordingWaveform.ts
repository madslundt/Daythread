// Use the same normalized, 160-bin peak envelope as the iPhone player.
export function recordingPeaks(channels: readonly Float32Array[], binCount = 160): number[] {
  if (!Number.isInteger(binCount) || binCount < 1 || binCount > 2048) throw new RangeError();
  const frames = channels[0]?.length ?? 0;
  const peaks = new Array<number>(binCount).fill(0);
  for (const channel of channels) {
    for (let frame = 0; frame < channel.length; frame++) {
      const sample = channel[frame];
      if (!Number.isFinite(sample)) continue;
      const bin = Math.min(binCount - 1, Math.floor(frame * binCount / frames));
      peaks[bin] = Math.max(peaks[bin], Math.abs(sample));
    }
  }
  const maximum = Math.max(...peaks);
  return maximum > 0 ? peaks.map((peak) => peak / maximum) : peaks;
}

export function playbackTime(seconds: number): string {
  const value = Math.floor(Number.isFinite(seconds) ? Math.max(0, seconds) : 0);
  const minutes = String(Math.floor(value / 60) % 60).padStart(2, "0");
  const remainder = String(value % 60).padStart(2, "0");
  return value >= 3600 ? `${String(Math.floor(value / 3600)).padStart(2, "0")}:${minutes}:${remainder}`
    : `${minutes}:${remainder}`;
}
