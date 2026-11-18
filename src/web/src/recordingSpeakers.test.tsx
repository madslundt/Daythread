import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TranscriptTurns, turnsInRange } from "./RecordingTranscript";
import { initialSpeakerPassage, nextSpeakerSkip, recordingSpeakers, speakerIdentity, speakerKey, speakerPassages, speakersInPassage, speakerSkipPlan, turnsInPlaybackOrder } from "./recordingSpeakers";
import type { TranscriptTurn } from "./types";

const turn = (fields: Partial<TranscriptTurn>): TranscriptTurn => ({ text: "Speech.", start_offset: 0, end_offset: 7, ...fields });

describe("speaker filters", () => {
  it("preserves returning speakers, scopes part-local identifiers and groups identical names", () => {
    const a = turn({ speaker_number: 1, source_part: "one" });
    const b = turn({ speaker_label: "Speaker 1", source_part: "two" });
    const firstRae = turn({ speaker_id: "id1", speaker_name: "Rae" });
    const secondRae = turn({ speaker_id: "id2", speaker_name: "Rae" });
    const turns = [a, b, a, firstRae, secondRae, turn({ attribution: "overlap" }), turn({ attribution: "unknown" })];
    const speakers = recordingSpeakers(turns);
    expect([...speakers.values()].map((s) => s.name ?? s.number)).toEqual([1, 2, "Rae"]);
    expect(speakerKey(firstRae)).toBe(speakerKey(secondRae));
    expect(speakers.get(speakerKey(firstRae)!)?.color).toBe(recordingSpeakers([secondRae]).get(speakerKey(secondRae)!)?.color);
  });

  it("keeps recording-wide speaker numbering without showing absent speakers in a topic", () => {
    const allTurns = [turn({ speaker_number: 1 }), turn({ speaker_number: 2, start_offset: 8, end_offset: 15, start: 4, end: 5 }),
      turn({ attribution: "unknown", start_offset: 16, end_offset: 23 })];
    const passage = turnsInRange("Speech. Speech. Speech.", allTurns, { start_offset: 10, end_offset: 23 })!;
    expect(passage[0].start).toBeUndefined();
    const html = renderToStaticMarkup(<TranscriptTurns text="eech. Speech." turns={passage} allTurns={allTurns} />);
    expect(html).toContain('data-speaker-color="1">Speaker 2</span>');
    expect(html).toContain('recording-speaker-label">Unassigned speech</span>');
    expect(html).not.toContain('caption recording-speaker-label" data-speaker-color="0"');
    expect(html).not.toContain('aria-pressed="true">Speaker 1');
    expect(html).toContain('aria-pressed="true">Speaker 2');
  });

  it("interleaves parallel tracks chronologically without rearranging stored text or guessing untimed positions", () => {
    const first = turn({ text: "Hello", source_part: "one", speaker_number: 4, start: 1, end: 2, end_offset: 5 });
    const last = turn({ ...first, text: "Again", start: 5, end: 6, start_offset: 6, end_offset: 11 });
    const untimed = turn({ text: "Untimed", start_offset: 12, end_offset: 19 });
    const middle = turn({ text: "Reply", source_part: "two", speaker_number: 1, start: 3, start_offset: 20, end_offset: 25 });
    const equal = turn({ ...middle, text: "Also", start_offset: 26, end_offset: 30 });
    const trackOrder = [first, last, untimed, middle, equal];
    expect(turnsInPlaybackOrder(trackOrder)).toEqual([first, middle, untimed, equal, last]);
    expect(trackOrder[1]).toBe(last);
    expect(turnsInRange("Hello Again Untimed Reply Also", trackOrder, { start_offset: 0, end_offset: 25 })).toEqual([
      first, middle, untimed, last,
    ]);
    const html = renderToStaticMarkup(<TranscriptTurns text="Hello Again Untimed Reply Also" turns={trackOrder} />);
    expect(html.indexOf(">Hello<")).toBeLessThan(html.indexOf(">Reply<"));
    expect(html.indexOf(">Reply<")).toBeLessThan(html.indexOf(">Again<"));
    expect(html.match(/recording-speaker-label" data-speaker-color="\d">Speaker \d<\/span>/g)?.map((label) => label.match(/Speaker \d/)?.[0]))
      .toEqual(["Speaker 1", "Speaker 2", "Speaker 2", "Speaker 1"]);
  });

  it("reuses a renamed speaker and their color only in topics where they speak", () => {
    const edit = { kind: "transcript" as const, scope_id: "part", id: "a", version: "v1" };
    const named = turn({ text: "Opening", end_offset: 7, speaker_edit: edit, speaker_name: "Mads", start: 0 });
    const other = turn({ text: "Reply", start_offset: 8, end_offset: 13, speaker_number: 2, start: 3 });
    const again = turn({ ...named, text: "Again", start_offset: 14, end_offset: 19, start: 6 });
    const allTurns = [named, other, again];
    const render = (turns: TranscriptTurn[]) => renderToStaticMarkup(<TranscriptTurns text="Opening Reply Again" turns={turns} allTurns={allTurns} />);
    const color = recordingSpeakers(allTurns).get(speakerKey(named)!)!.color;
    for (const html of [render([named, other]), render([again])]) {
      expect(html).toContain(`data-speaker-color="${color}" aria-pressed="true">Mads`);
      expect(html).toContain('aria-label="Edit speaker: Mads"');
      expect(html).not.toContain('aria-pressed="true">Speaker 1');
    }
    expect(render([named, other])).toContain('aria-pressed="true">Speaker 2');
    expect(render([again])).not.toContain('aria-pressed="true">Speaker 2');
    const otherTopic = render([other]);
    expect(otherTopic).toContain('data-speaker-color="1" aria-pressed="true">Speaker 2');
    expect(otherTopic).not.toContain("Mads");
    expect(otherTopic).not.toContain(">Opening<");
    expect(otherTopic).not.toContain(">Again<");
  });

  it("keeps edit targets separate when names share a color and preserves numbers on rename", () => {
    const edit = (id: string) => ({ kind: "transcript" as const, scope_id: "part", id, version: "v1" });
    const a = turn({ speaker_number: 1, speaker_edit: edit("a") });
    const b = turn({ speaker_number: 2, speaker_edit: edit("b"), start_offset: 8, end_offset: 15 });
    const c = turn({ speaker_number: 3, speaker_edit: edit("c"), start_offset: 16, end_offset: 23 });
    const named = [a, b].map((item) => ({ ...item, speaker_name: "Mads", speaker_name_is_custom: true }));
    const turns = [...named, c, { ...named[0], start_offset: 24, end_offset: 31 }];
    const speakers = recordingSpeakers(turns);
    expect(speakers.size).toBe(2);
    expect(speakers.get(speakerKey(c)!)?.number).toBe(3);
    expect(speakerKey(named[0])).toBe(speakerKey(named[1]));
    expect(speakers.get(speakerKey(named[0])!)?.color).toBe(recordingSpeakers([named[1]]).get(speakerKey(named[1])!)?.color);
    expect(speakersInPassage(turns, speakers).map(({ edit }) => edit.id)).toEqual(["a", "b", "c"]);
    const html = renderToStaticMarkup(<TranscriptTurns text="Speech." turns={turns} />);
    expect(html.match(/aria-label="Edit speaker: Mads"/g)).toHaveLength(4);
    expect(html.match(/aria-label="Edit speaker: Speaker 3"/g)).toHaveLength(2);
    expect(html).not.toContain("<dialog");
  });

  it("renders unclassified speech as a clickable colored label without treating overlap as a speaker", () => {
    const unclassified = turn({ attribution: "unknown", speaker_edit: {
      kind: "transcript", scope_id: "part", id: "unclassified", version: "v1",
    } });
    const overlap = turn({ attribution: "overlap", start_offset: 8, end_offset: 15 });
    const html = renderToStaticMarkup(<TranscriptTurns text="Speech. Speech." turns={[unclassified, overlap]} />);
    expect(html).toContain('aria-pressed="true">Speaker 1');
    expect(html).toContain('class="recording-speaker-label recording-speaker-rename" data-speaker-color="0" aria-label="Edit speaker: Speaker 1">Speaker 1');
    expect(html).toContain("Overlapping speech");
    expect(html).not.toContain("Unassigned speech");
  });

  it("starts at the first timed turn in a topic or the clicked row and navigates only that speaker's passages", () => {
    const speaker = { speaker_name: "Rae", speaker_edit: { kind: "transcript" as const, scope_id: "one", id: "a", version: "v1" } };
    const first = turn({ ...speaker, start: 0, end: 2 });
    const other = turn({ ...speaker, speaker_edit: { ...speaker.speaker_edit, scope_id: "two" }, start: 3, end: 5, start_offset: 8 });
    const selected = turn({ ...speaker, start: 6, end: 7, start_offset: 38 });
    const untimed = turn({ ...speaker, start_offset: 24 });
    const last = turn({ ...speaker, start: 11, part_end: 14, start_offset: 32 });
    const missingAudio = turn({ ...speaker, start: 20, end: 21, part_end: 20, start_offset: 40 });
    const passages = speakerPassages([last, untimed, selected, other, first, missingAudio], speakerIdentity(first)!);
    expect(passages.map(({ offset, start, end }) => [offset, start, end])).toEqual([[0, 0, 2], [38, 6, 7], [32, 11, 14]]);
    expect(initialSpeakerPassage(passages, [selected, last])).toBe(1);
    expect(initialSpeakerPassage(passages, [selected, last], last)).toBe(2);
    expect(initialSpeakerPassage(passages, [selected, last], untimed)).toBe(-1);
    expect(initialSpeakerPassage(passages, [untimed])).toBe(0);
  });

  it("loads the matching original clip when a topic clips the start of a later turn", () => {
    const turns = [turn({ start: 0, end: 2, speaker_number: 1 }),
      turn({ start: 3, end: 4, start_offset: 8, end_offset: 15, speaker_number: 2 }),
      turn({ start: 6, end: 8, start_offset: 16, end_offset: 23, speaker_number: 1 })];
    const shown = turnsInRange("Speech. Speech. Speech.", turns, { start_offset: 18, end_offset: 23 })!;
    const passages = speakerPassages(turns, speakerIdentity(turns[0])!);
    expect(shown[0].start).toBeUndefined();
    expect(initialSpeakerPassage(passages, shown)).toBe(1);
    expect(initialSpeakerPassage(passages, shown, shown[0])).toBe(1);
  });

  it("does not play into the next speaker when the selected turn has no end time", () => {
    const first = turn({ start: 1, speaker_number: 1 });
    const last = turn({ start: 7, end: 8, speaker_number: 1, start_offset: 8 });
    const reply = turn({ start: 3, end: 5, speaker_number: 2, start_offset: 16 });
    expect(speakerPassages([first, last, reply], speakerIdentity(first)!).map(({ start, end }) => [start, end]))
      .toEqual([[1, 3], [7, 8]]);
  });

  it("skips timed speech without cutting through kept or overlapping audio", () => {
    const a = turn({ speaker_number: 1, start: 1, end: 7 });
    const timedOverlap = turn({ attribution: "overlap", start: 3, end: 4 });
    const kept = turn({ speaker_number: 2, start: 6, end: 8 });
    const missingEnd = turn({ speaker_number: 1, start: 9 });
    const plan = speakerSkipPlan([a, timedOverlap, kept, missingEnd], new Set([speakerKey(a)!]));
    expect(plan).toEqual({ intervals: [{ start: 1, end: 3 }, { start: 4, end: 6 }], incomplete: true });
    expect(nextSpeakerSkip(plan.intervals, 3)).toEqual({ start: 4, end: 6 });
    expect(nextSpeakerSkip(plan.intervals, 6)).toBeUndefined();
  });

  it("conservatively protects untimed speech without blocking unrelated joined parts", () => {
    const a = turn({ speaker_name: "Rae", start: 2, end: 3, source_part: "one", part_start: 0, part_end: 5 });
    const untimed = turn({ attribution: "unknown", source_part: "one", part_start: 0, part_end: 5 });
    const two = turn({ speaker_name: "Rae", start: 8, end: 10, source_part: "two", part_start: 5, part_end: 12 });
    expect(speakerSkipPlan([a, untimed, two], new Set([speakerKey(a)!]))).toEqual({
      intervals: [{ start: 8, end: 10 }], incomplete: true,
    });
  });
});
