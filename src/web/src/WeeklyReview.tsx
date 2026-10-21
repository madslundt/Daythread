import { t, source } from "./i18n";
import { profileResourceUrl } from "./profiles";
import { useEffect, useState } from "react";
import { errorMessage, newId, post } from "./api";
import { dateRangeForShortcut, dateTimeLabel, dayLabel, localDay, shiftDay } from "./format";
import { SourceList } from "./SourceList";
import type { ChatScope, Source } from "./types";
import { ErrorState, Loading, Notice } from "./UI";
import { useResource } from "./useResource";
import { WeeklyRecapOverview } from "./DayRecapOverview";

type Coverage = {
  sources: number; speech_sources: number; speech_days: number; pending_chunks: number;
  empty_chunks: number; expired_chunks: number; audio_unavailable: number;
  captured_seconds: number; capture_gaps: number; known_gap_seconds: number;
};
type Item = {
  category: "decision" | "commitment" | "discussion" | "change";
  claim: string; qualification: string;
  status: "supported" | "explicitly_open" | "follow_through_unconfirmed";
  evidence: { source_id: string; quote: string }[];
};
type Version = {
  id: string; week_start: string; timezone: string; generated_at: string;
  cutoff: string; start: string; end: string; comparison_start: string; partial: boolean;
  coverage: Coverage; comparison_coverage: Coverage; items: Item[];
};
type Job = {
  id: string; state: "queued" | "running" | "succeeded" | "failed";
  completed_calls: number; error: string | null;
};
type Review = {
  week_start: string; timezone: string; partial: boolean; cutoff: string;
  coverage: Coverage; comparison_coverage: Coverage;
  state: "generated" | "not_generated" | "empty"; version: Version | null;
  stale: boolean; sources: Source[]; job: Job | null;
};
const groups: [Item["category"], string][] = [
  ["decision", source("Supported decisions")], ["commitment", source("Commitments needing attention")],
  ["discussion", source("Recurring discussions")], ["change", source("Changes since last week")],
];

