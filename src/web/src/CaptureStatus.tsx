import type { Status } from "./types";
import { ErrorState } from "./UI";

export function CaptureStatus({
  status,
  error,
}: {
  status?: Status;
  error?: string;
}) {
  const when = (at?: string | null) =>
    at ? new Date(at).toLocaleString() : "unknown";
  const processing = status?.processing;
  const stale =
    !!error ||
    (!!status?.as_of && Date.now() - Date.parse(status.as_of) > 120_000);
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
