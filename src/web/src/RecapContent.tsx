import { useEffect, useId, useMemo, useState } from "react";
import { t } from "./i18n";
import { Icon } from "./Icon";
import { CopySummary } from "./CopySummary";
import { recapBrowserTopics, recapParts, recapReadingText, recapTopics, recapTopicText, topicFocus, type RecapBrowserTopic, type RecapFocus } from "./recapItems";
import { RecapSummary, RecapTopic } from "./RecapTopic";
import type { ChatScope, Source } from "./types";
import { RichText } from "./UI";

export function RecapContent({ text, day, sources, timezone, onChat, onCorrect, compact = false, onFocus, emptyMessage }: {
  text: string; day: string; version: string; sources: Source[]; timezone: string;
  onChat?: (scope: ChatScope, question?: string) => void;
  onCorrect: () => void; compact?: boolean;
  onFocus?: (focus?: RecapFocus) => void;
  emptyMessage?: string;
}) {
  const [view, setView] = useState<"day" | "topics">("day");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string>();
  useEffect(() => () => onFocus?.(undefined), [onFocus]);
  const id = useId();
  const { topics, introduction, followups } = useMemo(() => recapTopics(recapParts(text), day), [text, day]);
  const browserTopics = useMemo(() => recapBrowserTopics(topics, sources), [topics, sources]);
  const active = browserTopics.find((topic) => topic.id === selected);
  useEffect(() => {
    if (selected && !active) { setSelected(undefined); onFocus?.(undefined); }
  }, [selected, active, onFocus]);
  const normalized = query.normalize("NFKC").toLocaleLowerCase().trim();
  function changeView(next: "day" | "topics") {
    setView(next); setSelected(undefined); onFocus?.(undefined);
  }
  function explore(topic: RecapBrowserTopic) {
    setView("topics"); setSelected(topic.id);
    onFocus?.(topic.originalPassages ? { title: topic.title, basis: "original", passages: [] } : topicFocus(topic, sources));
  }
  return <div className={`recap-workspace ${compact ? "recap-reading-view" : ""}`}>
    <div className="recap-view-switch" aria-label={t("Recap view")}>
      <button type="button" aria-pressed={view === "day"} onClick={() => changeView("day")}>{t("Day overview")}</button>
      <button type="button" aria-pressed={view === "topics"} onClick={() => changeView("topics")}>{t("Explore topics")}</button>
    </div>
    {view === "day" ? <div className="recap-overview">
      {introduction.length ? <div className="recap-introduction"><RecapSummary text={recapTopicText(introduction)} /></div> : null}
      {topics.map((topic, index) => <RecapTopic key={`${index}:${topic.title}`} {...topic} day={day} sources={sources}
        timezone={timezone} onChat={onChat} onCorrect={onCorrect} compact={false} onExplore={() => explore(browserTopics[index])} />)}
      {!text.trim() ? <p className="caption">{emptyMessage || t("No useful recap content in transcribed recordings yet. Your recordings remain available.")}</p> : null}
      {followups.length ? <details className="recap-recorded-followups"><summary>{t("Follow-ups mentioned in the recap")}</summary>
        <RichText text={followups.map((part) => `- ${recapReadingText(part.text)}`).join("\n")} />
        <p className="caption">{t("Review and manage these in the overview below.")}</p>
      </details> : null}
      <div className="recap-overview-actions">{onChat ? <button className="text-button recap-day-chat" onClick={() => onChat({ kind: "day", day })}>
        <Icon name="chat" size={16} />{t("Chat with this day")}
      </button> : null}
      {text.trim() ? <CopySummary text={recapReadingText(text)} context={t("Day recap")} /> : null}
      </div>
    </div> : active ? <div className="recap-topic-detail">
      <button className="text-button recap-back" onClick={() => { setSelected(undefined); onFocus?.(undefined); }}>
        <Icon name="chevron-left" size={15} />{t("All topics")}
      </button>
      <RecapTopic key={selected} {...active} day={day} sources={sources} timezone={timezone} onChat={onChat}
        onCorrect={onCorrect} onFocus={onFocus} compact={false} expanded />
    </div> : <div className="recap-topic-browser">
      <label className="recap-topic-search" htmlFor={`${id}-search`}>
        <Icon name="search" size={17} />
        <input id={`${id}-search`} type="search" value={query} onChange={(event) => setQuery(event.target.value)}
          placeholder={t("Find a topic in this day")} aria-label={t("Find a topic in this day")} />
      </label>
      <p className="caption">{t("Choose a topic to read its recap, revisit the conversation, and review related actions.")}</p>
      {browserTopics.map((topic) => {
        const summary = recapTopicText(topic.parts);
        const searchable = `${topic.title}\n${summary}\n${topic.originalPassages?.map((passage) => passage.text).join("\n") || ""}`;
        if (normalized && !searchable.normalize("NFKC").toLocaleLowerCase().includes(normalized)) return null;
        return <button key={topic.id} type="button" className="recap-topic-option" onClick={() => explore(topic)}>
          <span><strong>{topic.title}</strong><span>{topic.originalPassages ? t("Original passages") : summary.replace(/\*\*/g, "")}</span></span><Icon name="chevron-right" size={16} />
        </button>;
      })}
      {!browserTopics.some((topic) => `${topic.title}\n${recapTopicText(topic.parts)}\n${topic.originalPassages?.map((passage) => passage.text).join("\n") || ""}`.normalize("NFKC").toLocaleLowerCase().includes(normalized)) ? <p className="recap-topic-empty">
        {normalized ? t("No topics match. Try another word, or ask about this day.") : t("No topic sections in this recap yet. You can still read the day overview and explore its recordings.")}
      </p> : null}
      {onChat ? <button className="text-button" onClick={() => onChat({ kind: "day", day }, query.trim() ? t("Recap {value0} from this day. What happened, what was agreed, and what needs follow-up?", { value0: query.trim() }) : undefined)}>
        <Icon name="chat" size={16} />{t("Ask about this day")}
      </button> : null}
    </div>}
  </div>;
}
