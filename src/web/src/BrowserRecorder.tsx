import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import { RecordingUploadQueue } from "./audioUpload";
import { BrowserRecordingStore, capturedRecordingFile, microphoneAvailable, startMicrophoneCapture,
  type BrowserRecording, type CapturedRecording, type MicrophoneCapture } from "./browserRecording";
import { dateTimeLabel } from "./format";
import { t } from "./i18n";
import { Icon } from "./Icon";
import { profileScope } from "./profiles";
import { Notice } from "./UI";

// Retain a failed browser write across profile switches during this visit.
const pendingSaves = new Map<string, CapturedRecording>();
if (typeof window !== "undefined") window.addEventListener("beforeunload", (event) => {
  if (pendingSaves.size) { event.preventDefault(); event.returnValue = ""; }
});

export function BrowserRecorder({ owner, timezone, visible, onUploaded }: {
  owner: string; timezone: string; visible: boolean; onUploaded: () => void;
}) {
  const scope = useRef(profileScope()).current;
  const store = useRef<BrowserRecordingStore | undefined>(undefined);
  const capture = useRef<MicrophoneCapture | undefined>(undefined);
  const alive = useRef(true);
  const transfer = useRef(new AbortController());
  const [recordings, setRecordings] = useState<BrowserRecording[]>([]);
  const [unsaved, setUnsaved] = useState<CapturedRecording | undefined>(() => pendingSaves.get(owner));
  const [active, setActive] = useState("");
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [review, setReview] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const database = () => store.current ??= new BrowserRecordingStore();
  useEffect(() => {
    alive.current = true;
    void Promise.resolve().then(() => database().list(owner)).then((items) => {
      if (alive.current) setRecordings((current) => [...current, ...items.filter((item) => !current.some((other) => other.id === item.id))]);
    }).catch((error) => { if (alive.current) setError(errorMessage(error)); });
    const stop = () => capture.current?.stop();
    scope.signal.addEventListener("abort", stop);
    return () => {
      alive.current = false;
      scope.signal.removeEventListener("abort", stop);
      capture.current?.stop();
      transfer.current.abort();
    };
  }, [owner, scope]);
  useEffect(() => {
    if (!active) return;
    const update = () => setElapsed(Math.floor((Date.now() - Date.parse(active)) / 1000));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    if (!active && !starting && !saving && !unsaved) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active, starting, saving, unsaved]);

  async function save(value: CapturedRecording) {
    pendingSaves.set(owner, value);
    if (alive.current) setSaving(true);
    try {
      await database().save(value);
      if (pendingSaves.get(owner) === value) pendingSaves.delete(owner);
      if (alive.current) {
        setUnsaved(undefined);
        setRecordings((items) => [value.recording, ...items.filter((item) => item.id !== value.recording.id)]);
        setReview(value.recording.id);
      }
    } catch {
      if (alive.current) {
        setUnsaved(value);
        setError(t("This recording could not be saved in this browser. Retry saving or download it before leaving this page."));
      }
    } finally { if (alive.current) setSaving(false); }
  }
  async function start() {
    if (starting || capture.current || saving || unsaved || busy) return;
    setStarting(true); setError(""); setNotice("");
    try {
      const value = await startMicrophoneCapture(owner, (value) => {
        capture.current = undefined;
        if (alive.current) setActive("");
        if (value) void save(value);
      }, (message) => { if (alive.current) setError(message); });
      capture.current = value;
      if (!alive.current || scope.signal.aborted) value.stop();
      else setActive(value.startedAt);
    } catch (error) {
      if (alive.current) setError(error instanceof DOMException && ["NotAllowedError", "SecurityError"].includes(error.name)
        ? t("Allow microphone access in your browser to start recording.") : errorMessage(error));
    } finally { if (alive.current) setStarting(false); }
  }
  async function upload(recording: BrowserRecording) {
    setBusy(recording.id); setError(""); setNotice("");
    try {
      const value = await database().load(owner, recording.id);
      if (!value) throw new Error(t("This local recording is no longer available."));
      scope.signal.throwIfAborted(); transfer.current.signal.throwIfAborted();
      const queue = new RecordingUploadQueue();
      await queue.upload({ file: capturedRecordingFile(value), recordedAt: recording.startedAt, timeReference: "start" },
        "", transfer.current.signal, scope, (stage) => { if (alive.current) setProgress(stage); }, () => {});
      await database().remove(owner, recording.id);
      if (alive.current) {
        setRecordings((items) => items.filter((item) => item.id !== recording.id));
        setReview(""); onUploaded();
      }
    } catch (error) { if (alive.current && !scope.signal.aborted) setError(errorMessage(error)); }
    finally { if (alive.current) { setBusy(""); setProgress(""); } }
  }
  async function remove(recording: BrowserRecording) {
    setBusy(recording.id); setError("");
    try {
      await database().remove(owner, recording.id);
      if (alive.current) {
        setRecordings((items) => items.filter((item) => item.id !== recording.id));
        if (review === recording.id) setReview("");
      }
    } catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { if (alive.current) setBusy(""); }
  }
  function download(value: CapturedRecording) {
    const url = URL.createObjectURL(value.audio);
    const link = document.createElement("a"); link.href = url; link.download = value.recording.name; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  async function downloadSaved(recording: BrowserRecording) {
    setBusy(recording.id); setError("");
    try {
      const value = await database().load(owner, recording.id);
      if (!value) throw new Error(t("This local recording is no longer available."));
      if (alive.current) download(value);
    } catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { if (alive.current) setBusy(""); }
  }
  return <section className="browser-recorder" hidden={!visible && !active && !recordings.length && !unsaved && !saving} aria-label={t("Record audio")}>
    <div className="section-heading">
      <h2>{t("Record audio")}</h2>
      {active ? <div className="button-row">
        <span className="recording-clock" role="timer">{t("Recording · {time}", { time: `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}` })}</span>
        <button className="button dark" onClick={() => capture.current?.stop()}>{t("Stop recording")}</button>
      </div> : <button className="button accent" disabled={starting || saving || !!unsaved || !!busy || !microphoneAvailable()}
        onClick={() => void start()}><Icon name="mic" />{starting ? t("Opening microphone…") : t("Start recording")}</button>}
    </div>
    <p className="caption">{microphoneAvailable()
      ? t("Record with this device’s microphone. After stopping, choose Upload now, Remove, or Upload later.")
      : t("Microphone recording requires HTTPS or localhost and a supported browser.")}</p>
    {active ? <p className="caption">{t("Keep this page open while recording. Recording stops automatically before two hours.")}</p> : null}
    {error ? <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice> : null}
    {notice ? <Notice onDismiss={() => setNotice("")}>{notice}</Notice> : null}
    {saving ? <p role="status">{t("Saving recording on this device…")}</p> : null}
    {unsaved ? <div className="button-row">
      <button className="button" onClick={() => { setError(""); void save(unsaved); }} disabled={saving}>{t("Retry saving")}</button>
      <button className="button" onClick={() => download(unsaved)}>{t("Download recording")}</button>
      <button className="button" onClick={() => { pendingSaves.delete(owner); setUnsaved(undefined); setError(""); }}>{t("Remove")}</button>
    </div> : null}
    {recordings.length ? <div className="local-recordings">
      <h3>{t("Recordings on this device")}</h3>
      <p className="caption">{t("Saved in this browser for this profile until you upload or remove them. Clearing browser data removes local recordings.")}</p>
      {recordings.map((recording) => <article key={recording.id} className="local-recording">
        <p>{dateTimeLabel(recording.startedAt, timezone)}</p>
        {review === recording.id ? <LocalRecordingPlayer owner={owner} recording={recording} store={database()} /> : null}
        <div className="button-row">
          <button className="button accent" disabled={!!busy || saving} onClick={() => void upload(recording)}>{t("Upload now")}</button>
          <button className="button" disabled={!!busy} onClick={() => { setReview(""); setNotice(t("Recording saved on this device. Upload it from Recordings on this device when you’re ready.")); }}>{t("Upload later")}</button>
          <button className="text-button" disabled={!!busy} onClick={() => setReview(review === recording.id ? "" : recording.id)}>{t("Listen")}</button>
          <button className="text-button" disabled={!!busy} onClick={() => void downloadSaved(recording)}>{t("Download recording")}</button>
          <button className="text-button danger" disabled={!!busy} onClick={() => void remove(recording)}>{t("Remove")}</button>
        </div>
        {busy === recording.id && progress ? <p className="caption" role="status">{progress}</p> : null}
      </article>)}
    </div> : null}
  </section>;
}

function LocalRecordingPlayer({ owner, recording, store }: { owner: string; recording: BrowserRecording; store: BrowserRecordingStore }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true, objectURL = "";
    void store.load(owner, recording.id).then((value) => {
      if (!alive) return;
      if (!value) { setError(t("This local recording is no longer available.")); return; }
      objectURL = URL.createObjectURL(value.audio); setUrl(objectURL);
    }).catch((error) => { if (alive) setError(errorMessage(error)); });
    return () => { alive = false; if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [owner, recording.id, store]);
  return error ? <p role="alert">{error}</p> : url ? <audio controls src={url} aria-label={t("Local recording playback")} /> : null;
}
