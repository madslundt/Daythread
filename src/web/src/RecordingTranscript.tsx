import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, audioUrl, errorMessage, recordingAudioUrl, request } from "./api";
import { Icon } from "./Icon";
import { t } from "./i18n";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";
import { playbackTime } from "./recordingWaveform";
import type { Source, TranscriptTurn } from "./types";
import { useResource } from "./useResource";
import { initialSpeakerPassage, recordingSpeakers, speakerIdentity, speakerKey, speakerPassages, speakersInPassage, speakerSkipPlan, turnIsExcluded, turnsInPlaybackOrder, type Speaker } from "./recordingSpeakers";

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
  const previewSrc = source.audio_available === false || source.audio_availability === "missing" ? undefined
    : source.reference_type === "recording_transcription" ? recordingAudioUrl(source.recording_id || source.id) : audioUrl(source.id);
  return <TranscriptTurns text={text} onSeek={onSeek} reading={reading} allTurns={turns} previewSrc={previewSrc} turns={range
    ? turnsInRange(source.text || "", turns, range) : turns} />;
}

export function turnsInRange(text: string, turns: TranscriptTurn[] | undefined, range: {
  start_offset: number; end_offset: number;
}): TranscriptTurn[] | undefined {
  const original = Array.from(text);
  return turns && turnsInPlaybackOrder(turns).flatMap((turn) => {
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
  return speaker.name ?? t("Speaker {value0}", { value0: speaker.number });
}

type EditableSpeakers = ReturnType<typeof speakersInPassage>;
type SpeakerEditSelection = { entries: EditableSpeakers; row?: TranscriptTurn };

function SpeakerEditor({ selection: { entries, row }, turns, allTurns, previewSrc, onClose }: {
  selection: SpeakerEditSelection; turns: TranscriptTurn[]; allTurns: TranscriptTurn[]; previewSrc?: string; onClose: () => void;
}) {
  const [target, setTarget] = useState(entries[0]);
  const [name, setName] = useState(target.speaker.name ?? "");
  const passages = useMemo(() => speakerPassages(allTurns, target.identity), [allTurns, target.identity]);
  const [passageIndex, setPassageIndex] = useState(() => initialSpeakerPassage(passages, turns, row));
  const passage = passages[passageIndex];
  const followingRow = row ? passages.findIndex(({ offset }) => offset >= row.start_offset) : -1;
  const previous = passage ? passageIndex - 1 : followingRow === -1 ? passages.length - 1 : followingRow - 1;
  const next = passage ? passageIndex + 1 : row ? passages.findIndex(({ offset }) => offset > row.start_offset) : -1;
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
  return <dialog ref={dialog} className="profile-dialog recording-speaker-dialog" aria-labelledby={title} onCancel={(event) => {
    event.preventDefault(); if (!busy) onClose();
  }}>
    <form className="recording-speaker-editor" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <h2 id={title}>{t("Edit speaker")}</h2>
      {entries.length > 1 ? <label>{t("Speaker")}<select value={target.identity} disabled={busy} onChange={(event) => {
        const entry = entries.find((item) => item.identity === event.target.value);
        if (entry) {
          setTarget(entry); setName(entry.speaker.name ?? ""); setError("");
          setPassageIndex(initialSpeakerPassage(speakerPassages(allTurns, entry.identity), turns));
        }
      }}>
        {entries.map((entry, index) => {
          const label = speakerLabel(entry.speaker);
          const repeated = entries.some((other) => other !== entry && other.speaker.key === entry.speaker.key);
          return <option key={entry.identity} value={entry.identity}>
            {repeated ? `${index + 1}. ${label} — ${entry.sample}` : label}
          </option>;
        })}
      </select></label> : <span className="recording-speaker-label" data-speaker-color={target.speaker.color}>{speakerLabel(target.speaker)}</span>}
      <label>{t("Name")}<input value={name} maxLength={200} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
      <div className="recording-speaker-preview">
        {previewSrc && passage ? <>
          <p className="caption" aria-live="polite">{playbackTime(passage.start)} · {passage.text}</p>
          <RecordingAudioPlayer key={`${target.identity}:${passage.offset}`} src={`${previewSrc}#t=${passage.start.toFixed(6)},${passage.end.toFixed(6)}`}
            label={speakerLabel(target.speaker)} showWaveform={false} />
        </> : <p className="caption" role="status">{t("No audio preview for this passage.")}</p>}
        {previewSrc && passages.length > 0 ? <div className="button-row">
          <button className="button small" type="button" disabled={previous < 0} onClick={() => setPassageIndex(previous)}>{t("Previous passage")}</button>
          <button className="button small" type="button" disabled={next < 0 || next >= passages.length} onClick={() => setPassageIndex(next)}>{t("Next passage")}</button>
        </div> : null}
      </div>
      {error || stale ? <p className="caption" role="alert">{error || t("Speaker assignments or evidence changed; reload and retry")}</p> : null}
      <div className="button-row">
        <button className="button" type="button" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
        <button className="button accent" disabled={busy || stale}>{t("Save name")}</button>
      </div>
    </form>
  </dialog>;
}

export function TranscriptTurns({ text, turns = noTurns, allTurns = turns, onSeek, reading, previewSrc }: {
  text: string; turns?: TranscriptTurn[]; allTurns?: TranscriptTurn[]; onSeek?: (seconds: number) => void; reading?: SpeakerReading; previewSrc?: string;
}) {
  const [showTimes, setShowTimes] = useState(false);
  const [editingSpeaker, setEditingSpeaker] = useState<{ key: string; row?: TranscriptTurn }>();
  const filters = useSpeakerFilters();
  const local = useSpeakerReading(reading ? undefined : allTurns, text, filters, false);
  const { speakers, excluded, toggle, incomplete } = reading ?? local;
  const ordered = useMemo(() => turnsInPlaybackOrder(turns), [turns]);
  const visible = ordered.filter((turn) => !turnIsExcluded(turn, excluded));
  const hasTimes = visible.some((turn) => turn.start != null);
  const { passageSpeakers, editable, editableByIdentity } = useMemo(() => {
    const present = new Set(ordered.filter((turn) => turn.text.trim()).map(speakerKey));
    const editable = speakersInPassage(ordered, speakers);
    return { passageSpeakers: [...speakers.values()].filter((speaker) => present.has(speaker.key)),
      editable, editableByIdentity: new Map(editable.map((entry) => [entry.identity, entry])) };
  }, [ordered, speakers]);
  const rowEntry = editingSpeaker?.row && editableByIdentity.get(speakerIdentity(editingSpeaker.row) ?? "");
  const entries = rowEntry ? [rowEntry] : editable.filter((entry) => entry.speaker.key === editingSpeaker?.key);
  function editButton(speaker: Speaker, row?: TranscriptTurn) {
    const label = t("Edit speaker: {value0}", { value0: speakerLabel(speaker) });
    return <button className="icon-button recording-edit-speaker" type="button" aria-label={label} title={label}
      onClick={() => setEditingSpeaker({ key: speaker.key, row })}><Icon name="pencil" size={15} /></button>;
  }
  return <>
    {passageSpeakers.length ? <div className="recording-speaker-filters">
      <div role="group" aria-label={t("Filter speakers")} className="recording-speaker-list">
        {passageSpeakers.map((speaker) => <span className="recording-speaker-chip" key={speaker.key}>
          <button type="button" className="recording-speaker-label" data-speaker-color={speaker.color}
            aria-pressed={!excluded.has(speaker.key)} onClick={() => toggle(speaker.key)}>{speakerLabel(speaker)}</button>
          {editable.some((entry) => entry.speaker.key === speaker.key) ? editButton(speaker) : null}
        </span>)}
      </div>
      {incomplete ? <p className="caption" role="status">{t("Some hidden speech will still play if its timing is unclear or it overlaps other audio.")}</p> : null}
    </div> : null}
    {editingSpeaker && entries.length ? <SpeakerEditor selection={{ entries, row: editingSpeaker.row }} turns={turns}
      allTurns={allTurns} previewSrc={previewSrc} onClose={() => setEditingSpeaker(undefined)} /> : null}
    {hasTimes ? <label className="caption recording-timestamp-option">
      <input type="checkbox" checked={showTimes} onChange={(event) => setShowTimes(event.target.checked)} />
      {t("Show timestamps")}
    </label> : null}
    {!turns.length ? <p className="transcript-text">{text}</p> : visible.some((turn) => turn.text.trim()) ? <div className="transcript-text recording-attributed-transcript">
      {visible.filter((turn) => turn.text.trim()).map((turn) => {
        const speaker = speakers.get(speakerKey(turn) ?? "");
        return <div className="recording-attributed-run" key={turn.start_offset}>
          {speaker && editableByIdentity.has(speakerIdentity(turn) ?? "") ? <button type="button"
            className="recording-speaker-label recording-speaker-rename" data-speaker-color={speaker.color}
            aria-label={t("Edit speaker: {value0}", { value0: speakerLabel(speaker) })}
            onClick={() => setEditingSpeaker({ key: speaker.key, row: turn })}>{speakerLabel(speaker)} <Icon name="pencil" size={14} /></button>
            : <span className="caption recording-speaker-label" data-speaker-color={speaker?.color}>{speaker ? speakerLabel(speaker)
              : turn.attribution === "overlap" ? t("Overlapping speech") : t("Unassigned speech")}</span>}
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
