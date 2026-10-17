import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { progressTime, recordingEndTime, recordingTimes, selectRecording, uploadEstimate, validateUploadReceipt } from "./audioUpload";
import { localDateTimeInput } from "./format";
import { SourceList } from "./SourceList";
import { AudioDropArea, UploadDialog } from "./UploadDialog";

const expected = {
  id: "c397bc72-f37c-4d2d-b699-7cf913211194", device_id: "manual-web",
  started_at: "2026-10-15T12:00:00.000Z", ended_at: "2026-10-15T12:02:00.000Z",
};

describe("upload time estimates", () => {
  it("uses observed bytes and elapsed time for the remaining transfer", () => {
    expect(uploadEstimate(250, 1000, 5)).toBe(15);
    // A resumed attempt measures only its remaining bytes, without crediting
    // already-saved bytes to the new connection's speed.
    expect(uploadEstimate(100, 500, 2)).toBe(8);
  });
  it("waits for a speed sample and does not estimate completed transfers", () => {
    expect(uploadEstimate(0, 1000, 5)).toBeUndefined();
    expect(uploadEstimate(100, 1000, 0.5)).toBeUndefined();
    expect(uploadEstimate(1000, 1000, 5)).toBeUndefined();
  });
  it("formats estimates without implying fractional-second accuracy", () => {
    expect(progressTime(0.2)).toBe("1 sec");
    expect(progressTime(91)).toBe("2 min");
    expect(progressTime(3900)).toBe("1 hr 5 min");
  });
});

