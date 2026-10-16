import { useEffect, useId, useRef, useState } from "react";
import { decodeRecording, encodeWav } from "./audioUpload";
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
  const [startedAt, setStartedAt] = useState(() => localDateTimeInput());
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [uploadProgress, setUploadProgress] = useState<{ completed: number; total: number }>();
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement;
    element?.showModal();
    element?.querySelector<HTMLInputElement>('input[type="file"]')?.focus();
    return () => {
      element?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);
  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setUploadProgress(undefined);
    setProgress("Preparing audio locally…");
    try {
      const start = new Date(startedAt);
      if (
        !Number.isFinite(start.getTime()) ||
        start.getTime() > Date.now() + 30_000
      )
        throw new Error(
          "Choose the time this recording began, at or before now.",
        );
      const chunkLength = 16000 * 120;
      if (
        !plan.current ||
        plan.current.file !== file ||
        plan.current.startedAt !== startedAt
      ) {
        const samples = await decodeRecording(file);
        if (!samples.length)
          throw new Error("The recording contains no audio.");
        plan.current = {
          file,
          startedAt,
          samples,
          ids: Array.from(
            { length: Math.ceil(samples.length / chunkLength) },
            newId,
          ),
          completed: 0,
        };
      }
      const current = plan.current;
      if (start.getTime() + current.samples.length / 16 > Date.now() + 30_000) {
        throw new Error(
          "This recording would end in the future. Set Recording started to when the original recording actually began.",
        );
      }
      setUploadProgress({ completed: current.completed, total: current.ids.length });
      for (let i = current.completed; i < current.ids.length; i++) {
        const offset = i * chunkLength;
        setProgress(`Uploading part ${i + 1} of ${current.ids.length}… Keep this window open.`);
        const query = new URLSearchParams({
          device_id: "web",
          started_at: new Date(start.getTime() + offset / 16).toISOString(),
        });
        await request(`/v1/audio/${current.ids[i]}?${query}`, {
          method: "PUT",
          headers: { "Content-Type": "audio/wav" },
          body: new Blob(
            [encodeWav(current.samples.subarray(offset, offset + chunkLength))],
            { type: "audio/wav" },
          ),
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
          Bring an existing recording into your memory. Choose an audio file
          and the time it began. Long recordings are uploaded in parts automatically.
        </p>
        <label htmlFor="audio-file">Audio file</label>
        <input
          id="audio-file"
          type="file"
          accept="audio/*,.wav,.m4a,.mp3,.ogg,.flac"
          aria-describedby="audio-file-hint"
          onChange={(event) => { setFile(event.target.files?.[0]); setError(""); }}
          required
          disabled={busy}
        />
        <p className="caption" id="audio-file-hint">WAV, M4A, MP3, OGG, FLAC, or another audio format supported by your browser.</p>
        <label htmlFor="capture-time">Recording started</label>
        <input
          id="capture-time"
          type="datetime-local"
          value={startedAt}
          aria-describedby="capture-time-hint"
          onChange={(event) => { setStartedAt(event.target.value); setError(""); }}
          max={localDateTimeInput()}
          required
          disabled={busy}
        />
        <p className="caption" id="capture-time-hint">
          Time on this device:{" "}
          {Intl.DateTimeFormat().resolvedOptions().timeZone}. Choose when the
          original recording began.
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
