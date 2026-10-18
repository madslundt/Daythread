import { isVendorTranscript, sourceVendor, sourceVendorLabel } from "./vendors";
import { ConversationTags } from "./ConversationTags";
import { RecordingLocation } from "./RecordingLocation";
import { statusLabel } from "./statusLabels";
import { t } from "./i18n";
import { ImportanceButton } from "./Importance";
import { SourceLabels } from "./SourceLabels";
import { useEffect, useState, type ReactNode } from "react";
import { audioUrl, errorMessage, request } from "./api";
import { dateTimeLabel, timeLabel } from "./format";
import { FeedbackForm } from "./FeedbackForm";
import { DecisionEditor } from "./DecisionEditor";
import { TranscriptionReview } from "./TranscriptionReview";
import { TranscriptTranslation } from "./TranscriptTranslation";
import { Speakers } from "./Speakers";
import { RecordingSections } from "./RecordingSections";
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
  compact = false,
  showDate = false,
  idPrefix = "source",
}: {
  sources: Source[];
  timezone: string;
  onCorrect?: () => void;
  onDeleted?: () => void;
  expanded?: boolean;
  compact?: boolean;
  showDate?: boolean;
  idPrefix?: string;
}) {
  return (
    <div className={`source-list${compact ? " source-list-compact" : ""}`}>
      {sources.map((source, index) => (
        <SourceRow
          key={source.id}
          source={source}
          idPrefix={idPrefix}
          timezone={timezone}
          onCorrect={onCorrect}
          onDeleted={onDeleted}
          expanded={expanded}
          compact={compact}
          showDate={showDate}
          index={index + 1}
        />
      ))}
    </div>
  );
}

