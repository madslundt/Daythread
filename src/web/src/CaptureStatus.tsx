import { statusLabel } from "./statusLabels";
import { displayLocale, t } from "./i18n";
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
    at ? new Date(at).toLocaleString(displayLocale()) : t("Unknown");
  const processing = status?.processing;
  const stale =
    !!error ||
    (!!status?.as_of && Date.now() - Date.parse(status.as_of) > 120_000);
  if (compact) {
    const phone = !stale && status?.phones?.find((item) => item.fresh);
    return (
      <details className="capture-overview">
        <summary>
          <span className="capture-overview-title">{t("Capture & sync")}<Icon name="chevron-right" size={16} /></span>
          <span className="capture-glance"><span className={`status-dot ${status && !stale ? "online" : ""}`} />{stale ? t("Status needs a refresh") : status ? t("Backend connected") : t("Connecting to backend…")}</span>
          <span className="caption">{phone ? t("Phone: {value0}", { value0: statusLabel(phone.capture_state) }) : t("Live capture unknown")}</span>
          {processing && (processing.queued > 0 || processing.transcribing > 0) ? <span className="caption">{processing.queued} {t("queued ·")}{" "}{processing.transcribing} {t("processing")}</span> : null}
          {processing && !stale && status?.worker_running ? <ProcessingProgress processing={processing} compact /> : null}
          {processing?.blocked ? <span className="status-warning caption">{processing.blocked} {t("blocked · needs attention")}</span> : null}
          {status && !status.worker_running ? <span className="status-warning caption">{t("Transcription")}{" "}{status.worker_enabled ? t("worker is not running") : t("is disabled")}.</span> : null}
        </summary>
        <CaptureStatus status={status} error={error} />
      </details>
    );
  }
  return (
    <section className="capture-status">
      <h2>{t("Capture status")}</h2>
      {error ? <ErrorState message={error} /> : null}
      {!status ? (
        <p className="caption">{t("No current backend observation.")}</p>
      ) : (
        <>
          <div className={`capture-summary ${stale ? "is-stale" : ""}`}>
            <span className={`status-dot ${!stale ? "online" : ""}`} />
            <span>{stale ? t("Status needs a refresh") : t("Backend connected")}</span>
          </div>
          <p className="caption">{status.ingestion?.last_received_at
            ? t("Last recording received {value0}", { value0: when(status.ingestion.last_received_at) })
            : t("Ready for your first recording.")}</p>
          {processing ? <dl className="processing-stats">
            <div><dt>{t("Queued")}</dt><dd>{processing.queued}</dd></div>
            <div><dt>{t("Processing")}</dt><dd>{processing.transcribing}</dd></div>
            <div><dt>{t("Blocked")}</dt><dd>{processing.blocked}</dd></div>
          </dl> : null}
          {processing && !stale && status.worker_running ? <ProcessingProgress processing={processing} /> : null}
          <p className="caption">{status.phones?.some((phone) => phone.fresh) && !stale
            ? t("Phone reports are available in the details below.")
            : t("No current phone report. Live capture is unknown.")}</p>
          {!status.worker_running ? <p className="caption status-warning">{t("Transcription")}{" "}{status.worker_enabled ? t("worker is not running") : t("is disabled")}.</p> : null}
          <details className="capture-details">
          <summary>{t("Capture & sync details")}</summary>
          <p className="caption">
            {t("Backend observation")}{" "}{when(status.as_of)}
            {stale ? t("· stale; current state unknown") : ""}
          </p>
          <p>
            {t("Last recording received:")}{" "}{when(status.ingestion?.last_received_at)}
          </p>
          <p className="caption">
            {t("Latest captured endpoint:")}{" "}{when(status.ingestion?.last_captured_at)}
            {t(". Backlog uploads can arrive later; this does not establish continuous capture.")}{" "}</p>
          {processing ? (
            <>
              <p>
                {processing.queued} {t("queued (")}{processing.retrying} {t("retrying) ·")}{" "}
                {processing.transcribing} {t("transcribing ·")}{" "}{processing.blocked}{" "}
                {t("blocked")}{" "}</p>
              <p className="caption">
                {t("Oldest known pending receipt:")}{" "}
                {when(processing.oldest_received_at)}
                {processing.oldest_age_seconds != null
                  ? t(" · waiting {value0} min", { value0: Math.round(processing.oldest_age_seconds / 60) })
                  : ""}
              </p>
              {processing.recent_errors.map((e) => (
                <p className="caption" key={e.id}>
                  {e.id}: {statusLabel(e.error_code)} · {e.attempts} {t("attempts")}{" "}</p>
              ))}
            </>
          ) : null}
          {!status.worker_running ? (
            <p className="caption">
              {t("Transcription worker")}{" "}
              {status.worker_enabled ? t("not running") : t("disabled")}.
            </p>
          ) : null}
          {status.phones?.length ? (
            status.phones.map((phone) => (
              <div key={phone.phone_id}>
                <p>
                  {t("Phone")}{" "}
                  {phone.fresh && !stale
                    ? statusLabel(phone.capture_state)
                    : t("state unknown")}
                </p>
                <p className="caption">
                  {t("Reported")}{" "}{when(phone.observed_at)}: {statusLabel(phone.capture_state)} (
                  {statusLabel(phone.cause)}{t("). Last decoded audio")}{" "}
                  {when(phone.last_decoded_at)}.
                </p>
                <p className="caption">
                  {phone.pending_recordings} {t("recordings waiting ·")}{" "}
                  {phone.recovery_items} {t("unfinished/recovery ·")}{" "}
                  {Math.round(phone.pending_seconds / 60)} {t("min ·")}{" "}
                  {statusLabel(phone.upload_policy)} ·{" "}
                  {statusLabel(phone.upload_state)}
                </p>
              </div>
            ))
          ) : (
            <p className="caption">
              {t("No phone report received; phone connectivity is unknown.")}{" "}</p>
          )}
          <p className="caption">
            {status.capture_gaps ?? 0} {t("stored capture interruptions. A saved archive does not prove live capture.")}{" "}</p>
          </details>
        </>
      )}
    </section>
  );
}

