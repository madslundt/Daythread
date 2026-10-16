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
    `${Math.round((value.seconds[key] || 0) / 60)} min`;
  return (
    <details className="completeness-details">
      <summary>Evidence available in this period</summary>
      {value.as_of ? (
        <p className="caption">
          Observed {new Date(value.as_of).toLocaleString()}. Future time is
          excluded.
        </p>
      ) : null}
      <p className="caption">
        {minutes("available_audio")} playable audio · {minutes("transcript")}{" "}
        transcript intervals · {minutes("intentional_pause")} intentional pauses
        · {minutes("reported_interruption")} reported interruptions. Overlaps
        are counted once per track.
      </p>
      {value.counts.webhook_receipt_estimates ? <p className="caption">
        {value.counts.webhook_receipt_estimates} official audio chunks use receipt estimates:
        {" "}{minutes("estimated_audio")} playable audio and {minutes("estimated_transcript")} transcript estimates.
        These do not establish capture coverage or remove unknown intervals.
      </p> : null}
      <p className="caption">
        {value.counts.pending} awaiting transcription · {value.counts.blocked}{" "}
        blocked · {value.counts.transcript_only_imports} transcript-only imports
        · {value.counts.expired_audio} expired audio ·{" "}
        {value.counts.missing_audio} missing files.
      </p>
      <p className="caption">
        {value.counts.expired_transcripts || 0} transcripts unavailable after
        retention.
      </p>
      <p className="caption">
        {value.counts.silence_gate} silence-gated · {value.counts.empty_stt}{" "}
        empty STT results. Neither proves no speech occurred.
      </p>
      <p className="caption">
        {value.unknown_count} intervals have no known coverage. Available
        transcripts do not prove continuous capture.
      </p>
      {value.capture_intervals?.map((interval) => (
        <p className="caption" key={interval.event_id}>
          {interval.state}: {new Date(interval.start).toLocaleString()} –{" "}
          {new Date(interval.end).toLocaleString()} · {interval.cause}
          {interval.open ? " (no closing transition reported)" : ""}
        </p>
      ))}
      {value.capture_gaps.map((gap) => (
        <p key={gap.gap_id} className="caption">
          {new Date(gap.started_at).toLocaleString()} –{" "}
          {new Date(gap.ended_at).toLocaleString()}: {gap.cause} · {gap.reason}
        </p>
      ))}
      {value.unknown_intervals.map((gap) => (
        <p className="caption" key={gap.start}>
          Unknown: {new Date(gap.start).toLocaleString()} –{" "}
          {new Date(gap.end).toLocaleString()}
        </p>
      ))}
      {value.truncated ? (
        <p className="caption">
          Showing the first 100 interval details; totals include the whole
          period.
        </p>
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
        This older output has no saved selection diagnostics.
      </p>
    );
  return (
    <div className="evidence-feedback">
      {current.error ? (
        <ErrorState message={current.error} retry={current.refresh} />
      ) : null}
      {value ? (
        <p className="caption">
          {value.selected.length} of {value.eligible_transcripts} eligible
          transcript sources selected · {value.cited_ids.length} cited ·{" "}
          {value.retrieval_mode.replaceAll("_", " ")}. {value.pending_chunks}{" "}
          pending and {value.blocked_chunks} blocked when generated. Evidence
          may pass through summarization. Selection and citations do not verify
          accuracy.
        </p>
      ) : null}
      {value?.pipeline ? (
        <p className="caption">
          {value.pipeline.reduction_used
            ? `Evidence was summarized before the final call; ${value.pipeline.final_prompt_source_ids.length} selected source IDs remained in its prompt.`
            : "The final prompt included source evidence directly."}{" "}
          Intermediate omissions are not assessed.
        </p>
      ) : null}
      {value?.changed_source_ids?.length ? (
        <p role="status" className="caption">
          Sources changed since this output. Recaps update automatically; ask again
          to use current evidence in chat.
        </p>
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
      <summary>Feedback and repairs ({resource.data?.total ?? "…"})</summary>
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : null}
      <p className="caption">
        Sync pending recordings on the phone; listen and correct the source
        transcript; the recap will update automatically. You can also ask again
        with a narrower scope. Mark resolved only after checking the repair.
      </p>
      {resource.data?.items.map((item) => (
        <FeedbackReviewRow
          key={item.id}
          item={item}
          refresh={resource.refresh}
        />
      ))}
      {resource.data && !resource.data.total ? (
        <p className="caption">No reports saved.</p>
      ) : null}
      {(resource.data?.total || 0) > 50 ? (
        <p className="caption">Showing the latest 50 reports.</p>
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
        {item.issue} · {item.status} · user stage: {item.stage} · diagnostic
        suggestion: {item.suggested_stage.replaceAll("_", " ")}
      </p>
      <p>{item.note}</p>
      {item.excerpt ? <blockquote>{item.excerpt}</blockquote> : null}
      {item.source_id ? (
        <>
          <button
            className="text-button"
            onClick={() => setSourceOpen(!sourceOpen)}
          >
            Inspect or correct source
          </button>
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
        <p className="caption">Output {item.output_id}</p>
      ) : null}
      {item.expected_source_ids.length ? (
        <p className="caption">
          Expected sources: {item.expected_source_ids.join(", ")}
        </p>
      ) : null}
      <p className="caption">
        {item.events
          .map((e) => `${e.status} (${e.stage}): ${e.note}`)
          .join(" · ")}
      </p>
      <label>
        Reviewed stage{" "}
        <select
          aria-label="Reviewed stage"
          value={stage}
          onChange={(e) => setStage(e.target.value)}
        >
          {stages.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <label>
        Repair checked{" "}
        <input
          value={note}
          maxLength={2000}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <button className="text-button" disabled={busy} onClick={() => update()}>
        {item.status === "open" ? "Mark resolved" : "Reopen"}
      </button>{" "}
      <button
        className="text-button"
        disabled={busy}
        onClick={() => update(true)}
      >
        Delete report
      </button>
      {error ? <ErrorState message={error} /> : null}
    </article>
  );
}