function SourceRow({
  source,
  idPrefix,
  timezone,
  onCorrect,
  onDeleted,
  expanded,
  compact,
  showDate,
  index,
}: {
  source: Source;
  idPrefix: string;
  timezone: string;
  onCorrect?: () => void;
  onDeleted?: () => void;
  expanded: boolean;
  compact: boolean;
  showDate: boolean;
  index: number;
}) {
  const [creatingDecision, setCreatingDecision] = useState(false);
  const [open, setOpen] = useState(expanded);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(source.text || "");
  const [saved, setSaved] = useState<string>();
  const [currentSource, setCurrentSource] = useState(source);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [audioError, setAudioError] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(!source.sections?.length || !!source.matches?.length);
  const [toolsOpen, setToolsOpen] = useState(false);
  useEffect(() => {
    setSaved(undefined);
  }, [source.id, source.source_version]);
  const text = saved ?? source.text;
  useEffect(() => { setCurrentSource(source); setSaved(undefined); }, [source]);
  useEffect(() => {
    const closeDecision = () => setCreatingDecision(false);
    window.addEventListener("omi:archive-changed", closeDecision);
    return () => window.removeEventListener("omi:archive-changed", closeDecision);
  }, []);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const corrected = await request<Source>(`/v1/transcripts/${encodeURIComponent(source.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ text: draft }),
      });
      setSaved(draft);
      setCurrentSource(corrected);
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
        t("Delete this recording and transcript? Derived diary entries, weekly reviews and chat history will also be cleared. Related decisions and inbox items, including manual edits and review history, will be removed because they used this recording. This cannot be undone."),
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
  const hasTopics = Boolean(currentSource.sections?.length && text);
  const metadata = <>
    <div className="source-meta">
      <span>
        {t("Source")}{" "}{index} · {timeLabel(source.started_at, timezone)}–
        {timeLabel(source.ended_at, timezone)} · {source.device_id}
      </span>
      <span>{source.state === "done" ? t("Transcribed") : statusLabel(source.state)}</span>
    </div>
    {source.source_kind === "manual_upload" ? <p className="caption">
      {sourceVendor(source) && sourceVendor(source) !== "manual"
        ? t("Uploaded recording from {vendor}", { vendor: sourceVendorLabel(source) })
        : t("Manually uploaded recording")}{source.source_id ? ` · ${source.source_id}` : ""}{t(". Capture time was supplied when importing. Archived audio is converted to mono PCM16 at 16000 Hz.")}{" "}</p> : null}
    {isVendorTranscript(source) ? <p className="caption">
      {t("Imported transcript from {vendor}", { vendor: sourceVendorLabel(source) })}{source.source_id ? ` · ${source.source_id}` : ""}
    </p> : null}
    {source.source_kind === "omi_audio_webhook" ? <p className="caption">
      {t("Forwarded from the official Omi integration · account identity supplied by the sender, pendant identity unknown. Times estimate a chunk ending at server receipt, not actual capture time. Each POST is separate; overlap, gaps and delivery order are uncertain. Input")}{" "}{source.source_metadata?.input_sample_rate || "unknown"} {t("Hz, archived as mono PCM16 at 16000 Hz.")}{" "}</p> : null}
  </>;
  const playback = source.audio_available === false ||
        source.audio_availability === "missing" ? (
          <p className="caption">
            {source.audio_availability === "missing"
              ? t("The archive expects audio, but the file is missing.")
              : isVendorTranscript(source)
                ? t("Imported from {vendor}. This import includes the transcript only; original audio is unavailable.", { vendor: sourceVendorLabel(source) })
                : t("Audio was removed by your retention policy. The transcript remains available.")}
          </p>
        ) : (
          <>
            <audio
              controls
              preload="none"
              src={audioUrl(source.id)}
              onError={() => setAudioError(true)}
              aria-label={t("Play recording from {value0}", { value0: timeLabel(source.started_at, timezone) })}
            />
            {audioError ? (
              <p className="caption">
                {t("This audio is unavailable. The original may have expired under your retention policy.")}{" "}</p>
            ) : null}
            {!compact ? <a
              className="text-link caption"
              href={audioUrl(source.id)}
              download
            >
              {source.source_kind === "omi_audio_webhook" || source.source_kind === "manual_upload" ? t("Download archived audio") : t("Download original audio")}
            </a> : null}
          </>
        );
  if (deleted)
    return (
      <p className="caption" role="status">
        {t("Recording deleted. Derived diary/chat content, weekly reviews and related decisions and inbox items were removed.")}{" "}</p>
    );
  return (
    <details
      className={`source-row${compact ? " source-row-compact" : ""}`}
      open={expanded || undefined}
      id={`${idPrefix}-${source.id}`}
      onToggle={(event) => {
        if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
      }}
    >
      <summary>
        <span className="source-time">
          {showDate
            ? dateTimeLabel(source.started_at, timezone)
            : timeLabel(source.started_at, timezone)}
        </span>
        <span className="source-overview">
          <span className="source-preview">
            {currentSource.title || (saved === undefined ? source.matches?.[0]?.text : undefined) || text ||
              (source.state === "done"
                ? t("No transcript text")
                : t("Recording · {value0}", { value0: statusLabel(source.state) }))}
          </span>
          {compact ? <span className="source-glance">
            {recordingDuration(source)}
            {source.alternate_sources?.length ? t(" · {value0} uploads", { value0: source.alternate_sources.length + 1 }) : ""}
            {hasTopics ? ` · ${currentSource.sections!.length} ${currentSource.sections!.length === 1 ? t("topic") : t("topics")}` : ""}
            {source.state !== "done" ? ` · ${statusLabel(source.state)}` : ""}
          </span> : null}
          {compact && source.matches?.length && currentSource.title ? <span className="source-match">{source.matches[0].text}</span> : null}
        </span>
        <span className="source-hints">
          <ImportanceButton target={{ kind: "source", id: source.id }} label={t("recording")} compact />
          {source.important ? <span title={t("Importance hint added during recording")}><Icon name="star" size={15} /></span> : null}
          {source.meeting_hint_ids?.length ? (
            <Icon name="mic" size={15} />
          ) : null}
        </span>
        <Icon name="chevron-right" size={15} />
      </summary>
      {!compact ? <FeedbackForm sourceId={source.id} /> : null}
      {source.error_code ||
      source.transcription_outcome === "silence_gate" ||
      source.transcription_outcome === "empty_stt" ? (
        <p className="caption">
          {t("Processing:")}{" "}{statusLabel(source.error_code) || statusLabel(source.transcription_outcome)}
        </p>
      ) : null}
      {!compact && source.audio_availability === "missing" ? (
        <p className="caption">
          {t("The archive expects audio but the file is missing.")}{" "}</p>
      ) : null}
      {source.state === "blocked" || source.error_code || (
        source.state === "done" && !text &&
        ["silence_gate", "empty_stt"].includes(source.transcription_outcome || "") &&
        source.audio_available !== false
      ) ? (
        <button
          className="text-button"
          onClick={async () => {
            try {
              await request(`/v1/audio/${source.id}/retry`, { method: "POST" });
              onCorrect?.();
            } catch (error) {
              setError(errorMessage(error));
            }
          }}
        >
          {source.state === "done"
            ? t("Retry empty transcription with current settings")
            : t("Retry transcription after resolving the problem")}
        </button>
      ) : null}
      <div className="source-detail">
        <ConversationTags sourceID={source.id} tags={currentSource.tags} onChanged={onCorrect} />
        <RecordingLocation source={currentSource} timezone={timezone} onChanged={onCorrect} />
        {source.alternate_sources?.length ? <details className="recording-alternatives">
          <summary>{t("Other uploads of this recording (")}{source.alternate_sources.length})</summary>
          <p className="caption">{t("Identical audio is shown once in the timeline. Each upload keeps its supplied time and transcript.")}</p>
          <SourceList sources={source.alternate_sources} timezone={timezone} showDate
            idPrefix={`${idPrefix}-alternate`} onCorrect={onCorrect} onDeleted={onDeleted} />
        </details> : null}
        {compact ? playback : null}
        {compact && !editing ? <RecordingSections source={currentSource} compact /> : null}
        {!compact ? metadata : null}
        {source.matches?.length ? <p className="caption">{t("Search excerpt · full transcript below. Timing covers the recording interval.")}</p> : null}
        <RecordingDisclosure enabled={compact} label={t("Full transcript")} className="recording-transcript" open={transcriptOpen} onToggle={setTranscriptOpen}>
        {editing ? (
          <div className="transcript-editor">
            <label htmlFor={`edit-${idPrefix}-${source.id}`}>{t("Correct transcript")}</label>
            <textarea
              id={`edit-${idPrefix}-${source.id}`}
              rows={5}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={200000}
            />
            <div className="button-row">
              <button
                className="button dark small"
                onClick={save}
                disabled={busy}
              >
                {busy ? t("Saving…") : t("Save correction")}
              </button>
              <button
                className="button small"
                onClick={() => setEditing(false)}
                disabled={busy}
              >
                {t("Cancel")}{" "}</button>
            </div>
            <p className="caption">
              {t("Corrections update search, invalidate saved recaps and weekly reviews, and require dependent decision evidence to be reviewed.")}{" "}</p>
          </div>
        ) : (
          <>
            {!compact ? <RecordingSections source={currentSource} /> : null}
            {open && (!compact || transcriptOpen) && text ? <TranscriptTranslation
              key={`${source.id}:${currentSource.transcript_version || currentSource.source_version || ""}:${text}`}
              sourceId={source.id} original={text}
            /> : <p className="transcript-text">
              {text ||
                (source.state === "done"
                  ? t("Processing completed without transcript text; this does not prove no speech occurred.")
                  : source.state === "expired"
                    ? t("Transcript unavailable after retention.")
                    : source.state === "blocked"
                      ? t("Transcription blocked. Resolve the problem and retry.")
                      : t("This recording is waiting for transcription."))}
            </p>}
            <button
              className="text-button"
              onClick={() => {
                setDraft(text || "");
                setEditing(true);
              }}
            >
              {t("Correct transcript")}{" "}</button>
          </>
        )}
        </RecordingDisclosure>
        <TranscriptionReview details={currentSource.transcription_details} onReview={() => {
          setDraft(text || "");
          setTranscriptOpen(true);
          setEditing(true);
        }} />
        {error ? <ErrorState message={error} /> : null}
        {!compact ? playback : null}
        <div className={compact ? "recording-footer" : undefined}>
        <RecordingDisclosure enabled={compact} label={t("Recording details & tools")} className="recording-utilities" open={toolsOpen} onToggle={setToolsOpen}>
        {compact ? <>
          {metadata}
          {source.audio_available !== false && source.audio_availability !== "missing" ? <a
              className="text-link caption"
              href={audioUrl(source.id)}
              download
            >
              {source.source_kind === "omi_audio_webhook" || source.source_kind === "manual_upload" ? t("Download archived audio") : t("Download original audio")}
            </a> : null}
          <FeedbackForm sourceId={source.id} />
        </> : null}
        {open && (!compact || toolsOpen) ? <Speakers kind="source" id={source.id} /> : null}
        {text && currentSource.source_version ? <button className="button small" onClick={() => setCreatingDecision(true)}>{t("Record a decision from this source")}</button> : null}
        {creatingDecision ? <DecisionEditor source={currentSource} onClose={() => setCreatingDecision(false)} onSaved={() => {}} /> : null}
        <SourceLabels sourceId={source.id} />
        <div className="source-tools">
          <button
            className="text-button"
            onClick={() => setShowHistory((value) => !value)}
          >
            {showHistory ? t("Hide correction history") : t("Correction history")}
          </button>
          <button
            className="text-button danger"
            onClick={remove}
            disabled={busy}
          >
            {t("Delete recording")}{" "}</button>
        </div>
        {showHistory ? (
          <CorrectionHistory id={source.id} timezone={timezone} />
        ) : null}
        </RecordingDisclosure>
        </div>
      </div>
    </details>
  );
}

function recordingDuration(source: Source): string {
  const seconds = Math.round((Date.parse(source.ended_at) - Date.parse(source.started_at)) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return t("Duration unavailable");
  if (seconds < 60) return t("{value0} sec", { value0: seconds });
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? t("{value0} min", { value0: minutes }) : t("{value0} hr{value1}", { value0: Math.floor(minutes / 60), value1: minutes % 60 ? t(" {value0} min", { value0: minutes % 60 }) : "" });
}

function RecordingDisclosure({ enabled, label, className, open, onToggle, children }: {
  enabled: boolean; label: string; className: string; open: boolean;
  onToggle: (open: boolean) => void; children: ReactNode;
}) {
  if (!enabled) return <>{children}</>;
  return <details className={`recording-disclosure ${className}`} open={open} onToggle={(event) => {
    if (event.target === event.currentTarget) onToggle(event.currentTarget.open);
  }}>
    <summary><span>{label}</span><Icon name="chevron-right" size={14} /></summary>
    <div className="recording-disclosure-content">{children}</div>
  </details>;
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
        <p className="caption">{t("Loading corrections…")}</p>
      ) : resource.data?.items.length ? (
        resource.data.items.map((revision) => (
          <details key={revision.id}>
            <summary>{dateTimeLabel(revision.created_at, timezone)}</summary>
            <p className="caption">{t("Previous transcript")}</p>
            <p className="transcript-text">
              {revision.previous_text || t("No transcript")}
            </p>
            <p className="caption">{t("Corrected transcript")}</p>
            <p className="transcript-text">
              {revision.text || t("No transcript text")}
            </p>
          </details>
        ))
      ) : (
        <p className="caption">{t("No corrections have been made.")}</p>
      )}
    </div>
  );
}
