import { displayLocale, t } from "./i18n";
import { useState } from "react";
import { ErrorState, Loading } from "./UI";
import { useResource } from "./useResource";
import "./usage.css";

export type UsageMetrics = {
  recordings: number;
  captured_minutes: number;
  retained_minutes: number;
  submitted_minutes: number;
  filtered_percent: number | null;
  transcription_credits: number;
  summary_credits: number;
  other_credits: number;
  unknown_credit_attempts: number;
  provider_attempts: number;
  filter_unavailable_recordings: number;
  summary_original_characters?: number;
  summary_retained_characters?: number;
  summary_submitted_characters?: number;
  summary_removed_fillers?: number;
  summary_omitted_sources?: number;
  summary_filter_runs?: number;
  summary_skipped_runs?: number;
  summary_requests?: number;
  summary_input_tokens?: number;
  summary_output_tokens?: number;
  summary_unknown_token_requests?: number;
  summary_unknown_character_requests?: number;
  summary_filtered_percent?: number | null;
  summary_source_minutes?: number;
  summary_unknown_duration_runs?: number;
};
export type UsageReport = {
  month: string;
  since: string | null;
  timezone: string;
  credits_complete: boolean;
  totals: UsageMetrics;
  days: (UsageMetrics & { date: string })[];
};

function number(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat(displayLocale(), { maximumFractionDigits }).format(value);
}
function dayLabel(value: string): string {
  return new Intl.DateTimeFormat(displayLocale(), { dateStyle: "medium", timeZone: "UTC" })
    .format(new Date(`${value}T12:00:00Z`));
}
function knownCredits(metrics: UsageMetrics): number {
  return metrics.transcription_credits + metrics.summary_credits + metrics.other_credits;
}

function measured(value: number | undefined): string {
  return value === undefined ? t("Not measured") : number(value, 0);
}

function hasKnownSummaryTokens(metrics: UsageMetrics): boolean {
  if (metrics.summary_input_tokens === undefined || metrics.summary_output_tokens === undefined)
    return false;
  if ((metrics.summary_unknown_token_requests ?? 0) > 0 &&
      metrics.summary_unknown_token_requests === metrics.summary_requests &&
      metrics.summary_input_tokens === 0 && metrics.summary_output_tokens === 0)
    return false;
  return true;
}

function summaryTokens(metrics: UsageMetrics): string {
  if (!hasKnownSummaryTokens(metrics)) return t("Not measured");
  return `${measured(metrics.summary_input_tokens)} / ${measured(metrics.summary_output_tokens)}`;
}

function summaryCharacters(metrics: UsageMetrics): string {
  if ((metrics.summary_unknown_character_requests ?? 0) > 0 &&
      metrics.summary_unknown_character_requests === metrics.summary_requests &&
      metrics.summary_submitted_characters === 0)
    return t("Not measured");
  return measured(metrics.summary_submitted_characters);
}

function summarySourceMinutes(metrics: UsageMetrics, withUnit = false): string {
  const minutes = metrics.summary_source_minutes;
  const runs = metrics.summary_filter_runs ?? 0;
  if (minutes === undefined || runs === 0 ||
      minutes === 0 && (metrics.summary_unknown_duration_runs ?? 0) >= runs)
    return t("Not measured");
  return withUnit ? t("{value0} min", { value0: number(minutes) }) : number(minutes);
}

