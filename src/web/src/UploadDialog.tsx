import { profileScope } from "./profiles";
import { useEffect, useId, useRef, useState } from "react";
import { decodeRecording, encodeWav, recordingTimes, selectRecording, validateUploadReceipt, type RecordingTimeReference } from "./audioUpload";
import { errorMessage, newId, request } from "./api";
import { localDateTimeInput } from "./format";
import { Icon } from "./Icon";
import { ErrorState } from "./UI";

export function UploadDialog({
  onClose,
  onUploaded,
}: {
  onClose: () => void;
  onUploaded: () => void;
}) {
  const scope = useRef(profileScope());
  const controller = useRef(new AbortController());
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const description = useId();
  const plan = useRef<
    | {
        file: File;
        startedAt: string;
        samples: Float32Array;
        ids: string[];
        completed: number;
      }
    | undefined
  >(undefined);
  const [file, setFile] = useState<File>();
  const [recordedAt, setRecordedAt] = useState(() => localDateTimeInput());
  const [timeReference, setTimeReference] = useState<RecordingTimeReference>("end");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [uploadProgress, setUploadProgress] = useState<{ completed: number; total: number }>();
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    controller.current = new AbortController();
    const element = dialog.current;
    const previousFocus = document.activeElement;
    element?.showModal();
    element?.querySelector<HTMLInputElement>('input[type="file"]')?.focus();
    return () => {
      controller.current.abort();
      element?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);
  function chooseRecording(files: ArrayLike<File>) {
    if (busy) return;
    try {
      const selected = selectRecording(files);
      setFile(selected);
      plan.current = undefined;
      setError("");
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setUploadProgress(undefined);
    setProgress("Preparing audio locally…");
    try {
      const form = new FormData(event.currentTarget);
      const timestamp = String(form.get("recorded_at") ?? recordedAt);
      const reference = form.get("time_reference") === "start" ? "start" : "end";
      setRecordedAt(timestamp);
      setTimeReference(reference);
      const samples = plan.current?.file === file
        ? plan.current.samples
        : await decodeRecording(file);
      controller.current.signal.throwIfAborted();
      scope.current.signal.throwIfAborted();
      const { startedAt: start } = recordingTimes(timestamp, samples.length, reference);
      const recordingStarted = start.toISOString();
      const chunkLength = 16000 * 120;
      if (
        !plan.current ||
        plan.current.file !== file ||
        plan.current.startedAt !== recordingStarted
      ) {
        plan.current = {
          file,
          startedAt: recordingStarted,
          samples,
          ids: Array.from(
            { length: Math.ceil(samples.length / chunkLength) },
            newId,
          ),
          completed: 0,
        };
      }
      const current = plan.current;
      setUploadProgress({ completed: current.completed, total: current.ids.length });
      for (let i = current.completed; i < current.ids.length; i++) {
        const offset = i * chunkLength;
        const samples = current.samples.subarray(offset, offset + chunkLength);
        const partStartedAt = new Date(start.getTime() + offset / 16).toISOString();
        setProgress(`Uploading part ${i + 1} of ${current.ids.length}… Keep this window open.`);
        const query = new URLSearchParams({
          device_id: "manual-web",
          started_at: partStartedAt,
          source_kind: "manual_upload",
          source_id: file.name,
        });
        const receipt = await request(`/v1/audio/${current.ids[i]}?${query}`, {
          method: "PUT",
          signal: controller.current.signal,
          headers: { "Content-Type": "audio/wav" },
          body: new Blob(
            [encodeWav(samples)],
            { type: "audio/wav" },
          ),
        }, scope.current);
        validateUploadReceipt(receipt, {
          id: current.ids[i],
          device_id: "manual-web",
          started_at: partStartedAt,
          ended_at: new Date(start.getTime() + (offset + samples.length) / 16).toISOString(),
        });
        current.completed = i + 1;
        setUploadProgress({ completed: current.completed, total: current.ids.length });
      }
      onUploaded();
      onClose();
    } catch (error) {
      setError(
        `${errorMessage(error)}${plan.current?.completed ? " Retry to resume the remaining recordings without duplicating uploads." : ""}`,
      );
    } finally {
      setBusy(false);
      setProgress("");
    }
  }
  return (
    <dialog
      ref={dialog}
      aria-labelledby={heading}
      aria-describedby={description}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      className="upload-dialog"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => event.preventDefault()}
    >
      <form onSubmit={upload} aria-busy={busy}>
        <div className="section-heading">
          <h2 id={heading}>Upload recording</h2>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close upload"
          >
            <Icon name="close" />
          </button>
        </div>
        <p className="muted" id={description}>
          Import audio from another phone, voice recorder, or computer, including
          exported Voice Memos. Choose a file and when the recording stopped
          or began. Long recordings are uploaded in parts automatically.
        </p>
        <AudioDropArea file={file} busy={busy} dragging={dragging}
          onDragging={setDragging} onChoose={chooseRecording} />
        <p className="caption" id="audio-file-hint">WAV, M4A, MP3, OGG, FLAC, or another format supported by your browser. Up to 100 MB and two hours. Audio is converted locally before upload; keep this window open until it finishes.</p>
        <label htmlFor="capture-time-reference">Time refers to</label>
        <select id="capture-time-reference" name="time_reference" value={timeReference}
          onChange={(event) => { setTimeReference(event.target.value === "start" ? "start" : "end"); setError(""); }}
          disabled={busy}>
          <option value="end">Recording ended</option>
          <option value="start">Recording started</option>
        </select>
        <label htmlFor="capture-time">{timeReference === "end" ? "Recording ended" : "Recording started"}</label>
        <input
          id="capture-time"
          name="recorded_at"
          type="datetime-local"
          value={recordedAt}
          aria-describedby="capture-time-hint"
          onChange={(event) => { setRecordedAt(event.target.value); setError(""); }}
          max={localDateTimeInput()}
          required
          disabled={busy}
        />
        <p className="caption" id="capture-time-hint">
          Time on this device:{" "}
          {Intl.DateTimeFormat().resolvedOptions().timeZone}.{" "}
          {timeReference === "end"
            ? "Choose when the recording stopped. Its start time is calculated from the audio duration."
            : "Choose when the recording began. Its end time is calculated from the audio duration."}
        </p>
        {error ? <ErrorState message={error} /> : null}
        {busy ? <progress className="upload-progress" aria-label="Recording upload progress"
          value={uploadProgress?.completed} max={uploadProgress?.total ?? 1} /> : null}
        {progress ? (
          <p role="status" className="caption">
            {progress}
          </p>
        ) : null}
        <div className="button-row">
          <button className="button accent" disabled={!file || busy}>
            <Icon name="upload" size={18} />
            {busy ? "Uploading…" : error && plan.current?.completed ? "Resume upload" : "Upload recording"}
          </button>
          <button
            className="button"
            type="button"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function AudioDropArea({ file, busy, dragging, onDragging, onChoose }: {
  file?: File;
  busy: boolean;
  dragging: boolean;
  onDragging: (dragging: boolean) => void;
  onChoose: (files: ArrayLike<File>) => void;
}) {
  return (
    <div
      className={`audio-drop-zone${dragging ? " dragging" : ""}${busy ? " disabled" : ""}`}
      role="group"
      aria-label="Audio file drop area"
      aria-disabled={busy}
      onDragEnter={(event) => { event.preventDefault(); if (!busy) onDragging(true); }}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = busy ? "none" : "copy"; }}
      onDragLeave={(event) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) onDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        onDragging(false);
        if (!busy) onChoose(event.dataTransfer.files);
      }}
    >
      <Icon name="upload" size={27} />
      <strong>{dragging ? "Drop your recording here" : "Drag and drop a recording"}</strong>
      <span>or choose an audio file from your device</span>
      <label className="button" htmlFor="audio-file">Choose audio file</label>
      <input
        className="sr-only"
        id="audio-file"
        type="file"
        accept="audio/*,.wav,.m4a,.mp3,.ogg,.flac,.aac,.opus,.aiff,.caf,.webm"
        aria-describedby="audio-file-hint selected-audio-file"
        onChange={(event) => {
          if (event.target.files?.length) onChoose(event.target.files);
          event.target.value = "";
        }}
        disabled={busy}
      />
      <span id="selected-audio-file" className="selected-audio-file" role="status">
        {file ? `${file.name} · ${(file.size / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB` : "One audio file at a time"}
      </span>
    </div>
  );
}
