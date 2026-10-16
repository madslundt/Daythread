import { Icon } from "./Icon";
import type { Source } from "./types";

export function RecordingSections({ source, compact = false }: { source: Source; compact?: boolean }) {
  if (!source.sections?.length || !source.text) return null;
  // Backend span offsets count Unicode characters, including non-BMP emoji.
  const characters = Array.from(source.text);
  return <div className="recording-sections">
    <h3>{compact ? "Topics" : source.sections.length === 1 ? "Topic" : `${source.sections.length} topic sections`}</h3>
    {source.sections.map((section, index) => <details key={section.start_offset}>
      <summary>{compact ? <><span>{section.title}</span><Icon name="chevron-right" size={14} /></> : <><span className="caption">{index + 1}.</span> {section.title}</>}</summary>
      <p className="transcript-text">{characters.slice(section.start_offset, section.end_offset).join("")}</p>
    </details>)}
  </div>;
}
