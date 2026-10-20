import { describe, expect, it } from "vitest";
import { groupTopicRuns } from "./topicOccurrences";

describe("topic occurrences within a recording", () => {
  it("counts consecutive passages as one occurrence and keeps later returns selectable", () => {
    const topics = groupTopicRuns(["Work", "Work", "Garden", "Work"], (title) => title);
    expect(topics.map((topic) => [topic.title, topic.occurrences.map((run) => run.length)]))
      .toEqual([["Work", [2, 1]], ["Garden", [1]]]);
  });
  it("keeps gaps separate even when the next available passage has the same title", () => {
    const sections = [{ title: "Work", start: 0, end: 10 }, { title: "Work", start: 20, end: 30 }];
    expect(groupTopicRuns(sections, (span) => span.title, (a, b) => a.end === b.start)[0].occurrences).toHaveLength(2);
  });
  it("normalizes case and spacing while preserving meaningful symbols and distinct subjects", () => {
    const topics = groupTopicRuns([" Work ", "WORK", "C++", "C#", "Work planning"], (title) => title);
    expect(topics).toHaveLength(4);
    expect(topics[0].occurrences[0]).toHaveLength(2);
  });
  it("does not share groups across recordings", () => {
    const first = groupTopicRuns(["Work"], (title) => title);
    const second = groupTopicRuns(["Work", "Work"], (title) => title);
    expect(first[0].occurrences[0]).toHaveLength(1);
    expect(second[0].occurrences[0]).toHaveLength(2);
  });
});
