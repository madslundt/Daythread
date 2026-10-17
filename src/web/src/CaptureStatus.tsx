import type { Status } from "./types";
import { ErrorState } from "./UI";
import { Icon } from "./Icon";

export function CaptureStatus({
  status,
  error,
  compact = false,
}: {
  status?: Status;
  error?: string;
  compact?: boolean;
}) {
  const when = (at?: string | null) =>
    at ? new Date(at).toLocaleString() : "unknown";
  const processing = status?.processing;
  const stale =
    !!error ||
    (!!status?.as_of && Date.now() - Date.parse(status.as_of) > 120_000);
  if (compact) {
    const phone = !stale && status?.phones?.find((item) => item.fresh);
    return (
      <details className="capture-overview">
        <summary>
          <span className="capture-overview-title">Capture & sync<Icon name="chevron-right" size={16} /></span>
          <span className="capture-glance"><span className={`status-dot ${status && !stale ? "online" : ""}`} />{stale ? "Status needs a refresh" : status ? "Backend connected" : "Connecting to backend…"}</span>
          <span className="caption">{phone ? `Phone: ${phone.capture_state.replaceAll("_", " ")}` : "Live capture unknown"}</span>
          {processing && (processing.queued > 0 || processing.transcribing > 0) ? <span className="caption">{processing.queued} queued · {processing.transcribing} processing</span> : null}
          {processing && !stale && status?.worker_running ? <ProcessingProgress processing={processing} compact /> : null}
          {processing?.blocked ? <span className="status-warning caption">{processing.blocked} blocked · needs attention</span> : null}
          {status && !status.worker_running ? <span className="status-warning caption">Transcription {status.worker_enabled ? "worker is not running" : "is disabled"}.</span> : null}
        </summary>
        <CaptureStatus status={status} error={error} />
      </details>
    );
  }
  return (
    <section className="capture-status">
      <h2>Capture status</h2>
      {error ? <ErrorState message={error} /> : null}
      {!status ? (
        <p className="caption">No current backend observation.</p>
      ) : (
        <>
          <div className={`capture-summary ${stale ? "is-stale" : ""}`}>
            <span className={`status-dot ${!stale ? "online" : ""}`} />
            <span>{stale ? "Status needs a refresh" : "Backend connected"}</span>
          </div>
          <p className="caption">{status.ingestion?.last_received_at
            ? `Last recording received ${when(status.ingestion.last_received_at)}`
            : "Ready for your first recording."}</p>
          {processing ? <dl className="processing-stats">
            <div><dt>Queued</dt><dd>{processing.queued}</dd></div>
            <div><dt>Processing</dt><dd>{processing.transcribing}</dd></div>
            <div><dt>Blocked</dt><dd>{processing.blocked}</dd></div>
          </dl> : null}
          {processing && !stale && status.worker_running ? <ProcessingProgress processing={processing} /> : null}
          <p className="caption">{status.phones?.some((phone) => phone.fresh) && !stale
            ? "Phone reports are available in the details below."
            : "No current phone report. Live capture is unknown."}</p>
          {!status.worker_running ? <p className="caption status-warning">Transcription {status.worker_enabled ? "worker is not running" : "is disabled"}.</p> : null}
          <details className="capture-details">
          <summary>Capture & sync details</summary>
          <p className="caption">
            Backend observation {when(status.as_of)}
            {stale ? " · stale; current state unknown" : ""}
          </p>
          <p>
            Last recording received: {when(status.ingestion?.last_received_at)}
          </p>
          <p className="caption">
            Latest captured endpoint: {when(status.ingestion?.last_captured_at)}
            . Backlog uploads can arrive later; this does not establish
            continuous capture.
          </p>
          <p className="caption">
            Shared pause requested: {status.capture_paused ? "yes" : "no"}.
            Observed phone state is separate.
          </p>
          {processing ? (
            <>
              <p>
                {processing.queued} queued ({processing.retrying} retrying) ·{" "}
                {processing.transcribing} transcribing · {processing.blocked}{" "}
                blocked
              </p>
              <p className="caption">
                Oldest known pending receipt:{" "}
                {when(processing.oldest_received_at)}
                {processing.oldest_age_seconds != null
                  ? ` · waiting ${Math.round(processing.oldest_age_seconds / 60)} min`
                  : ""}
              </p>
              {processing.recent_errors.map((e) => (
                <p className="caption" key={e.id}>
                  {e.id}: {e.error_code} · {e.attempts} attempts
                </p>
              ))}
            </>
          ) : null}
          {!status.worker_running ? (
            <p className="caption">
              Transcription worker{" "}
              {status.worker_enabled ? "not running" : "disabled"}.
            </p>
          ) : null}
          {status.phones?.length ? (
            status.phones.map((phone) => (
              <div key={phone.phone_id}>
                <p>
                  Phone{" "}
                  {phone.fresh && !stale
                    ? phone.capture_state
                    : "state unknown"}
                </p>
                <p className="caption">
                  Reported {when(phone.observed_at)}: {phone.capture_state} (
                  {phone.cause}). Last decoded audio{" "}
                  {when(phone.last_decoded_at)}.
                </p>
                <p className="caption">
                  {phone.pending_recordings} recordings waiting ·{" "}
                  {phone.recovery_items} unfinished/recovery ·{" "}
                  {Math.round(phone.pending_seconds / 60)} min ·{" "}
                  {phone.upload_policy.replaceAll("_", " ")} ·{" "}
                  {phone.upload_state}
                </p>
              </div>
            ))
          ) : (
            <p className="caption">
              No phone report received; phone connectivity is unknown.
            </p>
          )}
          <p className="caption">
            {status.capture_gaps ?? 0} stored capture interruptions. A saved
            archive does not prove live capture.
          </p>
          </details>
        </>
      )}
    </section>
  );
}

