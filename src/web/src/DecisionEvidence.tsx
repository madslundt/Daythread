import { useEffect, useState } from "react";
import { audioUrl } from "./api";
import type { DecisionAnchor } from "./decisionModels";
import { evidencePassage } from "./decisionModels";
import type { Source } from "./types";
import { ErrorState, Loading, Notice } from "./UI";
import { useResource } from "./useResource";

export function DecisionEvidence({ anchor, historical }: { anchor: DecisionAnchor; historical: boolean }) {
  const [open, setOpen] = useState(false);
  const [audioError, setAudioError] = useState(false);
  const resource = useResource<Source>(open ? `/v1/transcripts/${encodeURIComponent(anchor.source_id)}` : null);
  const { setData, refresh } = resource;
  useEffect(() => {
    const invalidate = () => { setData(undefined); refresh(); setAudioError(false); };
    window.addEventListener("omi:archive-changed", invalidate);
    return () => window.removeEventListener("omi:archive-changed", invalidate);
  }, [setData, refresh]);
  const source = resource.data;
  const passage = source ? evidencePassage(source, anchor) : null;
  return <details className="decision-evidence" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Supporting passage · source {anchor.source_id.slice(0, 8)}</summary>
    <blockquote>{anchor.quote}</blockquote>
    <p className="caption">Transcript characters {anchor.start}–{anchor.end}. {historical ? "Historical evidence; inspect the current transcript below." : "Check the quotation in its original context before confirming."}</p>
    {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
    {resource.loading && !source ? <Loading>Loading original transcript…</Loading> : null}
    {source ? <>
      {!passage ? <Notice tone="error">The transcript version or passage has changed. This quotation cannot be located in the current evidence. Edit the record and explicitly attach current evidence before confirming.</Notice> : null}
      <p className="caption">Current source transcript · {source.started_at} · {source.device_id}</p>
      <p className="transcript-text decision-original">{passage ? <>{passage.before}<mark>{passage.quote}</mark>{passage.after}</> : source.text || "No transcript remains."}</p>
      {source.audio_available === true ? <>
        <audio controls preload="none" src={audioUrl(source.id)} onError={() => setAudioError(true)} aria-label="Play supporting source" />
        {audioError ? <p className="caption">This source audio is currently unavailable.</p> : null}
      </> : <p className="caption">{source.source_kind === "omi_api" ? "Imported transcript only; original audio is unavailable." : "Original audio is unavailable or has expired. The transcript remains the evidence."}</p>}
    </> : null}
  </details>;
}
