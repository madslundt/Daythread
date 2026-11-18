import { useEffect, useMemo, useState, type ReactNode } from "react";
import { t } from "./i18n";
import { playbackTime } from "./recordingWaveform";
import type { Source, TranscriptTurn } from "./types";
import { useResource } from "./useResource";
import { recordingSpeakers, speakerKey, speakerSkipPlan, turnIsExcluded, type Speaker } from "./recordingSpeakers";

const noTurns: TranscriptTurn[] = [];
const noExclusions: ReadonlySet<string> = new Set();

export function useSpeakerFilters() {
  const [selection, setSelection] = useState<{ scope: string; keys: ReadonlySet<string> }>();
  return { selection, toggle(scope: string, key: string) {
    setSelection((current) => {
      const keys = new Set(current?.scope === scope ? current.keys : []);
      if (keys.has(key)) keys.delete(key); else keys.add(key);
      return { scope, keys };
    });
  } };
}

export function useSpeakerReading(turns: TranscriptTurn[] | undefined, scope: string,
  filters: ReturnType<typeof useSpeakerFilters>, canPlay: boolean, canSkip = canPlay) {
  const speakers = useMemo(() => recordingSpeakers(turns ?? noTurns), [turns]);
  const excluded = filters.selection?.scope === scope ? filters.selection.keys : noExclusions;
  const plan = useMemo(() => speakerSkipPlan(turns ?? noTurns, excluded), [turns, excluded]);
  return { speakers, excluded, toggle: (key: string) => filters.toggle(scope, key),
    intervals: canSkip ? plan.intervals : [], incomplete: canPlay && (canSkip ? plan.incomplete : excluded.size > 0) };
}

export type SpeakerReading = ReturnType<typeof useSpeakerReading>;

export function useSourceTurns(source: Source, loadDetail: boolean) {
  const { data: detail, refresh } = useResource<Source>(loadDetail ? `/v1/transcripts/${encodeURIComponent(source.id)}` : null);
  useEffect(() => {
    if (!loadDetail) return;
    window.addEventListener("omi:speakers-changed", refresh);
    window.addEventListener("omi:voice-profiles-changed", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("omi:speakers-changed", refresh);
      window.removeEventListener("omi:voice-profiles-changed", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [loadDetail, refresh]);
  return detail && detail.source_version === source.source_version && detail.text === source.text
    ? detail.transcript_turns ?? source.transcript_turns : source.transcript_turns;
}

export function RecordingTranscript({ label, children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <details className="recording-disclosure" onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{label ?? t("Full transcript")}</summary>
    {open ? children : null}
  </details>;
}

export function SourceTranscriptTurns({ source, text, onSeek, range, reading, fullTurns, loadDetail = !reading }: {
  source: Source; text: string; onSeek?: (seconds: number) => void;
  range?: { start_offset: number; end_offset: number };
  reading?: SpeakerReading; fullTurns?: TranscriptTurn[]; loadDetail?: boolean;
}) {
  const fetched = useSourceTurns(source, loadDetail && source.reference_type !== "recording_transcription");
  const turns = fullTurns ?? fetched;
  return <TranscriptTurns text={text} onSeek={onSeek} reading={reading} allTurns={turns} turns={range
    ? turnsInRange(source.text || "", turns, range) : turns} />;
}

export function turnsInRange(text: string, turns: TranscriptTurn[] | undefined, range: {
  start_offset: number; end_offset: number;
}): TranscriptTurn[] | undefined {
  const original = Array.from(text);
  return turns?.flatMap((turn) => {
    const left = Math.max(turn.start_offset, range.start_offset);
    const right = Math.min(turn.end_offset, range.end_offset);
    if (left >= right) return [];
    if (left === turn.start_offset && right === turn.end_offset) return [turn];
    return [{ ...turn, text: original.slice(left, right).join(""), start_offset: left,
      end_offset: right, start: left === turn.start_offset ? turn.start : undefined,
      end: right === turn.end_offset ? turn.end : undefined }];
  });
}

function speakerLabel(speaker: Speaker) {
  return speaker.name ?? t("Speaker {value0}", { value0: speaker.letter! });
}

export function TranscriptTurns({ text, turns = noTurns, allTurns = turns, onSeek, reading }: {
  text: string; turns?: TranscriptTurn[]; allTurns?: TranscriptTurn[]; onSeek?: (seconds: number) => void; reading?: SpeakerReading;
}) {
  const [showTimes, setShowTimes] = useState(false);
  const filters = useSpeakerFilters();
  const local = useSpeakerReading(reading ? undefined : allTurns, text, filters, false);
  const { speakers, excluded, toggle, incomplete } = reading ?? local;
  if (!turns.length) return <p className="transcript-text">{text}</p>;
  const visible = turns.filter((turn) => !turnIsExcluded(turn, excluded));
  const hasTimes = visible.some((turn) => turn.start != null);
  return <>
    {speakers.size ? <details className="recording-speaker-filters">
      <summary>{t("Filter speakers")}{excluded.size ? ` (${excluded.size})` : ""}</summary>
      <div role="group" aria-label={t("Filter speakers")}>
        {[...speakers.values()].map((speaker) => <label key={speaker.key}>
          <input type="checkbox" checked={!excluded.has(speaker.key)} onChange={() => toggle(speaker.key)} />
          <span className="recording-speaker-label" data-speaker-color={speaker.color}>{speakerLabel(speaker)}</span>
        </label>)}
      </div>
      {incomplete ? <p className="caption" role="status">{t("Some hidden speech will still play if its timing is unclear or it overlaps other audio.")}</p> : null}
    </details> : null}
    {hasTimes ? <label className="caption recording-timestamp-option">
      <input type="checkbox" checked={showTimes} onChange={(event) => setShowTimes(event.target.checked)} />
      {t("Show timestamps")}
    </label> : null}
    {visible.some((turn) => turn.text.trim()) ? <div className="transcript-text recording-attributed-transcript">
      {visible.filter((turn) => turn.text.trim()).map((turn) => {
        const speaker = speakers.get(speakerKey(turn) ?? "");
        return <div className="recording-attributed-run" key={turn.start_offset}>
          <span className="caption recording-speaker-label" data-speaker-color={speaker?.color}>{speaker ? speakerLabel(speaker)
            : turn.attribution === "overlap" ? t("Overlapping speech") : t("Unassigned speech")}</span>
          {showTimes && turn.start != null ? onSeek
            ? <button type="button" className="caption recording-speaker-time text-link"
              aria-label={`${t("Playback position")} ${playbackTime(turn.start)}`}
              onClick={() => onSeek(turn.start!)}>{playbackTime(turn.start)}</button>
            : <span className="caption recording-speaker-time">{playbackTime(turn.start)}</span> : null}
          <div>{turn.text}</div>
        </div>;
      })}
    </div> : <p className="caption" role="status">{t("No speech from the selected speakers in this passage.")}</p>}
  </>;
}