function remainingTime(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${hours} hr${rest ? ` ${rest} min` : ""}`;
}

function ProcessingProgress({ processing, compact = false }: {
  processing: NonNullable<Status["processing"]>;
  compact?: boolean;
}) {
  if (!processing.queued && !processing.transcribing) return null;
  const active = processing.active || [];
  const labels: Record<string, string> = {
    analyzing: "Analyzing audio", transcribing: "Transcribing",
    enhancing: "Checking enhanced audio", saving: "Saving transcript",
  };
  const estimate = processing.estimated_remaining_seconds;
  const time = estimate != null && estimate > 0
    ? `Approx. ${remainingTime(estimate)} remaining for the processing queue`
    : processing.retrying || processing.blocked
      ? "Time remaining unavailable while recordings need retry or repair"
      : (processing.estimate_sample_count || 0) === 0
        ? "Estimating time remaining after the first transcription"
        : "Time remaining unavailable";
  if (compact) {
    const item = active[0];
    return <span className="caption">{item ? `${labels[item.stage] || "Processing audio"}${item.total_chunks ? ` · ${item.completed_chunks} of ${item.total_chunks} chunks complete` : ""}. ` : ""}{time}</span>;
  }
  return <div className="processing-progress" role="status" aria-live="polite">
    {active.map((item) => <div key={item.id}>
      <p className="caption">
        {labels[item.stage] || "Processing audio"}
        {item.total_chunks ? ` · ${item.completed_chunks} of ${item.total_chunks} chunks complete` : ""}
        {item.elapsed_seconds != null ? ` · ${Math.floor(item.elapsed_seconds)} sec elapsed` : ""}
      </p>
      {item.total_chunks > 0 ? <progress
        aria-label={`${labels[item.stage] || "Processing audio"} chunks complete`}
        value={item.completed_chunks} max={item.total_chunks}
      /> : null}
    </div>)}
    <p className="caption">{time}. {estimate != null && estimate > 0 ? "Based on recent transcription speed; this may change." : ""}</p>
  </div>;
}
