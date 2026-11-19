import { describe, expect, it } from "vitest";
import { answerCitations, citationIds } from "./chatCitations";
import { chatPassages, chatSourceGroups } from "./chatSources";
import type { Source } from "./types";

const id = (index: number) => `abcdef00-1234-4567-890a-${String(index).padStart(12, "0")}`;
const base = Date.UTC(2026, 10, 18, 15);
function source(index: number, start: number, end: number, recording?: string): Source {
  return { id: id(index), device_id: "test", state: "done", text: "A source",
    started_at: new Date(base + 1000 * start).toISOString(), ended_at: new Date(base + 1000 * end).toISOString(),
    source_metadata: recording ? { recording_id: recording } : undefined };
}

function timed(index: number, partStart: number, clipStart: number): Source {
  const text = `Passage ${index}`;
  return { ...source(index, partStart, partStart + 120, "recording"), text,
    matches: [{ text, start_offset: 0, end_offset: text.length, timing: "segment" }],
    transcript_turns: [{ text, start_offset: 0, end_offset: text.length, start: clipStart, end: clipStart + 2 }] };
}

describe("chat source groups", () => {
  it("groups overlapping and close parts with a shared recording ID, without losing their original order or citation status", () => {
    const first = source(1, 0, 10, "same");
    const second = source(2, 8, 20, "same");
    const third = source(3, 35, 40, "same");
    const distant = source(4, 56, 65, "same");
    const other = source(5, 6, 9, "different");
    const groups = chatSourceGroups([distant, other, third, second, first, first], `Decision [${second.id}, ${third.id}]. Later [${distant.id}].`);
    expect(groups.map(({ number, entries }) => [number, entries.map(({ source, cited }) => [source.id, cited])])).toEqual([
      [1, [[first.id, false], [second.id, true], [third.id, true]]],
      [2, [[distant.id, true]]], [3, [[other.id, false]]],
    ]);
  });

  it("uses verified passage times when broad part intervals would incorrectly merge distant speech", () => {
    const first = timed(1, 0, 3);
    const later = timed(2, 5, 60);
    const close = timed(3, 6, 1);
    expect(chatSourceGroups([first, later, close], "").map(({ entries }) => entries.map(({ source }) => source.id)))
      .toEqual([[first.id, close.id], [later.id]]);
  });

  it("does not infer shared recordings from time alone, receipt time, or missing capture times", () => {
    const reliable = source(1, 0, 10, "same");
    const unlinked = source(2, 0, 10);
    const receipt = { ...source(3, 0, 10, "same"), timestamp_basis: "receipt_end_estimate" };
    const missing = { ...source(4, 0, 10, "same"), started_at: "" };
    expect(chatSourceGroups([reliable, unlinked, receipt, missing], "")).toHaveLength(4);
  });

  it("keeps historical passages and citation numbering without exposing clip timing for playback", () => {
    const first = timed(1, 0, 1);
    const later = timed(2, 5, 60);
    const [group, second] = chatSourceGroups([first, later], `Read [${first.id}].`, true);
    expect(group.entries[0].passages[0]).toEqual({ from: 0, through: 9, text: "Passage 1" });
    expect(group.cited).toBe(true);
    expect(second.number).toBe(2);
  });
});

describe("chat transcript excerpts", () => {
  const text = "\u{1F4DD} Opening. Plan A. More. Later.";
  const original = { ...source(1, 0, 100), text, transcript_turns: [
    { text: "\u{1F4DD} Opening.", start_offset: 0, end_offset: 10, start: 0, end: 3 },
    { text: "Plan A.", start_offset: 11, end_offset: 18, start: 5, end: 7 },
    { text: "More.", start_offset: 19, end_offset: 24, start: 9, end: 11 },
    { text: "Later.", start_offset: 25, end_offset: 31, start: 40, end: 44 },
  ] };
  function match(start_offset: number, end_offset: number) {
    return { start_offset, end_offset, text: Array.from(text).slice(start_offset, end_offset).join(""), timing: "recording" };
  }

  it("validates Unicode offsets and merges overlapping and nearby timed excerpts without including distant ones", () => {
    const matches = [match(25, 31), match(19, 24), match(11, 18), match(13, 18)];
    expect(chatPassages({ ...original, matches })).toEqual([
      { from: 11, through: 24, text: "Plan A. More.", timing: { start: 5, end: 11 } },
      { from: 25, through: 31, text: "Later.", timing: { start: 40, end: 44 } },
    ]);
  });

  it("keeps the original text when there's no reliable timing and rejects mismatched excerpt evidence", () => {
    const matches = [match(11, 18), { ...match(19, 24), text: "Old text." }, match(25, 300)];
    expect(chatPassages({ ...original, transcript_turns: [], matches }))
      .toEqual([{ from: 11, through: 18, text: "Plan A." }]);
    expect(chatPassages({ ...original, matches: [match(8, 18)], transcript_turns: original.transcript_turns.slice(1) })[0].timing)
      .toBeUndefined();
  });

  it("keeps adjacent audio excerpts separate when text between them has no timing", () => {
    const turns = original.transcript_turns.filter(({ start_offset }) => start_offset !== 11);
    expect(chatPassages({ ...original, transcript_turns: turns, matches: [match(0, 10), match(19, 24)] })).toHaveLength(2);
  });
});

it("only identifies complete groups of known UUIDs in Markdown prose", () => {
  expect(citationIds(`${id(1).toUpperCase()}, ${id(2)}`)).toEqual([id(1), id(2)]);
  expect(citationIds(`${id(1)}, bad`)).toEqual([]);
  expect(answerCitations(`\`[${id(1)}]\` [${id(1)}](https://example.com) [${id(2)}, ${id(9)}] [${id(3).toUpperCase()}]`,
    [id(1), id(2), id(3)])).toEqual([id(3)]);
});
