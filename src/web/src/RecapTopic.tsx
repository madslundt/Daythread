import { t } from "./i18n";
import { useState } from "react";
import { Icon } from "./Icon";
import { RecapEvidence } from "./RecapEvidence";
import { itemSources, recapTopicText, type RecapPart } from "./recapItems";
import type { Source } from "./types";
import { InlineText, RichText } from "./UI";

type Props = {
  title: string; parts: RecapPart[]; sources: Source[]; timezone: string;
  onCorrect: () => void; compact: boolean;
};

export function RecapTopic({ title, parts, sources, timezone, onCorrect, compact }: Props) {
  const [showEvidence, setShowEvidence] = useState(false);
  const text = recapTopicText(parts);
  const plain = text.replace(/\*\*/g, "");
  const heading = title.replace(/\*\*/g, "");
  const related = itemSources(parts.map((part) => part.text).join("\n"), sources);
  const body = <>
    <div className="recap-topic-summary"><RichText text={text} /></div>
    <details className="recap-topic-evidence" onToggle={(event) => {
      if (event.target === event.currentTarget) setShowEvidence(event.currentTarget.open);
    }}>
      <summary>{t("Original passages")}</summary>
      {showEvidence ? related.sources.length ? related.sources.map((source) => (
        <RecapEvidence key={source.id} source={source} point={plain} topic={heading} timezone={timezone} onCorrect={onCorrect} />
      )) : <p className="caption">{t("No linked or matching recordings for this topic.")}</p> : null}
    </details>
  </>;
  return compact ? <details className="recap-group">
    <summary>
      <span className="recap-group-overview"><span><InlineText text={title} /></span><span className="recap-group-summary">{plain}</span></span>
      <Icon name="chevron-right" size={15} />
    </summary>
    <div className="recap-group-content">{body}</div>
  </details> : <section className="recap-topic">
    <div className="recap-section-heading"><h3><InlineText text={title} /></h3></div>
    {body}
  </section>;
}