function SummaryUsage({ metrics }: { metrics: UsageMetrics }) {
  const filtered = metrics.summary_filtered_percent;
  const hasFiltering = (metrics.summary_filter_runs ?? 0) > 0;
  const tokensUnknown = metrics.summary_unknown_token_requests;
  const charactersUnknown = metrics.summary_unknown_character_requests;
  const hasMeasurements = metrics.summary_requests !== undefined;
  return <>
    <h3>{t("Summary usage")}</h3>
    <dl className="usage-metrics usage-summary-metrics">
      <div><dt>{t("Source audio considered for summaries")}</dt><dd>{summarySourceMinutes(metrics, true)}</dd></div>
      <div><dt>{t("Known summary credits")}</dt><dd>{number(metrics.summary_credits, 3)}</dd></div>
    </dl>
    <p className="caption">{t("Source minutes describe recording coverage; summaries are charged for text and tokens. Each pass counts selected recordings again, including skipped content; overlaps count once per pass. Retries and reduction add no minutes.")}</p>
    {(metrics.summary_unknown_duration_runs ?? 0) > 0 ? <p className="notice" role="status">{t("Summary source minutes are incomplete. Passes with unknown recording duration: {value0}.", { value0: number(metrics.summary_unknown_duration_runs ?? 0, 0) })}</p> : null}
    <p className="caption">{t("Summary text is measured in characters. Submitted text includes instructions, source references, reduction passes and retries.")}</p>
    <dl className="usage-metrics usage-summary-metrics">
      <div><dt>{t("Original text (characters)")}</dt><dd>{hasFiltering ? measured(metrics.summary_original_characters) : t("Not measured")}</dd></div>
      <div><dt>{t("Retained text (characters)")}</dt><dd>{hasFiltering ? measured(metrics.summary_retained_characters) : t("Not measured")}</dd></div>
      <div><dt>{t("Submitted text (characters)")}</dt><dd>{summaryCharacters(metrics)}</dd></div>
      <div><dt>{t("Summary text filtered out")}</dt><dd>{filtered === undefined || filtered === null ? t("Not measured")
        : new Intl.NumberFormat(displayLocale(), { style: "percent", maximumFractionDigits: 1 }).format(filtered / 100)}</dd></div>
    </dl>
    <p className="caption">{t("Each summary pass counts its selected source text again. Original and retained text measurements are unavailable for older requests.")}</p>
    <dl className="usage-credits usage-summary-counts">
      <div><dt>{t("Measured summary passes")}</dt><dd>{measured(metrics.summary_filter_runs)}</dd></div>
      <div><dt>{t("Fillers removed")}</dt><dd>{hasFiltering ? measured(metrics.summary_removed_fillers) : t("Not measured")}</dd></div>
      <div><dt>{t("Sources omitted")}</dt><dd>{hasFiltering ? measured(metrics.summary_omitted_sources) : t("Not measured")}</dd></div>
      <div><dt>{t("Summary passes skipped")}</dt><dd>{hasFiltering ? measured(metrics.summary_skipped_runs) : t("Not measured")}</dd></div>
      <div><dt>{t("Summary requests")}</dt><dd>{measured(metrics.summary_requests)}</dd></div>
      <div><dt>{t("Known input tokens")}</dt><dd>{hasKnownSummaryTokens(metrics) ? measured(metrics.summary_input_tokens) : t("Not measured")}</dd></div>
      <div><dt>{t("Known output tokens")}</dt><dd>{hasKnownSummaryTokens(metrics) ? measured(metrics.summary_output_tokens) : t("Not measured")}</dd></div>
    </dl>
    {!hasMeasurements ? <p className="notice" role="status">{t("Summary measurements are unavailable in this usage history.")}</p> : null}
    {(tokensUnknown ?? 0) > 0 || (charactersUnknown ?? 0) > 0 ? <p className="notice" role="status">{t("Summary measurements are incomplete. Requests with unknown token usage: {value0}. Requests with unknown submitted text size: {value1}. Pending, failed and older requests may lack these measurements.", { value0: number(tokensUnknown ?? 0, 0), value1: number(charactersUnknown ?? 0, 0) })}</p> : null}
  </>;
}

