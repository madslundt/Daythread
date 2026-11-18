import type { TranscriptTurn } from "./types";

export type Speaker = { key: string; name?: string; letter?: string; color: number };
export type AudioInterval = { start: number; end: number };

function assignedName(turn: TranscriptTurn) {
  const name = turn.speaker_name?.trim();
  // Older exports stored their generated labels in the name field.
  return name && (turn.speaker_id || !/^Speaker (?:\d+|[A-Z]+)$/.test(name)) ? name : undefined;
}

export function speakerKey(turn: TranscriptTurn): string | undefined {
  if (turn.attribution === "unknown" || turn.attribution === "overlap") return;
  const name = assignedName(turn);
  if (name) return `name:${name}`;
  if (turn.speaker_id) return `id:${turn.speaker_id}`;
  const label = turn.speaker_label?.trim() || turn.speaker_name?.trim();
  const identity = turn.speaker_number != null ? `number:${turn.speaker_number}` : label ? `label:${label}` : undefined;
  if (identity) return JSON.stringify([turn.source_part ?? "", identity]);
}

function letterFor(index: number): string {
  let letter = "";
  for (; index >= 0; index = Math.floor(index / 26) - 1) letter = String.fromCharCode(65 + index % 26) + letter;
  return letter;
}

export function recordingSpeakers(turns: readonly TranscriptTurn[]) {
  const speakers = new Map<string, Speaker>();
  let anonymous = 0;
  for (const turn of turns) {
    const key = speakerKey(turn);
    if (!key || speakers.has(key) || !turn.text.trim()) continue;
    const name = assignedName(turn);
    let color = 0;
    if (name) {
      for (const char of name) color = (color * 31 + char.codePointAt(0)!) % 10;
      speakers.set(key, { key, name, color });
    } else {
      const index = anonymous++;
      speakers.set(key, { key, letter: letterFor(index), color: index % 10 });
    }
  }
  return speakers;
}

export function turnIsExcluded(turn: TranscriptTurn, excluded: ReadonlySet<string>) {
  const key = speakerKey(turn);
  return key !== undefined && excluded.has(key);
}

function validStart(turn: TranscriptTurn): turn is TranscriptTurn & { start: number } {
  return turn.start != null && Number.isFinite(turn.start) && turn.start >= 0;
}

function timed(turn: TranscriptTurn): turn is TranscriptTurn & AudioInterval {
  return validStart(turn) && turn.end != null && Number.isFinite(turn.end) && turn.end > turn.start;
}

function mergeIntervals(spans: AudioInterval[]) {
  const merged: AudioInterval[] = [];
  for (const span of spans.sort((a, b) => a.start - b.start)) {
    if (span.start >= span.end) continue;
    const last = merged.at(-1);
    if (last && last.end >= span.start) last.end = Math.max(last.end, span.end);
    else merged.push({ ...span });
  }
  return merged;
}

export function speakerSkipPlan(turns: readonly TranscriptTurn[], excluded: ReadonlySet<string>) {
  if (!excluded.size) return { intervals: [], incomplete: false };
  const hidden = turns.filter((turn) => turn.text.trim() && turnIsExcluded(turn, excluded));
  const candidates = mergeIntervals(hidden.filter(timed));
  if (!candidates.length) return { intervals: [], incomplete: hidden.length > 0 };
  const kept: AudioInterval[] = [];
  const parts = new Map<string, TranscriptTurn[]>();
  for (const turn of turns) {
    const part = turn.source_part ?? "";
    if (!parts.has(part)) parts.set(part, []);
    parts.get(part)!.push(turn);
  }
  for (const part of parts.values()) {
    // Untimed speech protects the region between adjacent evidence, within its audio part.
    let nextEnd = part[0]?.part_end ?? Infinity;
    const nextEnds: number[] = [];
    for (let i = part.length - 1; i >= 0; i--) {
      const turn = part[i];
      nextEnds[i] = nextEnd;
      if (timed(turn)) nextEnd = turn.end;
    }
    let previousStart = part[0]?.part_start ?? -Infinity;
    part.forEach((turn, index) => {
      if (!turnIsExcluded(turn, excluded) && /[\p{L}\p{N}]/u.test(turn.text)) {
        if (timed(turn)) kept.push(turn);
        else kept.push({ start: validStart(turn) ? turn.start : previousStart, end: nextEnds[index] });
      }
      if (validStart(turn)) previousStart = turn.start;
    });
  }
  const blockers = mergeIntervals(kept);
  const intervals: AudioInterval[] = [];
  let block = 0;
  for (const candidate of candidates) {
    let start = candidate.start;
    while (block < blockers.length && blockers[block].end <= start) block++;
    while (block < blockers.length && blockers[block].start < candidate.end) {
      const kept = blockers[block];
      if (kept.start > start) intervals.push({ start, end: kept.start });
      start = Math.max(start, kept.end);
      if (start >= candidate.end) break;
      block++;
    }
    if (start < candidate.end) intervals.push({ start, end: candidate.end });
  }
  return { intervals, incomplete: hidden.some((turn) => !timed(turn)) ||
    intervals.length !== candidates.length || intervals.some((span, i) => span.start !== candidates[i].start || span.end !== candidates[i].end) };
}

/** Find the containing or next interval without scanning a long recording on every tick. */
export function nextSpeakerSkip(intervals: readonly AudioInterval[], position: number) {
  let left = 0, right = intervals.length;
  while (left < right) {
    const middle = (left + right) >>> 1;
    if (intervals[middle].end <= position) left = middle + 1;
    else right = middle;
  }
  return intervals[left];
}
