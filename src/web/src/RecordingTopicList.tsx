import { t } from "./i18n";
import { useId, type ReactNode } from "react";

export type TopicBookmark = { id: string; label: string; estimated?: boolean };
export type RecordingTopic = { key: string; title: string; occurrences: TopicBookmark[] };

export function RecordingTopicList({ topics, selected, onSelect, renderTopic }: {
  topics: RecordingTopic[]; selected?: string; onSelect: (id?: string) => void; renderTopic: (id: string) => ReactNode;
}) {
  const listId = useId();
  return <ol className="recording-topic-bookmarks" aria-label={t("Topics")}>
    {topics.map((topic, index) => {
      const active = topic.occurrences.find((occurrence) => occurrence.id === selected);
      const first = topic.occurrences[0];
      return <li key={topic.key} data-topic={topic.key}>
        <button className="recording-topic-toggle" type="button" aria-expanded={!!active} aria-controls={`${listId}-${index}`}
          onClick={() => onSelect(active ? undefined : first.id)}>
          <span className="recording-topic-time">{first.label}</span>
          <span>{topic.title}<small className="recording-topic-count caption">
            {topic.occurrences.length} {topic.occurrences.length === 1 ? t("recording") : t("recordings")}
            {first.estimated ? ` ${t("· estimated times")}` : ""}
          </small></span>
        </button>
        {topic.occurrences.length > 1 ? <div className="recording-topic-occurrences" role="group" aria-label={`${topic.title} · ${t("Recordings")}`}>
          {topic.occurrences.map((occurrence, index) => <button type="button" key={occurrence.id}
            aria-pressed={occurrence.id === selected} aria-label={`${t("Recording")} ${index + 1} · ${occurrence.label}`}
            onClick={() => onSelect(occurrence.id)}>
            {occurrence.label}{occurrence.estimated ? ` ${t("· estimated times")}` : ""}
          </button>)}
        </div> : null}
        <div id={`${listId}-${index}`} hidden={!active}>{active ? renderTopic(active.id) : null}</div>
      </li>;
    })}
  </ol>;
}