export function UsageDetails({ report }: { report: UsageReport }) {
  const totals = report.totals;
  return <>
    <p className="caption">{t("Measurements start when a recording is processed. Queued audio and older usage are excluded. Original audio follows your retention settings.")}</p>
    <dl className="usage-metrics">
      <div><dt>{t("Captured audio")}</dt><dd>{t("{value0} min", { value0: number(totals.captured_minutes) })}</dd></div>
      <div><dt>{t("Kept for transcription")}</dt><dd>{t("{value0} min", { value0: number(totals.retained_minutes) })}</dd></div>
      <div><dt>{t("Submitted to transcription")}</dt><dd>{t("{value0} min", { value0: number(totals.submitted_minutes) })}</dd></div>
      <div><dt>{t("Audio filtered out")}</dt><dd>{totals.filtered_percent === null ? t("No audio yet")
        : new Intl.NumberFormat(displayLocale(), { style: "percent", maximumFractionDigits: 1 }).format(totals.filtered_percent / 100)}</dd></div>
    </dl>
    <p className="caption">{t("Kept audio includes a little context around speech. Submitted minutes include extra transcription passes and retries, so they can exceed kept minutes.")}</p>
    <SummaryUsage metrics={totals} />
    <h3>{t("Known Daythread credits")}</h3>
    <dl className="usage-credits">
      <div><dt>{t("Transcription")}</dt><dd>{number(totals.transcription_credits, 3)}</dd></div>
      <div><dt>{t("Summaries & recaps")}</dt><dd>{number(totals.summary_credits, 3)}</dd></div>
      <div><dt>{t("Other AI tasks")}</dt><dd>{number(totals.other_credits, 3)}</dd></div>
    </dl>
    {!report.credits_complete ? <p className="notice" role="status">{t("Credit totals are incomplete. Requests with unknown charges: {value0}. Pending requests and failures may still incur charges.", { value0: number(totals.unknown_credit_attempts) })}</p> : null}
    {totals.provider_attempts > 0 ? <p className="caption">{t("Requests using your own provider or local models: {value0}. Their costs are not included in Daythread credits.", { value0: number(totals.provider_attempts) })}</p> : null}
    {totals.filter_unavailable_recordings > 0 ? <p className="notice" role="status">{t("Speech filtering was unavailable for {value0} recordings. Their original audio was submitted instead.", { value0: number(totals.filter_unavailable_recordings) })}</p> : null}
    <h3>{t("Daily usage")}</h3>
    <p className="caption">{t("Grouped by processing date in {value0} for the selected month.", { value0: report.timezone })}</p>
    {report.days.length === 0 ? <p>{t("No usage recorded for this month.")}</p> : <div className="usage-table-scroll" tabIndex={0} role="region" aria-label={t("Daily usage")}>
      <table className="usage-table">
        <thead><tr><th scope="col">{t("Date")}</th><th scope="col">{t("Captured minutes")}</th>
          <th scope="col">{t("Kept minutes")}</th><th scope="col">{t("Submitted minutes")}</th>
          <th scope="col">{t("Summary source minutes")}</th>
          <th scope="col">{t("Summary submitted characters")}</th><th scope="col">{t("Summary requests")}</th>
          <th scope="col">{t("Summary tokens (in / out)")}</th><th scope="col">{t("Known credits")}</th></tr></thead>
        <tbody>{report.days.map((day) => <tr key={day.date}>
          <th scope="row">{dayLabel(day.date)}</th><td>{number(day.captured_minutes)}</td><td>{number(day.retained_minutes)}</td>
          <td>{number(day.submitted_minutes)}</td><td>{summarySourceMinutes(day)}{(day.summary_unknown_duration_runs ?? 0) > 0 ? <small>{t("(incomplete)")}</small> : null}</td>
          <td>{summaryCharacters(day)}{(day.summary_unknown_character_requests ?? 0) > 0 ? <small>{t("(incomplete)")}</small> : null}</td>
          <td>{measured(day.summary_requests)}</td><td>{summaryTokens(day)}{(day.summary_unknown_token_requests ?? 0) > 0 ? <small>{t("(incomplete)")}</small> : null}</td>
          <td>{number(knownCredits(day), 3)}{day.unknown_credit_attempts > 0 ? <small> {t("(incomplete)")}</small> : null}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </>;
}

export function Usage() {
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null);
  const resource = useResource<UsageReport>(selectedMonth ? `/v1/usage?month=${selectedMonth}` : "/v1/usage", 30_000);
  const month = selectedMonth ?? resource.data?.month ?? "";
  return <section className="usage-panel">
    <div className="usage-heading"><div className="settings-section-heading"><h2>{t("Audio & credit usage")}</h2></div>
      <div className="usage-controls">
        <label className="usage-month-picker" htmlFor="usage-month"><span>{t("Choose month")}</span>
          <input id="usage-month" type="month" min="0001-01" max="9999-12" value={month}
            disabled={!month} onChange={(event) => {
              const value = event.target.value;
              if (/^(?!0000)\d{4}-(?:0[1-9]|1[0-2])$/.test(value)) setSelectedMonth(value);
            }} />
        </label>
        <button type="button" className="button secondary" onClick={resource.refresh} disabled={resource.loading}>{t("Refresh")}</button>
      </div></div>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.loading && !resource.data ? <Loading>{t("Loading usage…")}</Loading> : null}
    {resource.data ? <UsageDetails report={resource.data} /> : null}
  </section>;
}