describe("recordings chosen or dropped from another device", () => {
  it("prefills the dialog with a recording dropped on Today and its last-modified time", () => {
    const file = new File(["audio"], "Voice Memo.M4A", { lastModified: new Date("2026-10-15T12:34:00Z").getTime() });
    const html = renderToStaticMarkup(<UploadDialog initialFiles={[file]} onClose={() => {}} onUploaded={() => {}} />);
    expect(html).toContain("Voice Memo.M4A ·");
    expect(html.match(/<input[^>]+name="recorded_at"[^>]*>/)?.[0]).toContain(`value="${recordingEndTime(file)}"`);
    expect(html).toContain('<option value="end" selected="">Recording ended</option>');
    expect(html).toContain('<option value="auto" selected="">Match automatically after transcription</option>');
    expect(html).not.toContain('class="button accent" disabled=""');
  });
  it("shows the existing validation errors for invalid drops on Today", () => {
    const wav = new File(["audio"], "memo.wav");
    for (const files of [[wav, wav], [new File(["text"], "notes.txt")], [new File([], "empty.wav")]]) {
      const html = renderToStaticMarkup(<UploadDialog initialFiles={files} onClose={() => {}} onUploaded={() => {}} />);
      expect(html).toContain('role="alert"');
      expect(html).toContain("One audio file at a time");
      expect(html).toContain('class="button accent" disabled=""');
    }
  });
  it("requires an explicit timestamp until a file provides one", () => {
    vi.useFakeTimers();
    try {
      const now = new Date("2026-10-16T12:34:56Z");
      vi.setSystemTime(now);
      const html = renderToStaticMarkup(<UploadDialog onClose={() => {}} onUploaded={() => {}} />);
      expect(html).toContain('<option value="end" selected="">Recording ended</option>');
      expect(html).toContain('name="recorded_at"');
      expect(html.match(/<input[^>]+name="recorded_at"[^>]*>/)?.[0]).toContain('value=""');
      expect(html.match(/<input[^>]+name="recorded_at"[^>]*>/)?.[0]).toContain('required=""');
      expect(html).toContain("last-modified");
      expect(html).toContain("when the recording stopped");
      expect(html).toContain('<option value="auto" selected="">Match automatically after transcription</option>');
      expect(html).toContain("Add another source to an existing conversation");
      expect(html).toContain("Every source is kept");
    } finally { vi.useRealTimers(); }
  });
  it("accepts a dropped recording and prevents the browser from navigating to the file", () => {
    const file = new File(["audio"], "memo.m4a");
    const onChoose = vi.fn(), onDragging = vi.fn(), preventDefault = vi.fn();
    const area = AudioDropArea({ busy: false, dragging: true, onChoose, onDragging });
    area.props.onDrop({ preventDefault, dataTransfer: { files: [file] } });
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(onChoose).toHaveBeenCalledWith([file]);
    expect(onDragging).toHaveBeenCalledWith(false);
  });
  it("keeps an in-progress upload unchanged when another recording is dropped", () => {
    const onChoose = vi.fn(), preventDefault = vi.fn();
    const area = AudioDropArea({ busy: true, dragging: false, onChoose, onDragging: vi.fn() });
    const event = { preventDefault, dataTransfer: { files: [new File(["audio"], "next.wav")], dropEffect: "copy" } };
    area.props.onDragOver(event);
    area.props.onDrop(event);
    expect(event.dataTransfer.dropEffect).toBe("none");
    expect(preventDefault).toHaveBeenCalledTimes(2);
    expect(onChoose).not.toHaveBeenCalled();
  });
  it("accepts audio MIME types and exported recordings without a MIME type", () => {
    const voiceMemo = new File(["audio"], "Voice Memo.M4A");
    const typed = new File(["audio"], "recording", { type: "audio/wav" });
    expect(selectRecording([voiceMemo])).toBe(voiceMemo);
    expect(selectRecording([typed])).toBe(typed);
    expect(selectRecording({ 0: voiceMemo, length: 1 })).toBe(voiceMemo);
  });
  it("rejects multiple files, non-audio files, and empty files", () => {
    const wav = new File(["audio"], "recording.wav");
    expect(() => selectRecording([])).toThrow("one recording");
    expect(() => selectRecording([wav, wav])).toThrow("one recording");
    expect(() => selectRecording([new File(["text"], "notes.txt")])).toThrow("audio file");
    expect(() => selectRecording([new File([], "empty.wav")])).toThrow("empty");
  });
  it("checks size and filename limits before reading dropped audio", () => {
    const large = new File(["audio"], "recording.wav");
    Object.defineProperty(large, "size", { value: 100_000_001 });
    expect(() => selectRecording([large])).toThrow("100 MB");
    expect(() => selectRecording([new File(["audio"], "x".repeat(252) + ".wav")])).toThrow("255 characters");
  });
  it("offers keyboard file selection alongside the drop area without requiring native input files", () => {
    const html = renderToStaticMarkup(<UploadDialog onClose={() => {}} onUploaded={() => {}} />);
    expect(html).toContain("Drag and drop a recording");
    expect(html).toContain("Choose audio file");
    expect(html).toContain('aria-label="Audio file drop area"');
    // Dropped files live in React state, rather than the browser's read-only FileList.
    expect(html.match(/<input[^>]+type="file"[^>]*>/)?.[0]).not.toContain("required");
  });
});