function remainingTime(seconds: number): string {
  if (seconds < 60) return t("less than a minute");
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return t("{value0} min", { value0: minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return t("{value0} hr{value1}", { value0: hours, value1: rest ? t(" {value0} min", { value0: rest }) : "" });
}

function ProcessingProgress({ processing, compact = false }: {
  processing: NonNullable<Status["processing"]>;
  compact?: boolean;
}) {
  if (!processing.queued && !processing.transcribing) return null;
  const active = processing.active || [];
  const labels: Record<string, string> = {
    analyzing: t("Analyzing audio"), transcribing: t("Transcribing"),
    enhancing: t("Checking enhanced audio"), saving: t("Saving transcript"),
  };
  const estimate = processing.estimated_remaining_seconds;
  const time = estimate != null && estimate > 0
    ? t("Approx. {value0} remaining for the processing queue", { value0: remainingTime(estimate) })
    : processing.retrying || processing.blocked
      ? t("Time remaining unavailable while recordings need retry or repair")
      : (processing.estimate_sample_count || 0) === 0
        ? t("Estimating time remaining after the first transcription")
        : t("Time remaining unavailable");
  if (compact) {
    const item = active[0];
    return <span className="caption">{item ? `${labels[item.stage] || t("Processing audio")}${item.total_chunks ? t(" · {value0} of {value1} chunks complete", { value0: item.completed_chunks, value1: item.total_chunks }) : ""}. ` : ""}{time}</span>;
  }
  return <div className="processing-progress" role="status" aria-live="polite">
    {active.map((item) => <div key={item.id}>
      <p className="caption">
        {labels[item.stage] || t("Processing audio")}
        {item.total_chunks ? t(" · {value0} of {value1} chunks complete", { value0: item.completed_chunks, value1: item.total_chunks }) : ""}
        {item.elapsed_seconds != null ? t(" · {value0} sec elapsed", { value0: Math.floor(item.elapsed_seconds) }) : ""}
      </p>
      {item.total_chunks > 0 ? <progress
        aria-label={t("{value0} chunks complete", { value0: labels[item.stage] || t("Processing audio") })}
        value={item.completed_chunks} max={item.total_chunks}
      /> : null}
    </div>)}
    <p className="caption">{time}. {estimate != null && estimate > 0 ? t("Based on recent transcription speed; this may change.") : ""}</p>
  </div>;
}

export function RecordingActivity({ status, error }: { status?: Status; error?: string }) {
  const stale = Boolean(error || !status?.as_of || !Number.isFinite(Date.parse(status.as_of)) ||
    Date.now() - Date.parse(status.as_of) > 120_000);
  const phones = stale ? [] : (status?.phones || []).filter((phone) => phone.fresh &&
    Date.now() - Date.parse(phone.observed_at) <= 120_000);
  const recording = phones.filter((phone) => phone.capture_state === "capturing" &&
    phone.last_decoded_at && Date.parse(phone.observed_at) - Date.parse(phone.last_decoded_at) >= 0 &&
    Date.parse(phone.observed_at) - Date.parse(phone.last_decoded_at) <= 5_000);
  const waiting = phones.reduce((count, phone) => count + phone.pending_recordings, 0);
  const processing = status?.processing;
  const generation = status?.generation;
  const labels: Record<string, string> = {
    summarizing: t("Summarizing"), organizing: t("Organizing recording topics"), translating: t("Translating"),
  };
  return <section className="recording-activity" aria-label={t("Recording activity")}>
    <div role="status" aria-live="polite">
      <h2>{t("Recording activity")}</h2>
      {recording.length ? recording.map((phone) => <p className="recording-live" key={phone.phone_id}>
        <span className="status-dot online" aria-hidden="true" />
        {phone.capture_source === "omi" ? t("Recording from Omi") : phone.capture_source === "phone" ? t("Recording from phone microphone") : t("Recording in progress")}
      </p>) : <p className="caption">{stale && status ? t("Status needs a refresh") : phones.some((phone) => phone.capture_state === "paused") ? t("Recording stopped on iPhone") : t("Live capture unknown")}</p>}
      {processing ? <p className="activity-counts">{t("Recordings: {queued} queued · {processing} processing", { queued: processing.queued, processing: processing.transcribing })}</p> : null}
      {waiting > 0 ? <p className="caption">{t("{count} recordings waiting to upload", { count: waiting })}</p> : null}
      {!stale && processing && status?.worker_running ? <ProcessingProgress processing={processing} /> : null}
      {generation && (generation.queued > 0 || generation.running > 0) ? <>
        <p className="activity-counts">{t("AI tasks: {queued} queued · {processing} processing", { queued: generation.queued, processing: generation.running })}</p>
        {!stale ? Object.entries(generation.stages).filter(([, count]) => count > 0).map(([stage, count]) =>
          <p className="caption" key={stage}>{labels[stage] || t("Processing")} · {count}</p>) : null}
      </> : null}
      {status && !status.worker_running && ((processing?.queued || 0) + (processing?.transcribing || 0) > 0) ? <p className="caption status-warning">{t("Processing is waiting for the worker to start.")}</p> : null}
      {processing?.blocked ? <p className="caption status-warning">{processing.blocked} {t("blocked · needs attention")}</p> : null}
      {stale && status ? <p className="caption">{t("Last known counts; current activity is unknown.")}</p> : null}
    </div>
  </section>;
}
