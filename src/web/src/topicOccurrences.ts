// Keep meaningful punctuation (for example C++ versus C#) distinct.
export function topicTitleKey(title: string): string {
  return title.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
}

export type TopicRuns<T> = { key: string; title: string; occurrences: T[][] };

// Call separately for each recording. An intervening topic always starts a new occurrence.
export function groupTopicRuns<T>(items: T[], title: (item: T) => string,
  adjacent: (previous: T, next: T) => boolean = () => true): TopicRuns<T>[] {
  const topics = new Map<string, TopicRuns<T>>();
  let previous: { topic: TopicRuns<T>; item: T } | undefined;
  for (const [index, item] of items.entries()) {
    const key = topicTitleKey(title(item)) || `untitled:${index}`;
    let topic = topics.get(key);
    if (!topic) { topic = { key, title: title(item), occurrences: [] }; topics.set(key, topic); }
    if (previous?.topic === topic && adjacent(previous.item, item)) topic.occurrences.at(-1)!.push(item);
    else topic.occurrences.push([item]);
    previous = { topic, item };
  }
  return [...topics.values()];
}
