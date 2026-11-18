import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, errorMessage, request } from "./api";
import { t } from "./i18n";
import { playbackTime } from "./recordingWaveform";
import type { Source, TranscriptTurn } from "./types";
import { useResource } from "./useResource";
import { recordingSpeakers, speakerKey, speakersInPassage, speakerSkipPlan, turnIsExcluded, type Speaker } from "./recordingSpeakers";

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
  const keys = filters.selection?.scope === scope ? filters.selection.keys : noExclusions;
  const excluded = useMemo(() => new Set([...keys].filter((key) => speakers.has(key))), [keys, speakers]);
  const plan = useMemo(() => speakerSkipPlan(turns ?? noTurns, excluded), [turns, excluded]);
  return { speakers, excluded, toggle: (key: string) => filters.toggle(scope, key),
    intervals: canSkip ? plan.intervals : [], incomplete: canPlay && (canSkip ? plan.incomplete : excluded.size > 0) };
}

export type SpeakerReading = ReturnType<typeof useSpeakerReading>;

export function useSpeakerRefresh(refresh: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    window.addEventListener("omi:speakers-changed", refresh);
    window.addEventListener("omi:voice-profiles-changed", refresh);
    return () => {
      window.removeEventListener("omi:speakers-changed", refresh);
      window.removeEventListener("omi:voice-profiles-changed", refresh);
    };
  }, [enabled, refresh]);
}

export function useSourceTurns(source: Source, loadDetail: boolean) {
  const { data: detail, refresh } = useResource<Source>(loadDetail ? `/v1/transcripts/${encodeURIComponent(source.id)}` : null);
  useSpeakerRefresh(refresh, loadDetail);
  useEffect(() => {
    if (!loadDetail) return;
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
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

type EditableSpeakers = ReturnType<typeof speakersInPassage>;

function SpeakerEditor({ entries, onClose }: { entries: EditableSpeakers; onClose: () => void }) {
  const [target, setTarget] = useState(entries[0]);
  const [name, setName] = useState(target.speaker.name ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const operation = useRef<AbortController | undefined>(undefined);
  const title = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { operation.current?.abort(); element?.close(); };
  }, []);
  const stale = entries.find((entry) => entry.identity === target.identity)?.edit.version !== target.edit.version;
  async function save() {
    if (stale || operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true); setError("");
    const { kind, scope_id, id, version } = target.edit;
    try {
      await request(`/v1/speakers/${kind}/${encodeURIComponent(scope_id)}/${encodeURIComponent(id)}`, {
        method: "PATCH", body: JSON.stringify({ version, name: name.trim() || null }), signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      window.dispatchEvent(new CustomEvent("omi:speakers-changed"));
      onClose();
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        window.dispatchEvent(new CustomEvent("omi:speakers-changed"));
      }
    } finally {
      operation.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return <dialog ref={dialog} className="profile-dialog" aria-labelledby={title} onCancel={(event) => {
    event.preventDefault(); if (!busy) onClose();
  }}>
    <form className="recording-speaker-editor" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <h2 id={title}>{t("Edit speaker")}</h2>
      <label>{t("Speaker")}<select value={target.identity} disabled={busy} onChange={(event) => {
        const entry = entries.find((item) => item.identity === event.target.value);
        if (entry) { setTarget(entry); setName(entry.speaker.name ?? ""); setError(""); }
      }}>
        {entries.map((entry, index) => {
          const label = speakerLabel(entry.speaker);
          const repeated = entries.some((other) => other !== entry && other.speaker.key === entry.speaker.key);
          return <option key={entry.identity} value={entry.identity}>
            {repeated ? `${index + 1}. ${label} — ${entry.sample}` : label}
          </option>;
        })}
      </select></label>
      <label>{t("Name")}<input value={name} maxLength={200} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
      {error || stale ? <p className="caption" role="alert">{error || t("Speaker assignments or evidence changed; reload and retry")}</p> : null}
      <div className="button-row">
        <button className="button" type="button" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
        <button className="button accent" disabled={busy || stale}>{t("Save name")}</button>
      </div>
    </form>
  </dialog>;
}

export function TranscriptTurns({ text, turns = noTurns, allTurns = turns, onSeek, reading }: {
  text: string; turns?: TranscriptTurn[]; allTurns?: TranscriptTurn[]; onSeek?: (seconds: number) => void; reading?: SpeakerReading;
}) {
  const [showTimes, setShowTimes] = useState(false);
  const [editingSpeaker, setEditingSpeaker] = useState(false);
  const filters = useSpeakerFilters();
  const local = useSpeakerReading(reading ? undefined : allTurns, text, filters, false);
  const { speakers, excluded, toggle, incomplete } = reading ?? local;
  if (!turns.length) return <p className="transcript-text">{text}</p>;
  const visible = turns.filter((turn) => !turnIsExcluded(turn, excluded));
  const hasTimes = visible.some((turn) => turn.start != null);
  const editable = speakersInPassage(turns, speakers);
  return <>
    <div className="recording-transcript-controls">
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
    {editable.length ? <button className="text-button recording-edit-speaker" type="button" onClick={() => setEditingSpeaker(true)}>
      {t("Edit speaker")}
    </button> : null}
    </div>
    {editingSpeaker && editable.length ? <SpeakerEditor entries={editable} onClose={() => setEditingSpeaker(false)} /> : null}
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
