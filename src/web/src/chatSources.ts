import type { Source } from "./types";
import { answerCitations } from "./chatCitations";

const nearbySourceMs = 15_000;
const nearbyPassageSeconds = 5;

type Timing = { start: number; end: number };
export type ChatPassage = { from: number; through: number; text: string; timing?: Timing };
export type ChatSourceEntry = { source: Source; passages: ChatPassage[]; cited: boolean };
export type ChatSourceGroup = { number: number; key: string; entries: ChatSourceEntry[]; cited: boolean };

function passageTiming(source: Source, chars: string[], from: number, through: number): Timing | undefined {
  const turns = source.transcript_turns?.filter((turn) => turn.end_offset > from && turn.start_offset < through);
  if (!turns?.length) return;
  const duration = (Date.parse(source.ended_at) - Date.parse(source.started_at)) / 1000;
  let cursor = from;
  let previousEnd = 0;
  for (const [index, turn] of turns.entries()) {
    if (!Number.isInteger(turn.start_offset) || !Number.isInteger(turn.end_offset)
        || turn.start_offset < 0 || turn.end_offset > chars.length || turn.start_offset >= turn.end_offset
        || (index > 0 && turn.start_offset < cursor) || chars.slice(cursor, turn.start_offset).join("").trim()
        || turn.start == null || turn.end == null
        || !Number.isFinite(turn.start) || !Number.isFinite(turn.end)
        || turn.start < previousEnd || turn.end <= turn.start || !(turn.end <= duration)) return;
    previousEnd = turn.end;
    cursor = turn.end_offset;
  }
  if (!chars.slice(cursor, through).join("").trim()) return { start: turns[0].start!, end: previousEnd };
}

export function chatPassages(source: Source): ChatPassage[] {
  if (!source.text || !source.matches?.length) return [];
  // API offsets count Unicode characters, not JavaScript UTF-16 units.
  const chars = Array.from(source.text);
  const spans: ChatPassage[] = [];
  for (const { start_offset: from, end_offset: through, text } of source.matches) {
    if (!Number.isInteger(from) || !Number.isInteger(through) || from < 0 || through <= from || through > chars.length
        || typeof text !== "string" || !text.trim() || chars.slice(from, through).join("") !== text) continue;
    spans.push({ from, through, text, timing: passageTiming(source, chars, from, through) });
  }
  const result: ChatPassage[] = [];
  for (const span of spans.sort((a, b) => a.from - b.from || a.through - b.through)) {
    const prior = result.at(-1);
    if (prior && (span.from <= prior.through || (prior.timing && span.timing
        && span.timing.start >= prior.timing.start && span.timing.start <= prior.timing.end + nearbyPassageSeconds))) {
      const through = Math.max(prior.through, span.through);
      const timing = passageTiming(source, chars, prior.from, through);
      if (span.from <= prior.through || timing) {
        Object.assign(prior, { through, text: chars.slice(prior.from, through).join(""), timing });
        continue;
      }
    }
    result.push(span);
  }
  return result;
}

export function chatSourceGroups(sources: Source[], answer: string, stale = false): ChatSourceGroup[] {
  const citationOrder = new Map(answerCitations(answer, sources.map(({ id }) => id)).map((id, index) => [id, index]));
  type Candidate = { entry: ChatSourceEntry; windows: Timing[]; start: number; rank: number };
  const byRecording = new Map<string, Candidate[][]>();
  const seen = new Set<string>();
  sources.forEach((source, rank) => {
    const id = source.id.toLowerCase();
    if (seen.has(id)) return;
    seen.add(id);
    const passages = chatPassages(source);
    const entry = { source, passages: stale ? passages.map(({ timing: _, ...passage }) => passage) : passages, cited: citationOrder.has(id) };
    const start = Date.parse(source.started_at);
    const end = Date.parse(source.ended_at);
    const recording = source.source_metadata?.recording_id || source.recording_id;
    // Receipt estimates and date-only imports do not establish proximity.
    const key = recording && end > start && (!source.timestamp_basis || source.timestamp_basis === "capture")
      ? `recording:${recording}` : `source:${id}`;
    const windows = passages.length && passages.every(({ timing }) => timing)
      ? passages.map(({ timing }) => ({ start: start + timing!.start * 1000, end: start + timing!.end * 1000 }))
      : [{ start, end }];
    const groups = byRecording.get(key) ?? [];
    const near = (other: Candidate) => other.windows.some((a) => windows.some((b) =>
      a.start <= b.end + nearbySourceMs && b.start <= a.end + nearbySourceMs));
    const joined = groups.filter((group) => group.some(near));
    byRecording.set(key, [...groups.filter((group) => !joined.includes(group)),
      [...joined.flat(), { entry, windows, start, rank }]]);
  });
  return [...byRecording.values()].flat().map((group) => {
    const entries = group.sort((a, b) => a.start - b.start || a.rank - b.rank).map(({ entry }) => entry);
    const cited = entries.filter((entry) => entry.cited);
    return { key: entries[0].source.id, entries, cited: !!cited.length,
      rank: cited.length ? Math.min(...cited.map(({ source }) => citationOrder.get(source.id.toLowerCase())!))
        : Math.min(...group.map((candidate) => candidate.rank)) };
  }).sort((a, b) => Number(b.cited) - Number(a.cited) || a.rank - b.rank)
    .map(({ rank: _, ...group }, index) => ({ ...group, number: index + 1 }));
}
