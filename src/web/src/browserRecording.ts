import { newId } from "./api";
import { t } from "./i18n";

export type BrowserRecording = {
  id: string; owner: string; startedAt: string; stoppedAt: string; name: string; type: string; size: number;
};
export type CapturedRecording = { recording: BrowserRecording; audio: Blob };
export type MicrophoneCapture = { startedAt: string; stop: () => void };

export function microphoneAvailable(): boolean {
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";
}

/** Capture stays local. The final dataavailable event precedes stop. */
export async function startMicrophoneCapture(owner: string, onStopped: (value?: CapturedRecording) => void,
  onError: (message: string) => void): Promise<MicrophoneCapture> {
  if (!microphoneAvailable()) throw new Error(t("Microphone recording requires HTTPS or localhost and a supported browser."));
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  try {
    const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    const id = newId(), startedAt = new Date().toISOString();
    let size = 0, stoppedAt = "", finished = false;
    let limit: ReturnType<typeof setTimeout> | undefined;
    const release = () => { clearTimeout(limit); stream.getTracks().forEach((track) => track.stop()); };
    const stop = () => {
      if (recorder.state !== "inactive") {
        stoppedAt = new Date().toISOString();
        recorder.stop();
      }
    };
    recorder.ondataavailable = (event) => {
      if (event.data.size) { chunks.push(event.data); size += event.data.size; }
      if (size >= 90_000_000 && recorder.state !== "inactive") {
        onError(t("Recording stopped at the browser recording limit.")); stop();
      }
    };
    recorder.onerror = () => { onError(t("The microphone was interrupted. Any captured audio is kept for review.")); stop(); };
    recorder.onstop = () => {
      if (finished) return;
      finished = true;
      release();
      const type = recorder.mimeType || chunks[0]?.type || "audio/webm";
      const audio = new Blob(chunks, { type });
      if (!audio.size) { onError(t("No audio was captured. Try recording again.")); onStopped(); return; }
      const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
      onStopped({ audio, recording: { id, owner, startedAt, stoppedAt: stoppedAt || new Date().toISOString(),
        name: `browser-recording-${id}.${extension}`, type, size: audio.size } });
    };
    recorder.start(1000);
    limit = setTimeout(() => { onError(t("Recording stopped at the browser recording limit.")); stop(); }, 7_199_000);
    return { startedAt, stop };
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
}

/** Keep metadata light; audio is loaded only when playing or uploading one item. */
export class BrowserRecordingStore {
  constructor(private readonly factory: IDBFactory = indexedDB) {}
  private open(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const request = this.factory.open("daythread-browser-recordings-v1", 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore("recordings", { keyPath: "id" }).createIndex("owner", "owner");
        request.result.createObjectStore("audio");
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error(t("Close other Daythread tabs and try saving again.")));
    });
  }
  private async transaction<T>(mode: IDBTransactionMode,
    action: (transaction: IDBTransaction, done: (value: T) => void) => void): Promise<T> {
    const db = await this.open();
    try {
      return await new Promise<T>((resolve, reject) => {
        const tx = db.transaction(["recordings", "audio"], mode);
        let value: T;
        tx.oncomplete = () => resolve(value);
        tx.onerror = tx.onabort = () => reject(tx.error ?? new Error(t("The browser could not save this recording.")));
        try { action(tx, (result) => { value = result; }); }
        catch (error) { tx.abort(); reject(error); }
      });
    } finally { db.close(); }
  }
  save(value: CapturedRecording): Promise<void> {
    return this.transaction("readwrite", (tx, done) => {
      tx.objectStore("recordings").put(value.recording);
      tx.objectStore("audio").put(value.audio, value.recording.id);
      done(undefined);
    });
  }
  list(owner: string): Promise<BrowserRecording[]> {
    return this.transaction("readonly", (tx, done) => {
      const request = tx.objectStore("recordings").index("owner").getAll(owner);
      request.onsuccess = () => done((request.result as BrowserRecording[]).sort((a, b) => b.startedAt.localeCompare(a.startedAt)));
    });
  }
  load(owner: string, id: string): Promise<CapturedRecording | undefined> {
    return this.transaction("readonly", (tx, done) => {
      const metadata = tx.objectStore("recordings").get(id);
      const audio = tx.objectStore("audio").get(id);
      audio.onsuccess = () => {
        const recording = metadata.result as BrowserRecording | undefined;
        done(recording?.owner === owner && audio.result instanceof Blob ? { recording, audio: audio.result } : undefined);
      };
    });
  }
  remove(owner: string, id: string): Promise<void> {
    return this.transaction("readwrite", (tx, done) => {
      const request = tx.objectStore("recordings").get(id);
      request.onsuccess = () => {
        if ((request.result as BrowserRecording | undefined)?.owner === owner) {
          tx.objectStore("recordings").delete(id); tx.objectStore("audio").delete(id);
        }
        done(undefined);
      };
    });
  }
}

export function capturedRecordingFile(value: CapturedRecording): File {
  return new File([value.audio], value.recording.name, { type: value.recording.type, lastModified: Date.parse(value.recording.stoppedAt) });
}
