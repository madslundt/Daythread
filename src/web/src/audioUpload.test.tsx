import { prepareSamples } from "./audioUpload.testSupport";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { progressTime, recordingEndTime, recordingTimes, selectRecording, selectRecordings, RecordingUploadQueue, type RecordingSelection, type PrepareRecording, type PreparedRecording, type RecordingUploadAPI, recordingFileDigest, uploadEstimate, validateUploadReceipt } from "./audioUpload";
import { newId } from "./api";
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
  it("accepts multiple initial drops and gives every recording its own capture time", () => {
    const first = new File(["audio"], "first.wav", { lastModified: Date.parse("2026-10-15T12:00:00Z") });
    const second = new File(["audio"], "second.m4a", { lastModified: Date.parse("2026-10-16T09:00:00Z") });
    const html = renderToStaticMarkup(<UploadDialog initialFiles={[first, second]} onClose={() => {}} onUploaded={() => {}} />);
    expect(html).toContain("2 recordings selected");
    expect(html).toContain("Checking files for duplicates before upload");
    expect(html.match(/<input[^>]+name="recorded_at"[^>]*>/)?.[0]).toContain(`value="${recordingEndTime(first)}"`);
    expect(html.match(/<input[^>]+name="recorded_at_1"[^>]*>/)?.[0]).toContain(`value="${recordingEndTime(second)}"`);
    expect(html).not.toContain('role="alert"');
    expect(selectRecordings({ 0: first, 1: second, length: 2 })).toEqual([first, second]);
    expect(() => selectRecordings([first, new File([], "bad.wav")])).toThrow("bad.wav: The recording is empty");
  });
  it("passes every dropped file to the upload dialog", () => {
    const files = [new File(["audio"], "first.wav"), new File(["audio"], "second.wav")];
    const onChoose = vi.fn();
    AudioDropArea({ busy: false, dragging: false, onChoose, onDragging: vi.fn() })
      .props.onDrop({ preventDefault: vi.fn(), dataTransfer: { files } });
    expect(onChoose).toHaveBeenCalledWith(files);
  });
  it("shows the existing validation errors for invalid drops on Today", () => {
    const wav = new File(["audio"], "memo.wav");
    for (const files of [[wav, new File(["text"], "notes.txt")], [new File([], "empty.wav")]]) {
      const html = renderToStaticMarkup(<UploadDialog initialFiles={files} onClose={() => {}} onUploaded={() => {}} />);
      expect(html).toContain('role="alert"');
      expect(html).toContain("Select one or more audio files");
      expect(html.match(/<button[^>]+class="button accent"[^>]*>/)?.[0]).toContain('disabled=""');
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
  it("accepts large recordings while checking filename limits before reading audio", () => {
    const large = new File(["audio"], "recording.wav");
    Object.defineProperty(large, "size", { value: 100_000_001 });
    expect(selectRecording([large])).toBe(large);
    expect(() => selectRecording([new File(["audio"], "x".repeat(252) + ".wav")])).toThrow("255 characters");
  });
  it("offers keyboard file selection alongside the drop area without requiring native input files", () => {
    const html = renderToStaticMarkup(<UploadDialog onClose={() => {}} onUploaded={() => {}} />);
    expect(html).toContain("Drag and drop recordings");
    expect(html).toContain("Choose audio files");
    expect(html.match(/<input[^>]+type="file"[^>]*>/)?.[0]).toContain('multiple=""');
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


describe("bulk recording upload retries", () => {
  const first: RecordingSelection = {
    file: new File(["audio"], "first.wav"), recordedAt: "2026-10-15T12:05:00Z", timeReference: "end",
  };
  const second: RecordingSelection = {
    file: new File(["different audio"], "second.wav"), recordedAt: "2026-10-16T09:00:00Z", timeReference: "start",
  };
  const scope = { id: "test-profile", signal: new AbortController().signal };
  function receipt(path: string, body: Blob) {
    const url = new URL(path, "http://localhost");
    return { id: url.pathname.split("/").at(-1), device_id: "manual-web",
      started_at: url.searchParams.get("started_at"),
      ended_at: new Date(Date.parse(url.searchParams.get("started_at")!) + (body.size - 44) / 32).toISOString(),
    };
  }
  function createQueue(decode: (file: File) => Promise<Float32Array>, send: (path: string, body: Blob) => Promise<unknown>) {
    const plans = new Map<string, Extract<PreparedRecording, { status: "upload" }>>();
    const backend: RecordingUploadAPI = {
      lookup: async () => ({ duplicate: false }),
      prepare: async (body) => {
        let plan = plans.get(body.file_digest);
        if (!plan) {
          plan = { ...body, status: "upload", recording_id: newId(),
            parts: body.digests.map((digest) => ({ id: newId(), digest, uploaded: false })) };
          plans.set(body.file_digest, plan);
        }
        return structuredClone(plan);
      },
    };
    return new RecordingUploadQueue(prepareSamples(decode), async (path, body) => {
      const result = await send(path, body);
      const actual = result as { id?: string };
      for (const plan of plans.values()) for (const part of plan.parts)
        if (part.id === actual.id) part.uploaded = true;
      return result;
    }, backend);
  }
  it("preserves file provenance and distinct recording IDs and capture bounds", async () => {
    const decode = vi.fn(async () => new Float32Array(16000));
    const send = vi.fn(async (path: string, body: Blob) => receipt(path, body));
    const queue = createQueue(decode, send);
    for (const recording of [first, second]) await queue.upload(recording, "conversation", scope.signal, scope, vi.fn(), vi.fn());
    const urls = send.mock.calls.map(([path]) => new URL(path, "http://localhost"));
    expect(urls.map((url) => url.searchParams.get("source_id"))).toEqual(["first.wav", "second.wav"]);
    expect(urls.map((url) => url.searchParams.get("started_at"))).toEqual(["2026-10-15T12:04:59.000Z", "2026-10-16T09:00:00.000Z"]);
    expect(new Set(urls.map((url) => url.searchParams.get("recording_id"))).size).toBe(2);
    for (const url of urls) expect(url.searchParams.get("conversation_source_id")).toBe("conversation");
    expect(queue.completed.size).toBe(2);
    await queue.upload(first, "", scope.signal, scope, vi.fn(), vi.fn());
    expect(decode).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenCalledTimes(2);
  });
  it("retries the unconfirmed chunk with identical IDs and skips saved files and parts", async () => {
    const decode = vi.fn(async (file: File) => new Float32Array(file === second.file ? 16000 * 121 : 16000));
    let fail = true;
    const send = vi.fn(async (path: string, body: Blob) => {
      if (new URL(path, "http://localhost").searchParams.get("part_index") === "1" && fail) {
        fail = false;
        return { ...receipt(path, body), id: "mismatched-receipt" };
      }
      return receipt(path, body);
    });
    const queue = createQueue(decode, send);
    await queue.upload(first, "", scope.signal, scope, vi.fn(), vi.fn());
    await expect(queue.upload(second, "", scope.signal, scope, vi.fn(), vi.fn())).rejects.toThrow("mismatched upload receipt");
    expect(queue.completed.has(first.file)).toBe(true);
    expect(queue.completed.has(second.file)).toBe(false);
    expect(queue.hasPlan(second.file)).toBe(true);
    await queue.upload(first, "", scope.signal, scope, vi.fn(), vi.fn());
    await queue.upload(second, "", scope.signal, scope, vi.fn(), vi.fn());
    expect(send).toHaveBeenCalledTimes(4);
    expect(send.mock.calls[3][0]).toBe(send.mock.calls[2][0]);
    expect(queue.completed.size).toBe(2);
  });
  it("keeps a stable plan even when the first chunk fails without an acknowledgement", async () => {
    const decode = vi.fn(async () => new Float32Array(16000));
    const send = vi.fn(async (path: string, body: Blob) => receipt(path, body))
      .mockRejectedValueOnce(new Error("Connection lost"));
    const queue = createQueue(decode, send);
    await expect(queue.upload(first, "", scope.signal, scope, vi.fn(), vi.fn())).rejects.toThrow("Connection lost");
    await queue.upload(second, "", scope.signal, scope, vi.fn(), vi.fn());
    await queue.upload(first, "", scope.signal, scope, vi.fn(), vi.fn());
    expect(send.mock.calls[2][0]).toBe(send.mock.calls[0][0]);
    expect(queue.completed.size).toBe(2);
  });
  it("stops after a profile change during local decoding before sending any file", async () => {
    const profile = new AbortController();
    const decode = vi.fn(async () => { profile.abort(); return new Float32Array(16000); });
    const send = vi.fn();
    const queue = createQueue(decode, send);
    await expect(queue.upload(first, "", scope.signal, { id: "old", signal: profile.signal }, vi.fn(), vi.fn()))
      .rejects.toMatchObject({ name: "AbortError" });
    expect(send).not.toHaveBeenCalled();
  });
});

describe("duplicate recording detection", () => {
  const signal = new AbortController().signal;
  const scope = { id: "default", signal };
  const first: RecordingSelection = {
    file: new File(["first export"], "first.wav"), recordedAt: "2026-10-15T12:00:00Z", timeReference: "end",
  };
  function plan(body: PrepareRecording): Extract<PreparedRecording, { status: "upload" }> {
    return { ...body, status: "upload", recording_id: newId(),
      parts: body.digests.map((digest) => ({ id: newId(), digest, uploaded: false })) };
  }
  function receipt(path: string, body: Blob) {
    const url = new URL(path, "http://localhost");
    return { id: url.pathname.split("/").at(-1), device_id: "manual-web",
      started_at: url.searchParams.get("started_at"),
      ended_at: new Date(Date.parse(url.searchParams.get("started_at")!) + (body.size - 44) / 32).toISOString() };
  }
  it("hashes actual bytes on LAN HTTP without relying on browser Web Crypto", async () => {
    vi.stubGlobal("crypto", {});
    try {
      expect(await recordingFileDigest(new File(["abc"], "memo.wav"), signal))
        .toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
      const a = await recordingFileDigest(new File(["same"], "a.wav"), signal);
      expect(await recordingFileDigest(new File(["same"], "renamed.wav"), signal)).toBe(a);
      expect(await recordingFileDigest(new File(["different"], "a.wav"), signal)).not.toBe(a);
    } finally { vi.unstubAllGlobals(); }
  });
  it("finds duplicate selected bytes before decoding or sending audio, including renamed copies", async () => {
    const copy = new File(["first export"], "renamed.wav");
    const sameNameDifferentBytes = new File(["different"], "first.wav");
    const backend = { lookup: vi.fn(async () => ({ duplicate: false })), prepare: vi.fn() };
    const decode = vi.fn(), send = vi.fn();
    const queue = new RecordingUploadQueue(prepareSamples(decode), send, backend);
    await queue.inspect([first.file, copy, sameNameDifferentBytes], signal, scope);
    expect(queue.skipped.get(copy)).toContain("first.wav");
    expect(queue.skipped.has(first.file)).toBe(false);
    expect(queue.skipped.has(sameNameDifferentBytes)).toBe(false);
    expect(backend.lookup).toHaveBeenCalledTimes(2);
    await queue.upload({ ...first, file: copy }, "", signal, scope, vi.fn(), vi.fn());
    expect(decode).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    await queue.inspect([copy], signal, scope);
    expect(queue.skipped.has(copy)).toBe(false); // Removing the original promotes its copy.
  });
  it("skips an archived duplicate without decoding or uploading any audio", async () => {
    const backend = { lookup: vi.fn(async () => ({ duplicate: true, source_id: "original.m4a" })), prepare: vi.fn() };
    const decode = vi.fn(), send = vi.fn();
    const queue = new RecordingUploadQueue(prepareSamples(decode), send, backend);
    await queue.upload(first, "", signal, scope, vi.fn(), vi.fn());
    expect(queue.skipped.get(first.file)).toContain("original.m4a");
    expect(decode).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    expect(backend.prepare).not.toHaveBeenCalled();
  });
  it("checks every converted part before transfer and skips identical decoded audio", async () => {
    const backend: RecordingUploadAPI = {
      lookup: async () => ({ duplicate: false }),
      prepare: vi.fn().mockImplementationOnce(async (body: PrepareRecording) => plan(body))
        .mockResolvedValueOnce({ status: "duplicate", source_id: "first.wav" }),
    };
    const send = vi.fn(async (path: string, body: Blob) => receipt(path, body));
    const queue = new RecordingUploadQueue(prepareSamples(async () => new Float32Array(16000 * 121)), send, backend);
    await queue.upload(first, "", signal, scope, vi.fn(), vi.fn());
    const copy = { ...first, file: new File(["different container"], "another-export.m4a") };
    await queue.upload(copy, "", signal, scope, vi.fn(), vi.fn());
    const prepare = vi.mocked(backend.prepare);
    expect(prepare.mock.calls[0][0].digests).toHaveLength(2);
    expect(prepare.mock.calls[1][0].digests).toEqual(prepare.mock.calls[0][0].digests);
    expect(send).toHaveBeenCalledTimes(2); // Two parts of only the first recording.
    expect(queue.skipped.get(copy.file)).toContain("first.wav");
    expect(queue.completed.size).toBe(1);
  });
  it("resumes an existing partial upload using canonical metadata and only missing parts", async () => {
    const backend: RecordingUploadAPI = {
      lookup: async () => ({ duplicate: false }),
      prepare: async (body) => {
        const original = plan(body);
        original.source_id = "original.wav";
        original.started_at = "2026-10-14T12:00:00Z";
        original.ended_at = "2026-10-14T12:02:01Z";
        original.parts[0].uploaded = true;
        return original;
      },
    };
    const send = vi.fn(async (path: string, body: Blob) => receipt(path, body));
    const queue = new RecordingUploadQueue(prepareSamples(async () => new Float32Array(16000 * 121)), send, backend);
    await queue.upload(first, "", signal, scope, vi.fn(), vi.fn());
    expect(send).toHaveBeenCalledOnce();
    const query = new URL(send.mock.calls[0][0], "http://localhost").searchParams;
    expect(query.get("source_id")).toBe("original.wav");
    expect(query.get("part_index")).toBe("1");
    expect(query.get("started_at")).toBe("2026-10-14T12:02:00.000Z");
  });
});
