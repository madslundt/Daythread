import { statusLabel } from "./statusLabels";
import { displayLocale, t } from "./i18n";
import { useState } from "react";
import { errorMessage, newId, post, request } from "./api";
import { SourceList } from "./SourceList";
import { FeedbackForm } from "./FeedbackForm";
import type { Source, Completeness, Evidence } from "./types";
import { useResource } from "./useResource";
import { ErrorState } from "./UI";

export function CompletenessDetails({ value }: { value?: Completeness }) {
  if (!value) return null;
  const minutes = (key: string) =>
    t("{value0} min", { value0: Math.round((value.seconds[key] || 0) / 60) });
  return (
    <details className="completeness-details">
      <summary>{t("Evidence available in this period")}</summary>
      {value.as_of ? (
        <p className="caption">
          {t("Observed")}{" "}{new Date(value.as_of).toLocaleString(displayLocale())}{t(". Future time is excluded.")}{" "}</p>
      ) : null}
      <p className="caption">
        {minutes("available_audio")} {t("playable audio ·")}{" "}{minutes("transcript")}{" "}
        {t("transcript intervals ·")}{" "}{minutes("intentional_pause")} {t("intentional pauses ·")}{" "}{minutes("reported_interruption")} {t("reported interruptions. Overlaps are counted once per track.")}{" "}</p>
      {value.counts.webhook_receipt_estimates ? <p className="caption">
        {value.counts.webhook_receipt_estimates} {t("official audio chunks use receipt estimates:")}{" "}{" "}{minutes("estimated_audio")} {t("playable audio and")}{" "}{minutes("estimated_transcript")} {t("transcript estimates. These do not establish capture coverage or remove unknown intervals.")}{" "}</p> : null}
      <p className="caption">
        {value.counts.pending} {t("awaiting transcription ·")}{" "}{value.counts.blocked}{" "}
        {t("blocked ·")}{" "}{value.counts.transcript_only_imports} {t("transcript-only imports ·")}{" "}{value.counts.expired_audio} {t("expired audio ·")}{" "}
        {value.counts.missing_audio} {t("missing files.")}{" "}</p>
      <p className="caption">
        {value.counts.expired_transcripts || 0} {t("transcripts unavailable after retention.")}{" "}</p>
      <p className="caption">
        {value.counts.silence_gate} {t("silence-gated ·")}{" "}{value.counts.empty_stt}{" "}
        {t("empty STT results. Neither proves no speech occurred.")}{" "}</p>
      <p className="caption">
        {value.unknown_count} {t("intervals have no known coverage. Available transcripts do not prove continuous capture.")}{" "}</p>
      {value.capture_intervals?.map((interval) => (
        <p className="caption" key={interval.event_id}>
          {statusLabel(interval.state)}: {new Date(interval.start).toLocaleString(displayLocale())} –{" "}
          {new Date(interval.end).toLocaleString(displayLocale())} · {statusLabel(interval.cause)}
          {interval.open ? t("(no closing transition reported)") : ""}
        </p>
      ))}
      {value.capture_gaps.map((gap) => (
        <p key={gap.gap_id} className="caption">
          {new Date(gap.started_at).toLocaleString(displayLocale())} –{" "}
          {new Date(gap.ended_at).toLocaleString(displayLocale())}: {statusLabel(gap.cause)} · {t(gap.reason)}
        </p>
      ))}
      {value.unknown_intervals.map((gap) => (
        <p className="caption" key={gap.start}>
          {t("Unknown:")}{" "}{new Date(gap.start).toLocaleString(displayLocale())} –{" "}
          {new Date(gap.end).toLocaleString(displayLocale())}
        </p>
      ))}
      {value.truncated ? (
        <p className="caption">
          {t("Showing the first 100 interval details; totals include the whole period.")}{" "}</p>
      ) : null}
      <FeedbackForm start={value.start} end={value.end} />
    </details>
  );
}

export function EvidenceFeedback({
  outputId,
  evidence,
  outputText,
}: {
  outputId?: string;
  evidence?: Evidence;
  outputText?: string;
}) {
  const current = useResource<Evidence>(
    outputId ? `/v1/outputs/${outputId}/evidence` : null,
  );
  const value = current.data || evidence;
  if (!outputId)
    return (
      <p className="caption">
        {t("This older output has no saved selection diagnostics.")}{" "}</p>
    );
  return (
    <div className="evidence-feedback">
      {current.error ? (
        <ErrorState message={current.error} retry={current.refresh} />
      ) : null}
      {value ? (
        <p className="caption">
          {value.selected.length} {t("of")}{" "}{value.eligible_transcripts} {t("eligible transcript sources selected ·")}{" "}{value.cited_ids.length} {t("cited ·")}{" "}
          {statusLabel(value.retrieval_mode)}. {value.pending_chunks}{" "}
          {t("pending and")}{" "}{value.blocked_chunks} {t("blocked when generated. Evidence may pass through summarization. Selection and citations do not verify accuracy.")}{" "}</p>
      ) : null}
      {value?.pipeline ? (
        <p className="caption">
          {value.pipeline.reduction_used
            ? t("Evidence was summarized before the final call; {value0} selected source IDs remained in its prompt.", { value0: value.pipeline.final_prompt_source_ids.length })
            : t("The final prompt included source evidence directly.")}{" "}
          {t("Intermediate omissions are not assessed.")}{" "}</p>
      ) : null}
      {value?.changed_source_ids?.length ? (
        <p role="status" className="caption">
          {t("Sources changed since this output. Recaps update automatically; ask again to use current evidence in chat.")}{" "}</p>
      ) : null}
      <FeedbackForm outputId={outputId} outputText={outputText} />
    </div>
  );
}

