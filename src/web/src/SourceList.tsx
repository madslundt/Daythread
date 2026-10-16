import { useState } from "react";
import { audioUrl, errorMessage, request } from "./api";
import { dateTimeLabel, timeLabel } from "./format";
import { Icon } from "./Icon";
import type { Source } from "./types";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export function SourceList({
  sources,
  timezone,
  onCorrect,
  onDeleted,
  expanded = false,
  showDate = false,
}: {
  sources: Source[];
  timezone: string;
  onCorrect?: () => void;
  onDeleted?: () => void;
  expanded?: boolean;
  showDate?: boolean;
}) {
  return (
    <div className="source-list">
      {sources.map((source, index) => (
        <SourceRow
          key={source.id}
          source={source}
          timezone={timezone}
          onCorrect={onCorrect}
          onDeleted={onDeleted}
          expanded={expanded}
          showDate={showDate}
          index={index + 1}
        />
      ))}
    </div>
  );
}

function SourceRow({
  source,
  timezone,
  onCorrect,
  onDeleted,
  expanded,
  showDate,
  index,
}: {
  source: Source;
  timezone: string;
  onCorrect?: () => void;
  onDeleted?: () => void;
  expanded: boolean;
  showDate: boolean;
  index: number;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(source.text || "");
  const [saved, setSaved] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [audioError, setAudioError] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const text = saved ?? source.text;
  async function save() {
    setBusy(true);
    setError("");
    try {
      await request(`/v1/transcripts/${encodeURIComponent(source.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ text: draft }),
      });
      setSaved(draft);
      setEditing(false);
      onCorrect?.();
      window.dispatchEvent(new Event("omi:archive-changed"));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (
      !window.confirm(
        "Delete this recording and transcript? Derived diary entries, weekly reviews and chat history will also be cleared because they may contain this recording. This cannot be undone.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await request(`/v1/audio/${encodeURIComponent(source.id)}`, {
        method: "DELETE",
      });
      setDeleted(true);
      (onDeleted || onCorrect)?.();
      window.dispatchEvent(new Event("omi:archive-changed"));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  if (deleted)
    return (
      <p className="caption" role="status">
        Recording deleted. Derived diary entries, weekly reviews and chat history were cleared.
      </p>
    );
  return (
    <details
      className="source-row"
      open={expanded || undefined}
      id={`source-${source.id}`}
    >
      <summary>
        <span className="source-time">
          {showDate
            ? dateTimeLabel(source.started_at, timezone)
            : timeLabel(source.started_at, timezone)}
        </span>
        <span className="source-preview">
          {text ||
            (source.state === "done"
              ? "No speech detected"
              : `Recording · ${source.state}`)}
        </span>
        <span className="source-hints">
          {source.important ? <Icon name="star" size={15} /> : null}
          {source.meeting_hint_ids?.length ? (
            <Icon name="mic" size={15} />
          ) : null}
        </span>
        <Icon name="chevron-right" size={15} />
      </summary>
      <div className="source-detail">
        <div className="source-meta">
          <span>
            Source {index} · {timeLabel(source.started_at, timezone)}–
            {timeLabel(source.ended_at, timezone)} · {source.device_id}
          </span>
          <span>{source.state === "done" ? "Transcribed" : source.state}</span>
        </div>
        {editing ? (
          <div className="transcript-editor">
            <label htmlFor={`edit-${source.id}`}>Correct transcript</label>
            <textarea
              id={`edit-${source.id}`}
              rows={5}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={20000}
            />
            <div className="button-row">
              <button
                className="button dark small"
                onClick={save}
                disabled={busy}
              >
                {busy ? "Saving…" : "Save correction"}
              </button>
              <button
                className="button small"
                onClick={() => setEditing(false)}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
            <p className="caption">
              Corrections update search and invalidate saved recaps and weekly reviews so they can
              be regenerated.
            </p>
          </div>
        ) : (
          <>
            <p className="transcript-text">
              {text ||
                (source.state === "done"
                  ? "No speech was detected in this recording."
                  : "This recording is waiting for transcription.")}
            </p>
            <button
              className="text-button"
              onClick={() => {
                setDraft(text || "");
                setEditing(true);
              }}
            >
              Correct transcript
            </button>
          </>
        )}
        {error ? <ErrorState message={error} /> : null}
        {source.audio_available === false ? (
          <p className="caption">
            {source.source_kind === "omi_api"
              ? "Imported from Omi. This import includes the transcript only; original audio is unavailable."
              : "Audio was removed by your retention policy. The transcript remains available."}
          </p>
        ) : (
          <>
            <audio
              controls
              preload="none"
              src={audioUrl(source.id)}
              onError={() => setAudioError(true)}
              aria-label={`Play recording from ${timeLabel(source.started_at, timezone)}`}
            />
            {audioError ? (
              <p className="caption">
                This audio is unavailable. The original may have expired under
                your retention policy.
              </p>
            ) : null}
            <a
              className="text-link caption"
              href={audioUrl(source.id)}
              download
            >
              Download original audio
            </a>
          </>
        )}
        <div className="source-tools">
          <button
            className="text-button"
            onClick={() => setShowHistory((value) => !value)}
          >
            {showHistory ? "Hide correction history" : "Correction history"}
          </button>
          <button
            className="text-button danger"
            onClick={remove}
            disabled={busy}
          >
            Delete recording
          </button>
        </div>
        {showHistory ? (
          <CorrectionHistory id={source.id} timezone={timezone} />
        ) : null}
      </div>
    </details>
  );
}

function CorrectionHistory({ id, timezone }: { id: string; timezone: string }) {
  const resource = useResource<{
    items: {
      id: string;
      text: string;
      previous_text: string | null;
      created_at: string;
    }[];
  }>(`/v1/transcripts/${encodeURIComponent(id)}/revisions`);
  return (
    <div className="correction-history">
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : resource.loading ? (
        <p className="caption">Loading corrections…</p>
      ) : resource.data?.items.length ? (
        resource.data.items.map((revision) => (
          <details key={revision.id}>
            <summary>{dateTimeLabel(revision.created_at, timezone)}</summary>
            <p className="caption">Previous transcript</p>
            <p className="transcript-text">
              {revision.previous_text || "No transcript"}
            </p>
            <p className="caption">Corrected transcript</p>
            <p className="transcript-text">{revision.text || "No speech"}</p>
          </details>
        ))
      ) : (
        <p className="caption">No corrections have been made.</p>
      )}
    </div>
  );
}
