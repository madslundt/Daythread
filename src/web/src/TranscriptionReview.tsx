import type { TranscriptionDetails } from "./types";

export function TranscriptionReview({ details, onReview }: {
  details?: TranscriptionDetails;
  onReview: () => void;
}) {
  const chunks = details?.chunks || [];
  const alternatives = chunks.filter((chunk) => chunk.candidate?.text && chunk.candidate.differs);
  const unavailable = chunks.some((chunk) =>
    ["failed", "timeout", "unavailable", "budget_exhausted"].includes(chunk.enhancement_status));
  if (!alternatives.length && !unavailable) return null;
  return <aside aria-label="Transcription cleanup review">
    {unavailable ? <p className="caption">
      Audio cleanup was unavailable or did not finish. The original transcription was kept.
    </p> : null}
    {alternatives.length ? <details>
      <summary>Alternative cleanup reading · needs review</summary>
      <p className="caption">
        Cleanup can miss quiet voices or mistake noise for words. These readings are excluded
        from search and summaries. Listen to the original recording, compare the readings,
        then save a correction if needed. Simultaneous voices may still be missing.
      </p>
      {alternatives.map((chunk) => <div key={chunk.start}>
        <p className="caption">{chunk.start.toFixed(1)}–{chunk.end.toFixed(1)} seconds into this recording</p>
        <p className="transcript-text">{chunk.candidate?.text}</p>
      </div>)}
      <button className="text-button" type="button" onClick={onReview}>
        Review and correct transcript
      </button>
    </details> : null}
  </aside>;
}
