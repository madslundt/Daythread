import { useState } from "react";
import { CopySummary } from "./CopySummary";
import { Icon } from "./Icon";
import { ImportanceButton, type ImportanceTarget } from "./Importance";
import { RecapEvidence } from "./RecapEvidence";
import { itemSources, recapTopicText, type RecapPart } from "./recapItems";
import type { ChatScope, Source } from "./types";
import { InlineText, RichText } from "./UI";

type Props = {
  title: string; parts: RecapPart[]; day: string; sources: Source[]; timezone: string;
  onChat: (scope: ChatScope, question?: string) => void; onCorrect: () => void;
  importanceTarget?: ImportanceTarget; compact: boolean;
};

export function RecapTopic({ title, parts, day, sources, timezone, onChat, onCorrect, importanceTarget, compact }: Props) {
  const [showEvidence, setShowEvidence] = useState(false);
  const text = recapTopicText(parts);
  const plain = text.replace(/\*\*/g, "");
  const heading = title.replace(/\*\*/g, "");
  const related = itemSources(parts.map((part) => part.text).join("\n"), sources);
  const actions = <div className="recap-topic-actions">
    <CopySummary text={`${heading}\n\n${plain}`} context={heading} />
    <button type="button" className="text-button" onClick={() => onChat({ kind: "day", day }, `Tell me more about this topic from my day recap: ${heading}\n${plain}`)}>
      <Icon name="chat" size={16} />Chat about this
    </button>
  </div>;
  const body = <>
    <div className="recap-topic-summary"><RichText text={text} /></div>
    {actions}
    <details className="recap-topic-evidence" onToggle={(event) => {
      if (event.target === event.currentTarget) setShowEvidence(event.currentTarget.open);
    }}>
      <summary>Original passages</summary>
      {showEvidence ? related.sources.length ? related.sources.map((source) => (
        <RecapEvidence key={source.id} source={source} point={plain} topic={heading} timezone={timezone} onCorrect={onCorrect} />
      )) : <p className="caption">No linked or matching recordings for this topic.</p> : null}
    </details>
  </>;
  const star = importanceTarget ? <ImportanceButton target={importanceTarget} label={heading} compact /> : null;
  return compact ? <details className="recap-group">
    <summary>
      <span className="recap-group-overview"><span><InlineText text={title} /></span><span className="recap-group-summary">{plain}</span></span>
      {star}<Icon name="chevron-right" size={15} />
    </summary>
    <div className="recap-group-content">{body}</div>
  </details> : <section className="recap-topic">
    <div className="recap-section-heading"><h3><InlineText text={title} /></h3>{star}</div>
    {body}
  </section>;
}
