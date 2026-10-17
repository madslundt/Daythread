import { profileScope } from "./profiles";
import { useEffect, useId, useRef, useState } from "react";
import { progressTime, recordingEndTime, selectRecordings, uploadEstimate, RecordingUploadQueue, type RecordingSelection, type UploadProgress } from "./audioUpload";
import { errorMessage } from "./api";
import { dateTimeLabel, localDateTimeInput } from "./format";
import { Icon } from "./Icon";
import type { Source } from "./types";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export function UploadDialog({
  initialFiles,
  onClose,
  onUploaded,
}: {
  initialFiles?: ArrayLike<File>;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const scope = useRef(profileScope());
  const controller = useRef(new AbortController());
  const duplicateCheck = useRef(new AbortController());
  const dialog = useRef<HTMLDialogElement>(null);
  const feedback = useRef<HTMLDivElement>(null);
  const heading = useId();
  const description = useId();
  const [queue] = useState(() => new RecordingUploadQueue());
  const [initialSelection] = useState<{ recordings: RecordingSelection[]; error?: string }>(() => {
    try {
      return { recordings: initialFiles?.length ? selectRecordings(initialFiles).map((file) => ({
        file, recordedAt: recordingEndTime(file), timeReference: "end",
      })) : [] };
    } catch (error) {
      return { recordings: [], error: errorMessage(error) };
    }
  });
  const [recordings, setRecordings] = useState(initialSelection.recordings);
  const [checking, setChecking] = useState(Boolean(initialSelection.recordings.length));
  const [fileErrors, setFileErrors] = useState(new Map<File, string>());
  const [activeIndex, setActiveIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [uploadProgress, setUploadProgress] = useState<UploadProgress>();
  const [started, setStarted] = useState(0);
  const [now, setNow] = useState(0);
  const [error, setError] = useState(initialSelection.error ?? "");
  const [dragging, setDragging] = useState(false);
  const [linkExisting, setLinkExisting] = useState(false);
  const [conversationSourceId, setConversationSourceId] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const conversations = useResource<{ items: Source[] }>(linkExisting
    ? `/v1/transcripts?limit=50&q=${encodeURIComponent(sourceQuery)}` : null);
  const locked = busy || queue.started;
  const transferring = Boolean(uploadProgress);
  useEffect(() => {
    if (busy) feedback.current?.scrollIntoView({ block: "nearest" });
  }, [busy, transferring]);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setNow(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    controller.current = new AbortController();
    const element = dialog.current;
    const previousFocus = document.activeElement;
    element?.showModal();
    element?.querySelector<HTMLInputElement>('input[type="file"]')?.focus();
    return () => {
      controller.current.abort();
      duplicateCheck.current.abort();
      element?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);
  useEffect(() => {
    if (initialSelection.recordings.length)
      void checkSelection(initialSelection.recordings.map((item) => item.file));
  }, [initialSelection]);
  async function checkSelection(files: File[]) {
    duplicateCheck.current.abort();
    const check = new AbortController();
    duplicateCheck.current = check;
    setChecking(true);
    try {
      await queue.inspect(files, check.signal, scope.current);
    } catch (error) {
      if (!check.signal.aborted && !scope.current.signal.aborted) setError(errorMessage(error));
    } finally {
      if (!check.signal.aborted) setChecking(false);
    }
  }
  function chooseRecording(files: ArrayLike<File>) {
    if (locked) return;
    try {
      const selected = selectRecordings(files);
      setRecordings(selected.map((file) => ({ file, recordedAt: recordingEndTime(file), timeReference: "end" })));
      setFileErrors(new Map());
      setLinkExisting(false);
      setConversationSourceId("");
      setSourceQuery("");
      setError("");
      void checkSelection(selected);
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  function updateRecording(index: number, change: Partial<RecordingSelection>) {
    setRecordings((items) => items.map((item, i) => i === index ? { ...item, ...change } : item));
    setFileErrors((errors) => {
      const next = new Map(errors);
      next.delete(recordings[index].file);
      return next;
    });
    setError("");
  }
  function removeRecording(file: File) {
    const remaining = recordings.filter((item) => item.file !== file);
    setRecordings(remaining);
    if (!queue.started) void checkSelection(remaining.map((item) => item.file));
    setFileErrors((errors) => {
      const next = new Map(errors);
      next.delete(file);
      return next;
    });
    setError("");
  }
  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!recordings.length || busy || checking) return;
    if (linkExisting && !conversationSourceId) {
      setError("Choose a source from the same conversation.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const selected = recordings.map((item, index) => ({ ...item,
      recordedAt: String(form.get(index ? `recorded_at_${index}` : "recorded_at") ?? item.recordedAt),
    }));
    setRecordings(selected);
    setBusy(true);
    setError("");
    setFileErrors(new Map());
    setStarted(performance.now());
    setNow(performance.now());
    const completedBefore = queue.completed.size;
    const failures = new Map<File, string>();
    try {
      for (const [index, recording] of selected.entries()) {
        if (queue.completed.has(recording.file) || queue.skipped.has(recording.file)) continue;
        controller.current.signal.throwIfAborted();
        scope.current.signal.throwIfAborted();
        setActiveIndex(index);
        setUploadProgress(undefined);
        setProgress("Preparing audio locally…");
        try {
          await queue.upload(recording, linkExisting ? conversationSourceId : "",
            controller.current.signal, scope.current, setProgress, setUploadProgress);
        } catch (error) {
          controller.current.signal.throwIfAborted();
          scope.current.signal.throwIfAborted();
          failures.set(recording.file, errorMessage(error));
        }
      }
      setFileErrors(failures);
      if (failures.size) {
        setError(`${failures.size} recording${failures.size === 1 ? "" : "s"} could not be uploaded. Retry to resume without duplicating completed uploads.`);
      } else if (!queue.skipped.size) onClose();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      if (!scope.current.signal.aborted && queue.completed.size > completedBefore) onUploaded();
      setBusy(false);
      setProgress("");
    }
  }
  const elapsed = Math.max(0, (now - started) / 1000);
  const estimate = uploadProgress && !uploadProgress.confirming && now - uploadProgress.updated < 15_000
    ? uploadEstimate(uploadProgress.attemptBytes,
      uploadProgress.total - uploadProgress.sent + uploadProgress.attemptBytes,
      (Math.max(now, uploadProgress.updated) - uploadProgress.attemptStarted) / 1000)
    : undefined;
  const percent = uploadProgress ? Math.floor(uploadProgress.sent / uploadProgress.total * 100) : undefined;
  const handled = recordings.length > 0 && recordings.every(({ file }) => queue.completed.has(file) || queue.skipped.has(file));
  const remaining = recordings.length - queue.skipped.size;
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
          <h2 id={heading}>Upload recordings</h2>
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
          exported Voice Memos. Choose one or more files and when each recording stopped
          or began. Long recordings are uploaded in parts automatically.
        </p>
        <AudioDropArea files={recordings.map((item) => item.file)} busy={locked} dragging={dragging}
          onDragging={setDragging} onChoose={chooseRecording} />
        <p className="caption" id="audio-file-hint">WAV, M4A, MP3, OGG, FLAC, or another format supported by your browser. Up to 100 MB and two hours per file. Audio is converted locally before upload; keep this window open until it finishes.</p>
        {checking ? <p role="status" className="caption">Checking files for duplicates before upload…</p> : null}
        {!checking && queue.skipped.size ? <p role="status" className="caption">
          {queue.completed.size} uploaded · {queue.skipped.size} duplicate{queue.skipped.size === 1 ? "" : "s"} skipped.
        </p> : null}
        {(recordings.length ? recordings : [{ recordedAt: "", timeReference: "end" as const, file: undefined }]).map((recording, index) => {
          const { file, recordedAt, timeReference } = recording;
          const suffix = index ? `_${index}` : "";
          const saved = file && queue.completed.has(file);
          const skipped = file && queue.skipped.get(file);
          return <fieldset className="upload-recording" key={index} disabled={busy || checking || Boolean(file && queue.hasPlan(file))}>
            {file ? <legend>{file.name} · {(file.size / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB</legend> : null}
            {skipped ? <p role="status" className="caption">Duplicate skipped: {skipped}</p> : null}
            {saved ? <p role="status" className="caption">Uploaded</p> : null}
            {!skipped ? <>
            <label htmlFor={`capture-time-reference${suffix}`}>Time refers to</label>
            <select id={`capture-time-reference${suffix}`} name={`time_reference${suffix}`} value={timeReference}
              onChange={(event) => updateRecording(index, { timeReference: event.target.value === "start" ? "start" : "end" })}
              disabled={!file}>
              <option value="end">Recording ended</option>
              <option value="start">Recording started</option>
            </select>
            <label htmlFor={`capture-time${suffix}`}>{timeReference === "end" ? "Recording ended" : "Recording started"}</label>
            <input id={`capture-time${suffix}`} name={`recorded_at${suffix}`} type="datetime-local"
              value={recordedAt} aria-describedby={`capture-time-hint${suffix}`}
              onChange={(event) => updateRecording(index, { recordedAt: event.target.value })}
              max={localDateTimeInput()} required disabled={!file} />
            <p className="caption" id={`capture-time-hint${suffix}`}>
              Time on this device: {Intl.DateTimeFormat().resolvedOptions().timeZone}.{" "}
              {timeReference === "end"
                ? "The file’s last-modified date and time is used when available. Otherwise, enter when the recording stopped. You can correct this time. Its start time is calculated from the audio duration."
                : "Choose when the recording began. Its end time is calculated from the audio duration."}
            </p>
            </> : null}
            {file && fileErrors.has(file) ? <ErrorState message={fileErrors.get(file)!} /> : null}
            {file && !queue.hasPlan(file) && recordings.length > 1 ? <button type="button" className="button"
              aria-label={`Remove ${file.name}`} onClick={() => removeRecording(file)}>
              Remove recording
            </button> : null}
          </fieldset>;
        })}
        <label htmlFor="upload-conversation">Conversation</label>
        <select id="upload-conversation" value={linkExisting ? "existing" : "auto"}
          disabled={locked} onChange={(event) => setLinkExisting(event.target.value === "existing")}>
          <option value="auto">Match automatically after transcription</option>
          <option value="existing">Add another source to an existing conversation</option>
        </select>
        <p className="caption">{recordings.length > 1 ? "This conversation choice applies to all selected files. " : ""}Overlapping recordings with shared speech are combined in the recap.
          Every source is kept so details missing from one recording can be included from another.</p>
        {linkExisting ? <>
          <label htmlFor="conversation-search">Find a conversation</label>
          <input id="conversation-search" type="search" value={sourceQuery} disabled={locked}
            placeholder="Search transcript text" onChange={(event) => setSourceQuery(event.target.value)} />
          <label htmlFor="conversation-source">Source from the same conversation</label>
          <select id="conversation-source" value={conversationSourceId} required disabled={locked}
            onChange={(event) => setConversationSourceId(event.target.value)}>
            <option value="">Choose a recording…</option>
            {conversationSourceId && !conversations.data?.items.some((s) => s.id === conversationSourceId)
              ? <option value={conversationSourceId}>Selected conversation</option> : null}
            {conversations.data?.items.filter((s) => s.state !== "expired").map((source) =>
              <option key={source.id} value={source.id}>
                {dateTimeLabel(source.started_at, Intl.DateTimeFormat().resolvedOptions().timeZone)}
                {" · "}{source.title || source.source_id || source.text?.slice(0, 80) || "Recording"}
              </option>)}
          </select>
          {conversations.loading ? <p role="status" className="caption">Loading conversations…</p> : null}
          {conversations.error ? <ErrorState message={conversations.error} retry={conversations.refresh} /> : null}
          <p className="caption">Choose this when you know the sources belong together, including
            recordings with different wording. Keep each recording’s original capture time.</p>
        </> : null}
        {error ? <ErrorState message={error} /> : null}
        {busy ? <div ref={feedback} className="upload-feedback">
          <p role="status" className="caption">Recording {activeIndex + 1} of {recordings.length}: {recordings[activeIndex]?.file.name} · {queue.completed.size} uploaded</p>
          <p role="status" className="upload-stage">
            <strong>{uploadProgress?.confirming ? `Saving part ${Math.min(uploadProgress.completed + 1, uploadProgress.parts)} of ${uploadProgress.parts} on the server…` : progress}</strong>
            {percent !== undefined ? <span>{percent}% sent</span> : null}
          </p>
          <progress className="upload-progress" aria-label={uploadProgress ? "Recording upload progress" : "Preparing audio"}
            value={uploadProgress?.sent} max={uploadProgress?.total ?? 1} />
          <p className="caption">{uploadProgress
            ? `${(uploadProgress.sent / 1_000_000).toFixed(1)} of ${(uploadProgress.total / 1_000_000).toFixed(1)} MB · ${uploadProgress.completed} of ${uploadProgress.parts} parts saved`
            : "Preparing on this device. A time estimate is not available for this step."}</p>
          <p className="caption">{progressTime(elapsed)} elapsed · {estimate !== undefined
            ? `About ${progressTime(estimate)} left to upload`
            : uploadProgress?.confirming ? "Waiting for the server to confirm this part"
              : uploadProgress ? "Estimating upload time…" : "Keep this window open"}</p>
          <p className="caption">Transcription continues on the server after upload. Follow its progress under Capture &amp; sync.</p>
        </div> : null}
        <div className="button-row">
          <button className="button accent" type={handled ? "button" : "submit"}
            onClick={handled ? onClose : undefined}
            disabled={!recordings.length || busy || checking || (!handled && linkExisting && !conversationSourceId)}>
            <Icon name="upload" size={18} />
            {checking ? "Checking duplicates…" : busy ? uploadProgress ? "Uploading…" : "Preparing audio…" : handled ? "Done" : fileErrors.size ? "Retry failed recordings" : remaining > 1 ? `Upload ${remaining} recordings` : "Upload recording"}
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

export function AudioDropArea({ files = [], busy, dragging, onDragging, onChoose }: {
  files?: File[];
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
      <strong>{dragging ? "Drop your recordings here" : "Drag and drop recordings"}</strong>
      <span>or choose audio files from your device</span>
      <label className="button" htmlFor="audio-file">Choose audio files</label>
      <input
        className="sr-only"
        id="audio-file"
        type="file"
        multiple
        accept="audio/*,.wav,.m4a,.mp3,.ogg,.flac,.aac,.opus,.aiff,.caf,.webm"
        aria-describedby="audio-file-hint selected-audio-file"
        onChange={(event) => {
          if (event.target.files?.length) onChoose(event.target.files);
          event.target.value = "";
        }}
        disabled={busy}
      />
      <span id="selected-audio-file" className="selected-audio-file" role="status">
        {files.length ? `${files.length} recording${files.length === 1 ? "" : "s"} selected` : "Select one or more audio files"}
      </span>
    </div>
  );
}
