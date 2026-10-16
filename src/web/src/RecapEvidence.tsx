import { useId, useState } from "react";
import { timeLabel } from "./format";
import { ImportanceButton } from "./Importance";
import { recapSourceSections } from "./recapItems";
import { SourceList } from "./SourceList";
import type { Source } from "./types";

// A recap point reveals its evidence directly. The complete recording is optional.
export function RecapEvidence({ source, point, timezone, onCorrect }: {
  source: Source; point: string; timezone: string; onCorrect: () => void;
}) {
  const [fullRecording, setFullRecording] = useState(false);
  const id = useId();
  const sections = recapSourceSections(point, source);
  const characters = Array.from(source.text || "");
  return <article className="recap-evidence">
    <div className="recap-evidence-heading">
      <span className="caption">{timeLabel(source.started_at, timezone)}</span>
      <span>{source.title || "Recording"}</span>
      <ImportanceButton target={{ kind: "source", id: source.id }} label="recording" compact />
    </div>
    <p className="caption">{sections.length ? "Matching topic passages · original transcript" : "Original transcript · no specific topic passage identified"}</p>
    {sections.length ? sections.map((section) => <blockquote key={section.start_offset}>
      <p className="caption">{section.title}</p>
      <p className="transcript-text">{characters.slice(section.start_offset, section.end_offset).join("")}</p>
    </blockquote>) : <p className="transcript-text">{source.text || "Transcript unavailable for this recording."}</p>}
    <details className="recap-full-recording" onToggle={(event) => {
      if (event.target === event.currentTarget) setFullRecording(event.currentTarget.open);
    }}>
      <summary>Full recording &amp; tools</summary>
      {fullRecording ? <SourceList sources={[source]} timezone={timezone} expanded onCorrect={onCorrect} idPrefix={`recap-full-${id}`} /> : null}
    </details>
  </article>;
}
