import type { TranscriptTurn } from "./types";

export type Speaker = { key: string; name?: string; number: number; color: number };
export type AudioInterval = { start: number; end: number };

function assignedName(turn: TranscriptTurn) {
  const name = turn.speaker_name?.trim();
  // Older exports stored their generated labels in the name field.
  return name && (turn.speaker_id || turn.speaker_name_is_custom || !/^Speaker (?:\d+|[A-Z]+)$/.test(name)) ? name : undefined;
}

export function speakerIdentity(turn: TranscriptTurn): string | undefined {
  if (turn.attribution === "overlap") return;
  if (turn.speaker_edit) return JSON.stringify([turn.speaker_edit.kind, turn.speaker_edit.scope_id, turn.speaker_edit.id]);
  if (turn.attribution === "unknown") return;
  if (turn.speaker_id) return `id:${turn.speaker_id}`;
  const label = turn.speaker_label?.trim() || turn.speaker_name?.trim();
  const identity = turn.speaker_number != null ? `number:${turn.speaker_number}` : label ? `label:${label}` : undefined;
  if (identity) return JSON.stringify([turn.source_part ?? "", identity]);
}

export function speakerKey(turn: TranscriptTurn): string | undefined {
  if (turn.attribution === "overlap" || (turn.attribution === "unknown" && !turn.speaker_edit)) return;
  const name = assignedName(turn);
  return name ? `name:${name}` : speakerIdentity(turn);
}

export function speakersInPassage(turns: readonly TranscriptTurn[], speakers: ReadonlyMap<string, Speaker>) {
  const seen = new Set<string>();
  return turns.flatMap((turn) => {
    const speaker = speakers.get(speakerKey(turn) ?? "");
    const identity = speakerIdentity(turn);
    if (!turn.speaker_edit || !speaker || !identity || !turn.text.trim() || seen.has(identity)) return [];
    seen.add(identity);
    return [{ identity, speaker, edit: turn.speaker_edit, sample: Array.from(turn.text.trim().replace(/\s+/g, " ")).slice(0, 60).join("") }];
  });
}

export function speakerPassages(turns: readonly TranscriptTurn[], identity: string) {
  // Bound a start-only preview by the next timed turn in the same audio part.
  const nextStarts = new Map<string, number>();
  const passages = [];
  const ordered = turnsInPlaybackOrder(turns);
  for (let i = ordered.length - 1; i >= 0; i--) {
    const turn = ordered[i];
    if (!turn.text.trim() || !validStart(turn)) continue;
    const part = turn.source_part ?? "";
    const next = nextStarts.get(part);
    nextStarts.set(part, turn.start);
    if (speakerIdentity(turn) !== identity) continue;
    const end = Math.min(timed(turn) ? turn.end : Math.min(turn.start + 8, next ?? Infinity), turn.part_end ?? Infinity);
    if (end > turn.start) passages.push({ offset: turn.start_offset, start: turn.start, end, text: turn.text });
  }
  return passages.reverse();
}

export function initialSpeakerPassage(passages: ReturnType<typeof speakerPassages>, shown: readonly TranscriptTurn[], row?: TranscriptTurn) {
  if (row) return passages.findIndex((passage) => passage.offset === (row.original_start_offset ?? row.start_offset));
  const offsets = new Set(shown.map((turn) => turn.original_start_offset ?? turn.start_offset));
  const first = passages.findIndex((passage) => offsets.has(passage.offset));
  return first === -1 && passages.length ? 0 : first;
}

export function recordingSpeakers(turns: readonly TranscriptTurn[]) {
  const speakers = new Map<string, Speaker>();
  const identities = new Map<string, number>();
  for (const turn of turnsInPlaybackOrder(turns)) {
    const key = speakerKey(turn);
    const identity = speakerIdentity(turn);
    if (identity && turn.text.trim() && !identities.has(identity)) identities.set(identity, identities.size);
    if (!key || !identity || speakers.has(key) || !turn.text.trim()) continue;
    const name = assignedName(turn);
    let color = 0;
    const index = identities.get(identity)!;
    if (name) for (const char of name) color = (color * 31 + char.codePointAt(0)!) % 10;
    else color = index % 10;
    speakers.set(key, { key, name, number: index + 1, color });
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

export function turnsInPlaybackOrder(turns: readonly TranscriptTurn[]) {
  // Interleave timed turns from parallel tracks without guessing times for untimed text.
  const timedTurns = turns.filter(validStart);
  if (timedTurns.every((turn, index) => index === 0 || timedTurns[index - 1].start <= turn.start)) return turns;
  timedTurns.sort((a, b) => a.start - b.start);
  let index = 0;
  return turns.map((turn) => validStart(turn) ? timedTurns[index++] : turn);
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
