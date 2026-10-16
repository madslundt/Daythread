import { useEffect, useRef, useState } from "react";
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
  const [error, setError] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
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
      for (let i = current.completed; i < current.ids.length; i++) {
        const offset = i * chunkLength;
        setProgress(`Uploading recording ${i + 1} of ${current.ids.length}…`);
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
      onCancel={(event) => {
        if (busy) event.preventDefault();
        else onClose();
      }}
      className="upload-dialog"
    >
      <form onSubmit={upload}>
        <div className="section-heading">
          <h2>Upload recording</h2>
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
        <p className="muted">
          Add audio to your memory. Conversion to mono 16 kHz WAV happens in
          this browser; longer recordings are split automatically.
        </p>
        <label htmlFor="audio-file">Audio file</label>
        <input
          id="audio-file"
          type="file"
          accept="audio/*,.wav,.m4a,.mp3,.ogg,.flac"
          onChange={(event) => setFile(event.target.files?.[0])}
          required
          disabled={busy}
        />
        <label htmlFor="capture-time">Recording started</label>
        <input
          id="capture-time"
          type="datetime-local"
          value={startedAt}
          onChange={(event) => setStartedAt(event.target.value)}
          max={localDateTimeInput()}
          required
          disabled={busy}
        />
        <p className="caption">
          Time on this device:{" "}
          {Intl.DateTimeFormat().resolvedOptions().timeZone}. Choose when the
          original recording began.
        </p>
        {error ? <ErrorState message={error} /> : null}
        {progress ? (
          <p role="status" className="caption">
            {progress}
          </p>
        ) : null}
        <div className="button-row">
          <button className="button accent" disabled={!file || busy}>
            <Icon name="upload" size={18} />
            {busy ? "Uploading…" : "Upload recording"}
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
