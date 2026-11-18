import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TranscriptTurns, turnsInRange } from "./RecordingTranscript";
import { initialSpeakerPassage, nextSpeakerSkip, recordingSpeakers, speakerIdentity, speakerKey, speakerPassages, speakersInPassage, speakerSkipPlan } from "./recordingSpeakers";
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

  it("keeps topic labels from the full transcript and never gives unattributed speech a speaker identity", () => {
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
    const selected = turn({ ...speaker, start: 6, end: 7, start_offset: 16 });
    const untimed = turn({ ...speaker, start_offset: 24 });
    const last = turn({ ...speaker, start: 11, part_end: 14, start_offset: 32 });
    const missingAudio = turn({ ...speaker, start: 20, end: 21, part_end: 20, start_offset: 40 });
    const passages = speakerPassages([last, untimed, selected, other, first, missingAudio], speakerIdentity(first)!);
    expect(passages.map(({ offset, start, end }) => [offset, start, end])).toEqual([[0, 0, 2], [16, 6, 7], [32, 11, 14]]);
    expect(initialSpeakerPassage(passages, [selected, last])).toBe(1);
    expect(initialSpeakerPassage(passages, [selected, last], last)).toBe(2);
    expect(initialSpeakerPassage(passages, [selected, last], untimed)).toBe(0);
    expect(initialSpeakerPassage(passages, [untimed])).toBe(0);
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
