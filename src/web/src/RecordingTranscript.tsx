import { useState, type ReactNode } from "react";
import { t } from "./i18n";
import { playbackTime } from "./recordingWaveform";
import type { Source, TranscriptTurn } from "./types";
import { useResource } from "./useResource";

export function RecordingTranscript({ label, children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <details className="recording-disclosure" onToggle={(event) => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>{label ?? t("Full transcript")}</summary>
    {open ? children : null}
  </details>;
}

export function SourceTranscriptTurns({ source, text, onSeek, range }: {
  source: Source; text: string; onSeek?: (seconds: number) => void;
  range?: { start_offset: number; end_offset: number };
}) {
  const detail = useResource<Source>(`/v1/transcripts/${encodeURIComponent(source.id)}`, 15_000).data;
  const turns = detail && detail.source_version === source.source_version && detail.text === source.text
    ? detail.transcript_turns : source.transcript_turns;
  return <TranscriptTurns text={text} onSeek={onSeek} turns={range
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
      end_offset: right, start: left === turn.start_offset ? turn.start : undefined }];
  });
}

export function TranscriptTurns({ text, turns = [], onSeek }: {
  text: string; turns?: TranscriptTurn[]; onSeek?: (seconds: number) => void;
}) {
  const [showTimes, setShowTimes] = useState(false);
  if (!turns.length) return <p className="transcript-text">{text}</p>;
  const hasTimes = turns.some((turn) => turn.start != null);
  return <>
    {hasTimes ? <label className="caption recording-timestamp-option">
      <input type="checkbox" checked={showTimes} onChange={(event) => setShowTimes(event.target.checked)} />
      {t("Show timestamps")}
    </label> : null}
    <div className="transcript-text recording-attributed-transcript">
      {turns.map((turn, index) => <div className="recording-attributed-run" key={`${index}:${turn.start_offset}`}>
        {turn.text.trim() ? <>
          <span className="caption recording-speaker-label">{turn.attribution === "overlap" ? t("Overlapping speech")
            : turn.speaker_name || turn.speaker_label || (turn.speaker_number !== undefined
              ? t("Speaker {value0}", { value0: turn.speaker_number }) : t("Unknown speaker"))}</span>
          {showTimes && turn.start != null ? onSeek
            ? <button type="button" className="caption recording-speaker-time text-link"
              aria-label={`${t("Playback position")} ${playbackTime(turn.start)}`}
              onClick={() => onSeek(turn.start!)}>{playbackTime(turn.start)}</button>
            : <span className="caption recording-speaker-time">{playbackTime(turn.start)}</span> : null}
        </> : null}
        <div>{turn.text}</div>
      </div>)}
    </div>
  </>;
}
