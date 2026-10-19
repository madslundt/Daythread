import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { playbackTime, recordingPeaks } from "./recordingWaveform";

describe("recording waveform", () => {
  it("retains first and final samples and takes peaks across every channel", () => {
    expect(recordingPeaks([
      new Float32Array([0.5, 0, 0, 0, 0]),
      new Float32Array([0, 0, -1, 0, 0.25]),
    ], 3)).toEqual([0.5, 1, 0.25]);
  });
  it("normalizes actual samples without fabricating signal in silence or empty bins", () => {
    expect(recordingPeaks([new Float32Array([0, -0.5])], 4)).toEqual([0, 0, 1, 0]);
    expect(recordingPeaks([new Float32Array(4)], 3)).toEqual([0, 0, 0]);
    expect(recordingPeaks([], 3)).toEqual([0, 0, 0]);
    expect(recordingPeaks([new Float32Array([NaN, Infinity, -Infinity, 0.25])], 2)).toEqual([0, 1]);
  });
  it("bounds the envelope size", () => {
    expect(recordingPeaks([new Float32Array([1])])).toHaveLength(160);
    for (const count of [0, -1, 1.5, 2049]) expect(() => recordingPeaks([], count)).toThrow(RangeError);
  });
});

describe("recording playback presentation", () => {
  it("formats finite elapsed times including recordings over an hour", () => {
    expect(playbackTime(0)).toBe("00:00");
    expect(playbackTime(125.9)).toBe("02:05");
    expect(playbackTime(3661)).toBe("01:01:01");
    for (const value of [-10, NaN, Infinity]) expect(playbackTime(value)).toBe("00:00");
  });
  it("exposes the phone controls and keyboard seeking without native browser controls", () => {
    const html = renderToStaticMarkup(<RecordingAudioPlayer src="/recording.wav" durationHint={120} active={false} />);
    for (const label of ["Playback position", "Play original audio", "Skip backward 15 seconds", "Skip forward 15 seconds", "Slower playback", "Faster playback", "Playback speed"])
      expect(html).toContain(`aria-label="${label}"`);
    expect(html).toContain("02:00");
    expect(html).toContain('type="range"');
    expect(html).toContain('preload="none"');
    expect(html).not.toContain(" controls");
  });
});