describe("manual recording start and end times", () => {
  const now = Date.parse("2026-10-16T12:00:00Z");
  const minutes = (count: number) => count * 60 * 16000;

  it("prefills the recording end from file metadata in local time", () => {
    const end = new Date("2026-10-15T23:45:00Z");
    const file = new File(["audio"], "memo.m4a", { lastModified: end.getTime() });
    expect(recordingEndTime(file, now)).toBe(localDateTimeInput(end));
  });
  it.each([undefined, 0, -1, NaN, Infinity, 8.64e15 + 1, now + 60_000])(
    "leaves the timestamp empty for unavailable or invalid metadata: %s", (lastModified) => {
      expect(recordingEndTime({ lastModified }, now)).toBe("");
    },
  );
  it("requires a date and time when file metadata is unavailable", () => {
    expect(() => recordingTimes("", minutes(20), "end", now)).toThrow("valid recording date");
  });

  it("allows a recording ending now by subtracting its full duration", () => {
    const bounds = recordingTimes(new Date(now).toISOString(), minutes(45), undefined, now);
    expect(bounds.startedAt.toISOString()).toBe("2026-10-16T11:15:00.000Z");
    expect(bounds.endedAt.getTime()).toBe(now);
  });
  it("treats an explicit end time as when the recording stopped", () => {
    const bounds = recordingTimes("2026-10-16T10:00:00Z", minutes(20), "end", now);
    expect(bounds.startedAt.toISOString()).toBe("2026-10-16T09:40:00.000Z");
    expect(bounds.endedAt.toISOString()).toBe("2026-10-16T10:00:00.000Z");
  });
  it("retains explicit start times and calculates the end from the duration", () => {
    const bounds = recordingTimes("2026-10-16T11:15:00Z", minutes(45), "start", now);
    expect(bounds.startedAt.toISOString()).toBe("2026-10-16T11:15:00.000Z");
    expect(bounds.endedAt.getTime()).toBe(now);
  });
  it("handles recordings beginning on the previous calendar day", () => {
    const bounds = recordingTimes("2026-10-16T00:10:00+02:00", minutes(20), "end", now);
    expect(bounds.startedAt.toISOString()).toBe("2026-10-15T21:50:00.000Z");
    expect(bounds.endedAt.toISOString()).toBe("2026-10-15T22:10:00.000Z");
  });
  it("subtracts elapsed audio duration across a daylight-saving transition", () => {
    const end = "2026-10-25T02:15:00+01:00";
    const bounds = recordingTimes(end, minutes(45), "end", Date.parse(end));
    expect(bounds.startedAt.toISOString()).toBe("2026-10-25T00:30:00.000Z");
    expect(bounds.endedAt.toISOString()).toBe("2026-10-25T01:15:00.000Z");
  });
  it("keeps capture bounds stable when an upload is retried later", () => {
    const timestamp = "2026-10-16T12:00:00Z";
    expect(recordingTimes(timestamp, minutes(45), "end", now + 600_000))
      .toEqual(recordingTimes(timestamp, minutes(45), "end", now));
  });
  it("rejects future end times, including those calculated from an explicit start", () => {
    expect(() => recordingTimes("2026-10-16T12:01:00Z", minutes(45), "end", now)).toThrow("when the recording stopped");
    expect(() => recordingTimes("2026-10-16T12:00:00Z", minutes(45), "start", now)).toThrow("Recording ended");
    expect(() => recordingTimes("invalid", minutes(45), "end", now)).toThrow("valid recording date");
    expect(() => recordingTimes("2026-10-16T12:00:00Z", 0, "end", now)).toThrow("no audio");
  });
});

describe("manual upload acknowledgements", () => {
  it("accepts matching normalized UTC capture bounds, including submillisecond samples", () => {
    expect(() => validateUploadReceipt({
      ...expected, started_at: "2026-10-15T12:00:00+00:00",
      ended_at: "2026-10-15T12:02:00.000063+00:00", state: "queued",
    }, expected)).not.toThrow();
  });
  it.each([
    null, {}, { ...expected, id: "another-id" }, { ...expected, device_id: "omi" },
    { ...expected, started_at: "2026-10-15T12:00:01Z" },
    { ...expected, ended_at: "2026-10-15T12:02:01Z" },
    { ...expected, ended_at: "2026-10-15T12:02:00.001Z" },
    { ...expected, ended_at: "invalid" }, { ...expected, started_at: null },
  ])("rejects a missing or mismatched receipt without acknowledging the part: %j", (receipt) => {
    expect(() => validateUploadReceipt(receipt, expected)).toThrow("mismatched upload receipt");
  });
});

describe("manual upload origin", () => {
  it("shows the original filename as text and identifies converted archived playback", () => {
    const html = renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[{
      ...expected, source_kind: "manual_upload", source_id: "Voice Memos <meeting>.m4a",
      state: "queued", text: null,
    }]} />);
    expect(html).toContain("Manually uploaded recording");
    expect(html).toContain("Voice Memos &lt;meeting&gt;.m4a");
    expect(html).toContain("Capture time was supplied when importing");
    expect(html).toContain("Download archived audio");
  });
});
