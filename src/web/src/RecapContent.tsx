import { useEffect, useId, useMemo, useState } from "react";
import { interfaceLanguage, source, t } from "./i18n";
import { Icon } from "./Icon";
import { CopySummary } from "./CopySummary";
import { originalRecapText, originalTopicTranslation, recapBrowserTopics, recapParts, recapReadingText, recapTopics, recapTopicText, topicFocus, type RecapBrowserTopic, type RecapFocus, type RecapTopicGroup } from "./recapItems";
import { RecapTopic } from "./RecapTopic";
import { recapTranslationDisplay } from "./recapTranslationDisplay";
import type { SummaryTranslationStructure } from "./transcriptTranslationRequests";
import type { ChatScope, Source } from "./types";
import { InlineText } from "./UI";
import { DisplayTranslationStatus, useDisplayTranslations, type DisplayTranslationInput } from "./displayTranslations";

type Props = {
  text: string; day: string; version: string; sources: Source[]; timezone: string;
  translatedText?: string; translationStructure?: SummaryTranslationStructure;
  onChat?: (scope: ChatScope, question?: string) => void;
  onCorrect: () => void; compact?: boolean;
  onFocus?: (focus?: RecapFocus) => void;
  emptyMessage?: string;
};

type TopicRow = RecapBrowserTopic & { displayTitle: string; displayParts: RecapTopicGroup["parts"]; followups?: boolean };

export function RecapContent({ text, translatedText, translationStructure, day, version, sources, timezone, onChat, onCorrect, compact = false, onFocus, emptyMessage }: Props) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string>();
  useEffect(() => () => onFocus?.(undefined), [onFocus]);
  const id = useId();
  const language = interfaceLanguage();
  const display = useMemo(() => translatedText === undefined ? undefined
    : recapTranslationDisplay(text, translatedText, translationStructure), [text, translatedText, translationStructure]);
  const canonicalRows = useMemo(() => {
    const parts = recapParts(text);
    const original = recapTopics(parts, day);
    const translated = display === undefined ? original : recapTopics(parts, day, recapParts(text, display));
    const topics: TopicRow[] = recapBrowserTopics(original.topics, sources).map((topic, index) => ({ ...topic,
      displayTitle: translated.topics[index]?.title || topic.title, displayParts: translated.topics[index]?.parts || topic.parts }));
    if (original.introduction.length) topics.unshift({ id: "recap:introduction", title: source("Day recap"), parts: original.introduction,
      displayTitle: t("Day recap"), displayParts: translated.introduction });
    if (original.followups.length) topics.push({ id: "recap:followups", title: source("Follow-ups mentioned in the recap"), parts: original.followups,
      displayTitle: t("Follow-ups mentioned in the recap"), displayParts: translated.followups, followups: true });
    return topics;
  }, [text, display, day, sources, language]);
  const labels = useDisplayTranslations(canonicalRows.map(originalTopicTranslation).filter((item): item is DisplayTranslationInput => !!item), `${day}:${version}`);
  const rows = useMemo(() => canonicalRows.map((topic) => {
    const input = originalTopicTranslation(topic);
    return input ? { ...topic, displayTitle: labels.title(input) } : topic;
  }), [canonicalRows, labels.title]);
  const active = rows.find((topic) => topic.id === selected);
  useEffect(() => {
    if (selected && !active) { setSelected(undefined); onFocus?.(undefined); }
  }, [selected, active, onFocus]);
  useEffect(() => {
    if (active && !active.originalPassages) onFocus?.({ ...topicFocus(active, sources), displayTitle: active.displayTitle });
  }, [active, sources, onFocus]);
  const normalized = query.normalize("NFKC").toLocaleLowerCase().trim();
  const visible = rows.filter((topic) => `${topic.displayTitle}\n${recapTopicText(topic.displayParts)}\n${topic.title}\n${recapTopicText(topic.parts)}\n${topic.originalPassages?.map((passage) => passage.text).join("\n") || ""}`
    .normalize("NFKC").toLocaleLowerCase().includes(normalized));
  function toggle(topic: RecapBrowserTopic) {
    const next = selected === topic.id ? undefined : topic.id;
    setSelected(next);
    const displayTitle = rows.find((row) => row.id === topic.id)?.displayTitle;
    onFocus?.(!next ? undefined : { ...(topic.originalPassages ? { title: topic.title, basis: "original" as const, passages: [] } : topicFocus(topic, sources)), displayTitle });
  }
  return <div className={`recap-workspace ${compact ? "recap-reading-view" : ""}`}>
    {translatedText !== undefined && display === undefined ? <p className="caption recap-translation-warning" role="status">
      {t("Update this translation to keep the recap’s topics and layout.")}
    </p> : null}
    {!text.trim() ? <p className="caption">{emptyMessage || t("No useful recap content in transcribed recordings yet. Your recordings remain available.")}</p> : null}
    <div className="recap-topic-browser">
      <label className="recap-topic-search" htmlFor={`${id}-search`}>
        <Icon name="search" size={17} />
        <input id={`${id}-search`} type="search" value={query} onChange={(event) => {
          setQuery(event.target.value); setSelected(undefined); onFocus?.(undefined);
        }} placeholder={t("Find a topic in this day")} aria-label={t("Find a topic in this day")} />
      </label>
      <DisplayTranslationStatus value={labels} />
      <div className="recap-topic-list">
        {visible.map((topic) => <RecapTopicRow key={topic.id} topic={topic} expanded={selected === topic.id} onToggle={() => toggle(topic)}
          day={day} sources={sources} timezone={timezone} onChat={onChat} onCorrect={onCorrect} onFocus={onFocus} />)}
      </div>
      {!visible.length && normalized ? <p className="recap-topic-empty">{t("No topics match. Try another word, or ask about this day.")}</p> : null}
      <div className="recap-overview-actions">
        {onChat ? <button className="text-button" onClick={() => onChat({ kind: "day", day }, query.trim()
          ? t("Recap {value0} from this day. What happened, what was agreed, and what needs follow-up?", { value0: query.trim() }) : undefined)}>
          <Icon name="chat" size={16} />{t("Ask about this day")}
        </button> : null}
        {text.trim() ? <CopySummary text={recapReadingText(display ?? text)} context={t("Day recap")} /> : null}
      </div>
    </div>
  </div>;
}

// A controlled row keeps its siblings visible while one canonical topic owns the focus.
export function RecapTopicRow({ topic, expanded, onToggle, ...props }: {
  topic: TopicRow; expanded: boolean; onToggle: () => void;
} & Pick<Props, "day" | "sources" | "timezone" | "onChat" | "onCorrect" | "onFocus">) {
  const id = useId();
  const preview = (recapTopicText(topic.displayParts) || originalRecapText(topic.originalPassages, props.sources)).replace(/\*\*/g, "");
  return <article className="recap-topic-row" data-topic-id={topic.id}>
    <h3><button type="button" className="recap-topic-option" aria-expanded={expanded} aria-controls={id} onClick={onToggle}>
      <span><strong><InlineText text={topic.displayTitle} /></strong>{!expanded ? <>
        {topic.originalPassages ? <small className="caption">{t("Original passages")}</small> : null}
        <span>{preview}</span>
      </> : null}</span>
      <Icon name="chevron-right" size={16} />
    </button></h3>
    <div id={id} hidden={!expanded}>
      {expanded ? <>
        <RecapTopic {...topic} {...props} compact={false} expanded inline />
        {topic.followups ? <p className="caption">{t("Review and manage these in the overview below.")}</p> : null}
      </> : null}
    </div>
  </article>;
}