const stages = ["unknown", "capture", "stt", "retrieval", "generation"];
type Report = {
  id: string;
  issue: string;
  stage: string;
  suggested_stage: string;
  status: string;
  note: string;
  excerpt?: string;
  source_id?: string;
  output_id?: string;
  expected_source_ids: string[];
  events: { note: string; stage: string; status: string }[];
};
export function FeedbackReview() {
  const resource = useResource<{ items: Report[]; total: number }>(
    "/v1/feedback",
  );
  return (
    <details className="feedback-review" onToggle={() => resource.refresh()}>
      <summary>{t("Feedback and repairs (")}{resource.data?.total ?? "…"})</summary>
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : null}
      <p className="caption">
        {t("Sync pending recordings on the phone; listen and correct the source transcript; the recap will update automatically. You can also ask again with a narrower scope. Mark resolved only after checking the repair.")}{" "}</p>
      {resource.data?.items.map((item) => (
        <FeedbackReviewRow
          key={item.id}
          item={item}
          refresh={resource.refresh}
        />
      ))}
      {resource.data && !resource.data.total ? (
        <p className="caption">{t("No reports saved.")}</p>
      ) : null}
      {(resource.data?.total || 0) > 50 ? (
        <p className="caption">{t("Showing the latest 50 reports.")}</p>
      ) : null}
    </details>
  );
}

function FeedbackReviewRow({
  item,
  refresh,
}: {
  item: Report;
  refresh: () => void;
}) {
  const [stage, setStage] = useState(item.stage);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);
  const source = useResource<Source>(
    sourceOpen && item.source_id ? `/v1/sources/${item.source_id}` : null,
  );
  async function update(remove = false) {
    setBusy(true);
    setError("");
    try {
      if (remove)
        await request(`/v1/feedback/${item.id}`, { method: "DELETE" });
      else
        await post(`/v1/feedback/${item.id}/events`, {
          event_id: newId(),
          status: item.status === "open" ? "resolved" : "open",
          stage,
          note,
        });
      refresh();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article>
      <p>
        {statusLabel(item.issue)} · {statusLabel(item.status)} {t("· user stage:")}{" "}{statusLabel(item.stage)} {t("· diagnostic suggestion:")}{" "}{statusLabel(item.suggested_stage)}
      </p>
      <p>{item.note}</p>
      {item.excerpt ? <blockquote>{item.excerpt}</blockquote> : null}
      {item.source_id ? (
        <>
          <button
            className="text-button"
            onClick={() => setSourceOpen(!sourceOpen)}
          >
            {t("Inspect or correct source")}{" "}</button>
          {source.data ? (
            <SourceList
              sources={[source.data]}
              timezone={Intl.DateTimeFormat().resolvedOptions().timeZone}
              expanded
              onCorrect={source.refresh}
            />
          ) : null}
          {source.error ? <ErrorState message={source.error} /> : null}
        </>
      ) : null}
      {item.output_id ? (
        <p className="caption">{t("Output")}{" "}{item.output_id}</p>
      ) : null}
      {item.expected_source_ids.length ? (
        <p className="caption">
          {t("Expected sources:")}{" "}{item.expected_source_ids.join(", ")}
        </p>
      ) : null}
      <p className="caption">
        {item.events
          .map((e) => `${statusLabel(e.status)} (${statusLabel(e.stage)}): ${e.note}`)
          .join(" · ")}
      </p>
      <label>
        {t("Reviewed stage")}{" "}
        <select
          aria-label={t("Reviewed stage")}
          value={stage}
          onChange={(e) => setStage(e.target.value)}
        >
          {stages.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <label>
        {t("Repair checked")}{" "}
        <input
          value={note}
          maxLength={2000}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <button className="text-button" disabled={busy} onClick={() => update()}>
        {item.status === "open" ? t("Mark resolved") : t("Reopen")}
      </button>{" "}
      <button
        className="text-button"
        disabled={busy}
        onClick={() => update(true)}
      >
        {t("Delete report")}{" "}</button>
      {error ? <ErrorState message={error} /> : null}
    </article>
  );
}
