import { t } from "./i18n";
import { useId, type ReactNode } from "react";
import { Icon } from "./Icon";

export type TopicBookmark = { id: string; label: string; estimated?: boolean; play?: () => void };
export type RecordingTopic = { key: string; title: string; occurrences: TopicBookmark[] };

export function RecordingTopicList({ topics, selected, onSelect, renderTopic }: {
  topics: RecordingTopic[]; selected?: string; onSelect: (id?: string) => void; renderTopic: (id: string) => ReactNode;
}) {
  const listId = useId();
  return <ol className="recording-topic-bookmarks" aria-label={t("Topics")}>
    {topics.map((topic, index) => {
      const active = topic.occurrences.find((occurrence) => occurrence.id === selected);
      const first = topic.occurrences[0];
      const play = (active ?? topic.occurrences.find((occurrence) => occurrence.play))?.play;
      return <li key={topic.key} data-topic={topic.key}>
        <div className="recording-topic-heading">
          <button className="recording-topic-toggle" type="button" aria-expanded={!!active} aria-controls={`${listId}-${index}`}
            onClick={() => onSelect(active ? undefined : first.id)}>
            <span>{topic.title}</span>
            <Icon name="chevron-right" size={16} />
          </button>
          {play ? <button type="button" className="icon-button recording-topic-play"
            aria-label={`${t("Listen from this topic")} · ${topic.title}`} onClick={play}>
            <Icon name="play" size={17} />
          </button> : null}
        </div>
        <div id={`${listId}-${index}`} hidden={!active}>
          {active && topic.occurrences.length > 1 ? <div className="recording-topic-occurrences" role="group" aria-label={`${topic.title} · ${t("Recordings")}`}>
            {topic.occurrences.map((occurrence, index) => <button type="button" key={occurrence.id}
              aria-pressed={occurrence.id === selected} aria-label={`${t("Recording")} ${index + 1} · ${occurrence.label}`}
              onClick={() => onSelect(occurrence.id)}>
              {occurrence.label}{occurrence.estimated ? ` ${t("· estimated times")}` : ""}
            </button>)}
          </div> : null}
          {active ? renderTopic(active.id) : null}
        </div>
      </li>;
    })}
  </ol>;
}
