import { t } from "./i18n";
import { useCallback, useEffect, useId, useRef, useState } from "react";
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
  displayTitle?: string; displayParts?: RecapPart[]; inline?: boolean;
  onCorrect: () => void; compact: boolean;
  day?: string; expanded?: boolean; onExplore?: () => void;
  originalPassages?: OriginalRecapPassage[];
  onFocus?: (focus?: RecapFocus) => void;
  onChat?: (scope: ChatScope, question?: string) => void;
};

export function RecapTopic({ title, parts, displayTitle = title, displayParts = parts, inline = false, sources, timezone, onCorrect, compact, day, onChat, expanded = false, onExplore, originalPassages, onFocus }: Props) {
  const [showEvidence, setShowEvidence] = useState(false);
  const passageId = useId();
  const text = recapTopicText(parts);
  const plain = text.replace(/\*\*/g, "");
  const heading = title.replace(/\*\*/g, "");
  const displayText = recapTopicText(displayParts);
  const displayHeading = displayTitle.replace(/\*\*/g, "");
  const reportFocus = useCallback((focus?: RecapFocus) => onFocus?.(focus ? { ...focus, displayTitle: displayHeading } : undefined), [onFocus, displayHeading]);
  const related = itemSources(`**${title}**\n${parts.map((part) => part.text).join("\n")}`, sources);
  const evidence = related.sources.length ? related.sources.map((source) => (
    <CurrentTopicEvidence key={source.id} id={source.id} point={plain} topic={heading} timezone={timezone} onCorrect={onCorrect} />
  )) : <p className="caption">{t("No linked or matching recordings for this topic.")}</p>;
  const body = <>
    {displayText ? inline ? <div className="recap-topic-summary"><RichText text={displayText} /></div> : <RecapSummary text={displayText} /> : null}
    {!onExplore ? <div className="recap-topic-actions">
      {expanded && onChat && day ? <button className="text-button" onClick={() => onChat({ kind: "day", day },
        t("Recap {value0} from this day. What happened, what was agreed, and what needs follow-up?", { value0: heading }))}>
        <Icon name="chat" size={16} />{t("Chat about this topic")}
      </button> : null}
      {displayText ? <CopySummary text={`${displayHeading}\n\n${displayText.replace(/\*\*/g, "")}`} context={displayHeading} /> : null}
    </div> : null}
    {onExplore ? null : <div className="recap-topic-evidence">
      <button type="button" className="text-button" aria-expanded={showEvidence} aria-controls={passageId}
        onClick={() => setShowEvidence((value) => !value)}>{showEvidence ? t("Hide original passages") : t("Show original passages")}</button>
      <div id={passageId} hidden={!showEvidence}>
        {originalPassages ? <OriginalTopicEvidence title={title} passages={originalPassages} visible={showEvidence}
          timezone={timezone} onCorrect={onCorrect} onFocus={reportFocus} /> : showEvidence ? evidence : null}
      </div>
    </div>}
  </>;
  return compact ? <details className="recap-group">
    <summary>
      <span className="recap-group-overview"><span><InlineText text={displayTitle} /></span><span className="recap-group-summary">{displayText.replace(/\*\*/g, "")}</span></span>
      <Icon name="chevron-right" size={15} />
    </summary>
    <div className="recap-group-content">{body}</div>
  </details> : <section className={`recap-topic${inline ? " recap-topic-inline" : ""}`}>
    {!inline ? <div className="recap-section-heading"><h3><InlineText text={displayTitle} /></h3>
      {onExplore ? <button className="text-button" aria-label={t("Explore topic: {value0}", { value0: heading })} onClick={onExplore}>{t("Explore topic")}<Icon name="chevron-right" size={15} /></button> : null}
    </div> : null}
    {body}
  </section>;
}

export function RecapSummary({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(text.length > 240 || text.includes("\n\n"));
  const content = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    setExpanded(false);
    const prose = content.current?.firstElementChild as HTMLElement | null;
    if (!prose) return;
    const measure = () => {
      const lineHeight = Number.parseFloat(window.getComputedStyle(prose).lineHeight);
      if (Number.isFinite(lineHeight) && lineHeight > 0) setOverflows(prose.scrollHeight > lineHeight * 3 + 1);
    };
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(prose);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [text]);
  return <>
    <div id={id} ref={content} className={`recap-topic-summary${expanded ? "" : " is-collapsed"}`}><RichText text={text} /></div>
    {overflows ? <button type="button" className="text-button recap-summary-toggle" aria-expanded={expanded} aria-controls={id}
      onClick={() => setExpanded((value) => !value)}>{expanded ? t("Show less") : t("Read more")}</button> : null}
  </>;
}

type OriginalEvidenceResult = { passage: OriginalRecapPassage; source?: Source; error?: string };

function OriginalTopicEvidence({ title, passages, timezone, onCorrect, onFocus, visible }: {
  title: string; passages: OriginalRecapPassage[]; timezone: string; onCorrect: () => void; visible: boolean;
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
  if (!visible) return null;
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