export function WeeklyReview({ timezone, onChat }: {
  timezone: string; onChat: (scope: ChatScope) => void;
}) {
  const today = localDay(timezone);
  const currentWeek = dateRangeForShortcut("this_week", today).start_day;
  const [week, setWeek] = useState(() => dateRangeForShortcut("last_week", today).start_day);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [selectedSource, setSelectedSource] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const path = `/v1/weekly-reviews/${week}`;
  const review = useResource<Review>(path + (selectedVersion ? `?version_id=${selectedVersion}` : ""));
  const activeJob = review.data?.job;
  const jobResource = useResource<Job>(activeJob && ["queued", "running"].includes(activeJob.state)
    ? `/v1/weekly-reviews/jobs/${activeJob.id}` : null, 2000);
  const history = useResource<{ items: { id: string; created_at: string }[] }>(
    showHistory ? `${path}/versions` : null,
  );
  useEffect(() => {
    const update = () => {
      review.refresh();
      history.refresh();
      setSelectedSource("");
      setSelectedVersion("");
    };
    window.addEventListener("omi:archive-changed", update);
    window.addEventListener("focus", update);
    return () => {
      window.removeEventListener("omi:archive-changed", update);
      window.removeEventListener("focus", update);
    };
  }, [review.refresh, history.refresh]);
  useEffect(() => {
    if (review.data?.job?.state === "succeeded") history.refresh();
  }, [review.data?.job?.id, review.data?.job?.state, history.refresh]);
  useEffect(() => {
    if (jobResource.data && ["succeeded", "failed"].includes(jobResource.data.state)) {
      review.refresh();
      history.refresh();
    }
  }, [jobResource.data?.state, review.refresh, history.refresh]);
  useEffect(() => {
    if (selectedSource) document.querySelector(".weekly-evidence")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selectedSource]);
  function select(value: string) {
    setWeek(value);
    setSelectedVersion("");
    setSelectedSource("");
    setError("");
  }
  async function generate(force: boolean) {
    setBusy(true);
    setError("");
    try {
      await post(`${path}/generate`, { request_id: newId(), force });
      setSelectedVersion("");
      review.refresh();
      history.refresh();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  const data = review.data;
  const version = data?.version;
  const job = jobResource.data || data?.job;
  const running = job?.state === "queued" || job?.state === "running";
  const cited = data?.sources.find((source) => source.id === selectedSource);
  return (
    <section className="weekly-review">
      <div className="section-heading weekly-navigation">
        <button className="button" onClick={() => select(shiftDay(week, -7))}>{t("← Previous week")}</button>
        <label>{t("Week beginning")}{" "}<input type="date" value={week} max={currentWeek} onChange={(event) => {
            if (event.target.value) select(dateRangeForShortcut("this_week", event.target.value).start_day);
          }} />
        </label>
        <button className="button" disabled={week >= currentWeek}
          onClick={() => select(shiftDay(week, 7))}>{t("Next week →")}</button>
      </div>
      <div className="button-row">
        <button className="text-button" onClick={() => select(shiftDay(currentWeek, -7))}>{t("Last completed week")}</button>
        <button className="text-button" onClick={() => select(currentWeek)}>{t("This week so far")}</button>
      </div>
      <h2>{dayLabel(week, true)} – {dayLabel(shiftDay(week, 6), true)}</h2>
      {!selectedVersion ? <>
        <h3>{t("Weekly recap")}</h3>
        <WeeklyRecapOverview key={week} week={week} timezone={timezone} />
      </> : null}
      <p className="caption">{t("Compared with")}{" "}{dayLabel(shiftDay(week, -7), true)} – {dayLabel(shiftDay(week, -1), true)} · {timezone}</p>
      <p className="caption">{t("Two weeks of recorded evidence, including commitments needing follow-up. This is not a complete backlog of older commitments. Absence of later mention never establishes completion.")}</p>
      {error || review.error || jobResource.error ? <ErrorState message={error || review.error || jobResource.error} retry={review.refresh} /> : null}
      {!data && review.loading ? <Loading /> : null}
      {data ? <>
        {data.partial ? <Notice>{t("This week is still in progress. Evidence cutoff:")}{" "}{dateTimeLabel(data.cutoff, data.timezone)}.</Notice> : null}
        <p className="caption">{t("Current archive coverage ·")}{" "}{data.timezone}</p>
        <div className="weekly-coverage">
          <CoverageSummary title={t("Selected week")} coverage={data.coverage} />
          <CoverageSummary title={t("Previous week")} coverage={data.comparison_coverage} />
        </div>
        <p className="caption">{t("Coverage describes received evidence only. Unuploaded phone backlog is unknown. Missing recordings do not establish silence or device failure. Transcripts cover whole overlapping chunks; dates and audio boundaries can differ.")}</p>
        {!data.comparison_coverage.speech_sources ? <Notice>{t("Insufficient recorded evidence to compare with the previous week.")}</Notice> : null}
        <div className="button-row">
          <button className="button dark" disabled={busy || running || !data.coverage.speech_sources}
            onClick={() => void generate(false)}>{busy ? t("Requesting…") : running ? t("Generating…") : version ? data.stale ? t("Refresh review") : t("Use saved review") : t("Generate review")}</button>
          {version ? <>
            <button className="button" disabled={busy || running} onClick={() => void generate(true)}>{t("Regenerate")}</button>
            <a className="button" href={profileResourceUrl(`${path}.md${selectedVersion ? `?version_id=${selectedVersion}` : ""}`)} download>{t("Export Markdown")}</a>
          </> : null}
          <button className="button" onClick={() => onChat({ kind: "range", start_day: week, end_day: shiftDay(week, 6) })}>{t("Chat about this week")}</button>
          <button className="text-button" onClick={() => setShowHistory((value) => !value)}>{t("Review history")}</button>
        </div>
        {running ? <p role="status">{job.state === "queued" ? t("Queued for local generation") : t("Generating locally · {value0} model calls completed", { value0: job.completed_calls })}{t(". You can leave this view; the backend keeps working.")}</p> : null}
        {job?.state === "failed" ? <ErrorState message={job.error || t("Generation failed. Try again.")} /> : null}
        {data.state === "empty" ? <p role="status">{t("No transcribed speech received for this week. Pending audio may appear later.")}</p> : null}
        {data.stale ? <Notice>{t("Recorded evidence or configuration changed. This saved interpretation is stale; refresh it before relying on it. Source inspection shows current transcripts.")}</Notice> : null}
        {selectedVersion ? <Notice>{t("Historical version. Its claims and quotes reflect the generation cutoff. Source inspection shows current transcripts.")}{" "}<button className="text-button" onClick={() => setSelectedVersion("")}>{t("Return to latest")}</button></Notice> : null}
        {showHistory ? <div className="weekly-history">
          {history.error ? <ErrorState message={history.error} retry={history.refresh} /> : null}
          {history.data?.items.length ? history.data.items.map((item) => <button className="button small" key={item.id}
            onClick={() => { setSelectedVersion(item.id); setSelectedSource(""); }}>
            {dateTimeLabel(item.created_at, timezone)} · {item.id.slice(0, 8)}
          </button>) : <p className="caption">{t("No saved versions.")}</p>}
        </div> : null}
        {version ? <>
          <p className="caption">{t("Saved version coverage: selected")}{" "}{version.coverage.speech_sources} {t("speech sources, comparison")}{" "}{version.comparison_coverage.speech_sources} {t("· bounds")}{" "}{dateTimeLabel(version.start, version.timezone)} {t("to")}{" "}{dateTimeLabel(version.end, version.timezone)} {t("(exclusive) ·")}{" "}{version.timezone}</p>
          <p className="caption">{t("Saved")}{" "}{dateTimeLabel(version.generated_at, version.timezone)} {t("· cutoff")}{" "}{dateTimeLabel(version.cutoff, version.timezone)} · {version.timezone}{version.partial ? t("· partial week") : ""}</p>
          {groups.map(([category, title]) => <section className="weekly-group" key={category}>
            <h3>{title}</h3>
            {version.items.filter((item) => item.category === category).length ? version.items.filter((item) => item.category === category).map((item, index) => <article className="weekly-item" key={index}>
              <p><strong>{item.claim}</strong></p>
              {category === "commitment" ? <p className="caption">{item.status === "explicitly_open" ? t("Explicitly still open") : t("Follow-through unconfirmed")}</p> : null}
              <p>{item.qualification}</p>
              {item.evidence.map((ref, citationIndex) => <div className="weekly-citation" key={citationIndex}>
                <blockquote>{ref.quote}</blockquote>
                <button className="text-button" onClick={() => setSelectedSource(ref.source_id)}>{t("Inspect source")}{" "}{ref.source_id.slice(0, 8)}</button>
              </div>)}
            </article>) : <p className="caption">{t("No supported items found in the recorded evidence.")}</p>}
          </section>)}
          {cited ? <section className="weekly-evidence" aria-label={t("Selected evidence")}>
            <h3>{t("Original evidence · current transcript")}</h3>
            <SourceList key={`${cited.id}-${cited.text}`} sources={[cited]} timezone={timezone} showDate expanded onCorrect={review.refresh} onDeleted={() => { setSelectedVersion(""); review.refresh(); history.refresh(); }} />
          </section> : null}
        </> : null}
      </> : null}
    </section>
  );
}

function CoverageSummary({ title, coverage }: { title: string; coverage: Coverage }) {
  return <div className="weekly-coverage-panel"><strong>{title}</strong>
    <p>{coverage.speech_days} {t("days with speech ·")}{" "}{coverage.speech_sources} {t("speech sources ·")}{" "}{Math.round(coverage.captured_seconds / 60)} {t("recorded interval minutes")}</p>
    <p className="caption">{coverage.pending_chunks} {t("pending ·")}{" "}{coverage.empty_chunks} {t("empty/silence ·")}{" "}{coverage.expired_chunks} {t("expired transcripts ·")}{" "}{coverage.capture_gaps} {t("known capture gaps (")}{Math.round(coverage.known_gap_seconds)} {t("seconds) ·")}{" "}{coverage.audio_unavailable} {t("sources without audio")}</p>
  </div>;
}
