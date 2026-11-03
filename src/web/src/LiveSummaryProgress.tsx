import { displayLocale, t } from "./i18n";

export type SummaryGenerationProgress = {
  day: string; stage: "daily_summary" | "day_overview" | "week_overview";
  status: "waiting" | "reasoning" | "generating";
  request_id: string | null; request_sequence: number; active_requests: number;
  request_started_at: string | null; elapsed_seconds: number;
  last_token_at: string | null; seconds_since_token: number; quiet: boolean;
  output_tokens: number; token_count_source: "provider" | "estimate";
  max_tokens: number | null; reasoning_characters: number; content_characters: number;
  events: number; thinking: boolean | null;
};

export function LiveSummaryProgress({ progress }: { progress: SummaryGenerationProgress }) {
  const number = (value: number) => new Intl.NumberFormat(displayLocale()).format(Math.round(value));
  const seconds = (value: number) => new Intl.NumberFormat(displayLocale(), {
    style: "unit", unit: "second", unitDisplay: "short",
  }).format(Math.round(value));
  const stages = {
    daily_summary: t("Day summary"), day_overview: t("Day overview"), week_overview: t("Week overview"),
  };
  const states = {
    waiting: t("Waiting for the model…"), reasoning: t("Model is thinking…"), generating: t("Writing summary…"),
  };
  return <section className="live-summary-progress" aria-label={t("Live summary progress")}>
    <h4>{t("Live summary progress")}</h4>
    <p>{stages[progress.stage]} · {progress.day}</p>
    <p>{progress.request_id ? states[progress.status] : t("Preparing the next summary request…")}</p>
    {progress.request_id ? <div aria-live="off">
      <p className="live-summary-token-count">{progress.token_count_source === "estimate"
        ? t("≈ {count} output tokens (estimated)", { count: number(progress.output_tokens) })
        : t("{count} output tokens reported", { count: number(progress.output_tokens) })}
        {progress.max_tokens ? <span className="caption"> · {t("{count} token limit", { count: number(progress.max_tokens) })}</span> : null}</p>
      {progress.max_tokens ? <progress max={progress.max_tokens}
        value={Math.min(progress.output_tokens, progress.max_tokens)} aria-label={t("Model output budget")} /> : null}
      <p className="caption">{t("Reasoning: {reasoning} characters · Summary: {summary} characters", {
        reasoning: number(progress.reasoning_characters), summary: number(progress.content_characters),
      })}</p>
      <p className="caption">{t("Request {request} · Elapsed: {elapsed}", {
        request: number(progress.request_sequence), elapsed: seconds(progress.elapsed_seconds),
      })}{progress.active_requests > 1 ? ` · ${t("{count} active model requests", { count: number(progress.active_requests) })}` : ""}</p>
      <p className="caption">{progress.thinking === false ? t("Thinking disabled for this request.")
        : progress.thinking === true ? t("Thinking enabled for this request.") : t("Thinking follows the model default.")}</p>
      <p className="caption">{progress.last_token_at
        ? t("Last model output: {elapsed} ago", { elapsed: seconds(progress.seconds_since_token) })
        : t("Waiting for the first model output.")}</p>
      {progress.quiet ? <p className="notice">{t("No recent model output. The request may be queued or stalled; this does not confirm a failure.")}</p> : null}
      {progress.token_count_source === "estimate" ? <p className="caption">{t("Live token counts are estimates until the model reports usage.")}</p> : null}
    </div> : null}
  </section>;
}
