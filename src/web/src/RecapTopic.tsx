import { t } from "./i18n";
import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";
import { CopySummary } from "./CopySummary";
import { RecapEvidence } from "./RecapEvidence";
import { currentRecapPassage, itemSources, recapTopicText, type OriginalRecapPassage, type RecapFocus, type RecapPart } from "./recapItems";
import type { ChatScope, Source } from "./types";
import { ErrorState, InlineText, Loading, RichText } from "./UI";
import { useResource } from "./useResource";

type Props = {
  title: string; parts: RecapPart[]; sources: Source[]; timezone: string;
  onCorrect: () => void; compact: boolean;
  day?: string; expanded?: boolean; onExplore?: () => void;
  originalPassages?: OriginalRecapPassage[];
  onFocus?: (focus?: RecapFocus) => void;
  onChat?: (scope: ChatScope, question?: string) => void;
};

export function RecapTopic({ title, parts, sources, timezone, onCorrect, compact, day, onChat, expanded = false, onExplore, originalPassages, onFocus }: Props) {
  const [showEvidence, setShowEvidence] = useState(false);
  const text = recapTopicText(parts);
  const plain = text.replace(/\*\*/g, "");
  const heading = title.replace(/\*\*/g, "");
  const related = itemSources(`**${title}**\n${parts.map((part) => part.text).join("\n")}`, sources);
  const evidence = originalPassages ? <OriginalTopicEvidence title={title} passages={originalPassages}
    timezone={timezone} onCorrect={onCorrect} onFocus={onFocus} /> : related.sources.length ? related.sources.map((source) => (
    <CurrentTopicEvidence key={source.id} id={source.id} point={plain} topic={heading} timezone={timezone} onCorrect={onCorrect} />
  )) : <p className="caption">{t("No linked or matching recordings for this topic.")}</p>;
  const body = <>
    {text ? <div className="recap-topic-summary"><RichText text={text} /></div> : null}
    {!onExplore ? <div className="recap-topic-actions">
      {expanded && onChat && day ? <button className="text-button" onClick={() => onChat({ kind: "day", day },
        t("Recap {value0} from this day. What happened, what was agreed, and what needs follow-up?", { value0: heading }))}>
        <Icon name="chat" size={16} />{t("Chat about this topic")}
      </button> : null}
      {text ? <CopySummary text={`${heading}\n\n${plain}`} context={heading} /> : null}
    </div> : null}
    {onExplore ? null : expanded ? <div className="recap-topic-passages"><h4>{t("Original passages")}</h4>{evidence}</div> : <details className="recap-topic-evidence" onToggle={(event) => {
      if (event.target === event.currentTarget) setShowEvidence(event.currentTarget.open);
    }}>
      <summary>{t("Original passages")}</summary>
      {showEvidence ? evidence : null}
    </details>}
  </>;
  return compact ? <details className="recap-group">
    <summary>
      <span className="recap-group-overview"><span><InlineText text={title} /></span><span className="recap-group-summary">{plain}</span></span>
      <Icon name="chevron-right" size={15} />
    </summary>
    <div className="recap-group-content">{body}</div>
  </details> : <section className="recap-topic">
    <div className="recap-section-heading"><h3><InlineText text={title} /></h3>
      {onExplore ? <button className="text-button" aria-label={t("Explore topic: {value0}", { value0: heading })} onClick={onExplore}>{t("Explore topic")}<Icon name="chevron-right" size={15} /></button> : null}
    </div>
    {body}
  </section>;
}

type OriginalEvidenceResult = { passage: OriginalRecapPassage; source?: Source; error?: string };

function OriginalTopicEvidence({ title, passages, timezone, onCorrect, onFocus }: {
  title: string; passages: OriginalRecapPassage[]; timezone: string; onCorrect: () => void;
  onFocus?: (focus?: RecapFocus) => void;
}) {
  const [results, setResults] = useState<OriginalEvidenceResult[]>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => { setResults(undefined); onFocus?.({ title, basis: "original", passages: [] }); setRevision((value) => value + 1); };
    window.addEventListener("focus", refresh);
    window.addEventListener("omi:archive-changed", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("omi:archive-changed", refresh);
    };
  }, [title, onFocus]);
  useEffect(() => {
    const controller = new AbortController();
    setResults(undefined); onFocus?.({ title, basis: "original", passages: [] });
    const ids = [...new Set(passages.map((passage) => passage.source_id))];
    void Promise.allSettled(ids.map((id) => request<Source>(`/v1/transcripts/${encodeURIComponent(id)}`, { signal: controller.signal })))
      .then((responses) => {
        if (controller.signal.aborted) return;
        const fetched = new Map(ids.map((id, index) => [id, responses[index]]));
        const checked = passages.map((passage): OriginalEvidenceResult => {
          const response = fetched.get(passage.source_id)!;
          if (response.status === "rejected") return { passage, error: errorMessage(response.reason) };
          const source = currentRecapPassage(passage, response.value);
          return source ? { passage, source } : { passage, error: t("Evidence changed — review required") };
        });
        setResults(checked);
        onFocus?.({ title, basis: "original", passages: checked.flatMap(({ passage, source }) => source ? [{
          source_id: source.id, start: passage.section.start_offset, end: passage.section.end_offset,
        }] : []) });
      });
    return () => controller.abort();
  }, [title, passages, revision, onFocus]);
  if (!results) return <Loading>{t("Loading recordings…")}</Loading>;
  return <>{results.map(({ passage, source, error }) => <div key={`${passage.source_id}:${passage.section.start_offset}`}>
    {source ? <RecapEvidence source={source} point="" sections={source.sections} timezone={timezone} onCorrect={() => {
      setResults(undefined); onFocus?.({ title, basis: "original", passages: [] }); setRevision((value) => value + 1); onCorrect();
    }} />
      : <ErrorState message={error!} retry={() => { onCorrect(); setRevision((value) => value + 1); }} />}
  </div>)}</>;
}

function CurrentTopicEvidence({ id, point, topic, timezone, onCorrect }: {
  id: string; point: string; topic: string; timezone: string; onCorrect: () => void;
}) {
  const resource = useResource<Source>(`/v1/transcripts/${encodeURIComponent(id)}`);
  if (resource.error) return <ErrorState message={resource.error} retry={resource.refresh} />;
  if (!resource.data) return <Loading>{t("Loading recordings…")}</Loading>;
  return <RecapEvidence source={resource.data} point={point} topic={topic} timezone={timezone} onCorrect={() => { resource.refresh(); onCorrect(); }} />;
}
