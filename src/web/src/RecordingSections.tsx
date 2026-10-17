import { t } from "./i18n";
import { useState } from "react";
import { TranscriptPassage } from "./TranscriptTranslation";
import { Icon } from "./Icon";
import type { Source } from "./types";

export function RecordingSections({ source, compact = false }: { source: Source; compact?: boolean }) {
  if (!source.sections?.length || !source.text) return null;
  return <div className="recording-sections">
    <h3>{compact ? t("Topics") : source.sections.length === 1 ? t("Topic") : `${source.sections.length} topic sections`}</h3>
    {source.sections.map((section, index) => <RecordingSection key={section.start_offset}
      source={source} section={section} index={index} compact={compact} />)}
  </div>;
}

function RecordingSection({ source, section, index, compact }: {
  source: Source; section: NonNullable<Source["sections"]>[number]; index: number; compact: boolean;
}) {
  const [open, setOpen] = useState(false);
  return <details onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{compact ? <><span>{section.title}</span><Icon name="chevron-right" size={14} /></> : <><span className="caption">{index + 1}.</span> {section.title}</>}</summary>
    {open ? <TranscriptPassage source={source} section={section} /> : <p className="transcript-text">
      {Array.from(source.text || "").slice(section.start_offset, section.end_offset).join("")}
    </p>}
  </details>;
}
