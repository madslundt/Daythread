import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TranscriptTurns, turnsInRange } from "./RecordingTranscript";
import { nextSpeakerSkip, recordingSpeakers, speakerKey, speakerSkipPlan } from "./recordingSpeakers";
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
    expect([...speakers.values()].map((s) => s.letter ?? s.name)).toEqual(["A", "B", "Rae"]);
    expect(speakerKey(firstRae)).toBe(speakerKey(secondRae));
    expect(speakers.get(speakerKey(firstRae)!)?.color).toBe(recordingSpeakers([secondRae]).get(speakerKey(secondRae)!)?.color);
  });

  it("keeps topic labels from the full transcript and never gives unattributed speech a speaker identity", () => {
    const allTurns = [turn({ speaker_number: 1 }), turn({ speaker_number: 2, start_offset: 8, end_offset: 15, start: 4, end: 5 }),
      turn({ attribution: "unknown", start_offset: 16, end_offset: 23 })];
    const passage = turnsInRange("Speech. Speech. Speech.", allTurns, { start_offset: 10, end_offset: 23 })!;
    expect(passage[0].start).toBeUndefined();
    const html = renderToStaticMarkup(<TranscriptTurns text="eech. Speech." turns={passage} allTurns={allTurns} />);
    expect(html).toContain('data-speaker-color="1">Speaker B</span>');
    expect(html).toContain('recording-speaker-label">Unassigned speech</span>');
    expect(html).not.toContain('caption recording-speaker-label" data-speaker-color="0"');
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
