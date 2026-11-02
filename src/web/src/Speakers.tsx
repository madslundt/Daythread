import { statusLabel } from "./statusLabels";
import { t } from "./i18n";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, errorMessage } from "./api";
import { SpeakersRequests, VoiceProfilesRequests, speakerName, speakerTime, type Speaker, type SpeakersData, type VoiceProfilesData, type VoiceProfile, type RememberVoiceChoice } from "./speakersRequests";
import { ErrorState } from "./UI";
import "./speakers.css";
import { RecordingAudioPlayer } from "./RecordingAudioPlayer";

export function Speakers({ kind, id }: { kind: "source" | "meeting"; id: string }) {
  return <SpeakersPanel key={`${kind}:${id}`} kind={kind} id={id} />;
}

function SpeakersPanel({ kind, id }: { kind: "source" | "meeting"; id: string }) {
  const [requests] = useState(() => new SpeakersRequests(kind, id));
  const [voiceRequests] = useState(() => new VoiceProfilesRequests());
  const [data, setData] = useState<SpeakersData>();
  const [voices, setVoices] = useState<VoiceProfilesData>();
  const [voiceError, setVoiceError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [listening, setListening] = useState<string>();
  const [audioError, setAudioError] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const activeMutation = useRef(false);
  const operationEpoch = useRef(0);
  const currentScope = useRef<SpeakersData["scope"]>(undefined);
  currentScope.current = data?.scope;
  const bindAudio = useCallback((element: HTMLAudioElement | null) => {
    audio.current = element;
    if (element) return () => { element.pause(); audio.current = null; };
  }, []);

  useEffect(() => {
    let active = true;
    operationEpoch.current += 1;
    setLoading(true);
    setBusy(false);
    activeMutation.current = false;
    setDetecting(false);
    setListening(undefined);
    setData(undefined);
    setVoices(undefined);
    setVoiceError("");
    setError("");
    requests.run("GET").then((result) => {
      if (active && result) { setData(result); setLoading(false); }
    }).catch((failure) => {
      if (active) { setError(errorMessage(failure)); setLoading(false); }
    });
    voiceRequests.run("GET").then((result) => { if (active && result) setVoices(result); })
      .catch((failure) => { if (active) setVoiceError(errorMessage(failure)); });
    return () => { active = false; requests.cancel(); voiceRequests.cancel(); };
  }, [requests, voiceRequests, revision]);

  useEffect(() => {
    if (data?.processing?.state !== "queued" && data?.processing?.state !== "processing") return;
    let active = true;
    const timer = window.setInterval(() => {
      void requests.run("GET").then((result) => {
        if (active && result) setData(result);
      }).catch((failure) => {
        if (active) setError(errorMessage(failure));
      });
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [data?.processing?.state, requests]);

  useEffect(() => {
    const refresh = () => {
      operationEpoch.current += 1;
      requests.cancel();
      voiceRequests.cancel();
      audio.current?.pause();
      setListening(undefined);
      setData(undefined);
      setVoices(undefined);
      setLoading(true);
      setRevision((value) => value + 1);
    };
    const speakersChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ origin: SpeakersRequests; scope?: SpeakersData["scope"] }>).detail;
      if (detail?.origin === requests || activeMutation.current) return;
      if (detail?.scope && currentScope.current &&
          (detail.scope.kind !== currentScope.current.kind || detail.scope.id !== currentScope.current.id)) return;
      refresh();
    };
    window.addEventListener("omi:archive-changed", refresh);
    window.addEventListener("omi:settings-changed", refresh);
    window.addEventListener("omi:speakers-changed", speakersChanged);
    window.addEventListener("omi:voice-profiles-changed", speakersChanged);
    window.addEventListener("focus", refresh);
    return () => {
      audio.current?.pause();
      window.removeEventListener("omi:archive-changed", refresh);
      window.removeEventListener("omi:settings-changed", refresh);
      window.removeEventListener("omi:speakers-changed", speakersChanged);
      window.removeEventListener("omi:voice-profiles-changed", speakersChanged);
      window.removeEventListener("focus", refresh);
    };
  }, [requests, voiceRequests]);

  async function refreshConflict(epoch: number) {
    const [scope, directory] = await Promise.allSettled([requests.run("GET"), voiceRequests.run("GET")]);
    if (operationEpoch.current !== epoch) return;
    if (scope.status === "fulfilled" && scope.value) setData(scope.value);
    if (directory.status === "fulfilled" && directory.value) setVoices(directory.value);
    window.dispatchEvent(new CustomEvent("omi:voice-profiles-changed", { detail: { origin: requests } }));
  }

  async function mutate(method: "POST" | "PATCH", suffix = "", body?: unknown) {
    if (loading || busy || activeMutation.current) return;
    const epoch = ++operationEpoch.current;
    activeMutation.current = true;
    setBusy(true);
    setDetecting(method === "POST" && suffix === "");
    setError("");
    audio.current?.pause();
    setListening(undefined);
    try {
      const result = await requests.run(method, suffix, body);
      if (result && operationEpoch.current === epoch) {
        setData(result);
        window.dispatchEvent(new CustomEvent("omi:speakers-changed", { detail: { origin: requests, scope: result.scope } }));
        if (suffix.endsWith("/remember") || suffix.endsWith("/confirm") || method === "PATCH" || suffix === "/merge" || suffix === "") {
          try {
            const directory = await voiceRequests.run("GET");
            if (directory && operationEpoch.current === epoch) setVoices(directory);
          } catch (failure) { if (operationEpoch.current === epoch) setVoiceError(errorMessage(failure)); }
          if (operationEpoch.current === epoch) window.dispatchEvent(new CustomEvent("omi:voice-profiles-changed", { detail: { origin: requests } }));
        }
      }
    } catch (failure) {
      if (operationEpoch.current === epoch) {
        setError(errorMessage(failure));
        if (failure instanceof ApiError && failure.status === 409) await refreshConflict(epoch);
      }
    }
    finally { if (operationEpoch.current === epoch) { activeMutation.current = false; setBusy(false); setDetecting(false); } }
  }

  async function forgetVoice(profile: VoiceProfile, exampleId?: string) {
    if (loading || busy || activeMutation.current || !voices) return;
    if (!window.confirm(exampleId ? t("Remove this saved voice example for {value0}? Confirmed conversation names stay unchanged.", { value0: profile.name }) : t("Forget the saved voice {value0} and all its examples? Future conversations will no longer suggest this voice. Confirmed conversation names stay unchanged.", { value0: profile.name }))) return;
    await updateVoices(() => voiceRequests.forget(profile.id, voices.revision, exampleId));
  }

  async function updateVoices(action: () => Promise<VoiceProfilesData | undefined>) {
    if (loading || busy || activeMutation.current) return;
    const epoch = ++operationEpoch.current;
    activeMutation.current = true; setBusy(true); setVoiceError("");
    audio.current?.pause(); setListening(undefined);
    try {
      const directory = await action();
      if (!directory || operationEpoch.current !== epoch) return;
      setVoices(directory);
      const updated = await requests.run("GET");
      if (updated && operationEpoch.current === epoch) setData(updated);
      if (operationEpoch.current === epoch) window.dispatchEvent(new CustomEvent("omi:voice-profiles-changed", { detail: { origin: requests } }));
    } catch (failure) {
      if (operationEpoch.current === epoch) {
        setVoiceError(errorMessage(failure));
        if (failure instanceof ApiError && failure.status === 409) await refreshConflict(epoch);
      }
    }
    finally { if (operationEpoch.current === epoch) { activeMutation.current = false; setBusy(false); } }
  }

  const selected = data?.speakers.find((speaker) => speaker.id === listening);
  return <SpeakersView data={data} voices={voices} voiceError={voiceError} loading={loading} busy={busy} detecting={detecting} error={error}
    onForget={(profile, example) => void forgetVoice(profile, example)}
    onSetSelf={(profile) => { if (voices) void updateVoices(() => voiceRequests.setSelf(profile, voices.revision)); }}
    onRemember={(speaker, choice) => void mutate("POST", `/${encodeURIComponent(speaker)}/remember`, { ...choice, version: data?.version })}
    onConfirm={(speaker, profile) => void mutate("POST", `/${encodeURIComponent(speaker)}/confirm`, { profile_id: profile, version: data?.version })}
    onRetry={() => setRevision((value) => value + 1)} onDetect={() => {
      const reset = data?.state !== "idle";
      if (reset && !window.confirm(t("Detect speakers again? This replaces the existing speaker labels, names and turn assignments for this conversation or meeting."))) return;
      void mutate("POST", "", reset ? { reset: true } : undefined);
    }}
    onRename={(speaker, name) => void mutate("PATCH", `/${encodeURIComponent(speaker)}`, { name, version: data?.version })}
    onMerge={(source, target) => void mutate("POST", "/merge", { source_id: source, target_id: target, version: data?.version })}
    onReassign={(turn, speaker) => void mutate("PATCH", `/turns/${encodeURIComponent(turn)}`, { speaker_id: speaker, version: data?.version })}
    onListen={(speaker) => { setAudioError(false); setListening(speaker); }}>
    {selected && data?.version ? <div className="speaker-playback">
      <p className="caption">{t("Listening to")}{" "}{speakerName(selected)} {t("· speaking turns joined in order.")}</p>
      <RecordingAudioPlayer key={`${selected.id}:${data.version}`} audioRef={bindAudio} autoPlay
        label={t("Listen to {value0} only", { value0: speakerName(selected) })} src={requests.audioUrl(selected.id, data.version)}
        onError={() => setAudioError(true)} />
      {audioError ? <p className="caption" role="alert">{t("Speaker audio is unavailable. The original audio may have expired. Refresh speakers to check again.")}</p> : null}
    </div> : null}
  </SpeakersView>;
}

function SpeakerPosition({ sourceStart, sourceEnd, audioStart, audioEnd, partIndex, partCount }: {
  sourceStart: number | null; sourceEnd: number | null;
  audioStart?: number | null; audioEnd?: number | null;
  partIndex?: number | null; partCount?: number | null;
}) {
  const hasAudioRange = audioStart != null && audioEnd != null;
  const hasSourceRange = sourceStart !== null && sourceEnd !== null;
  const range = hasAudioRange
    ? t("Elapsed audio {value0}–{value1}", {
      value0: speakerTime(audioStart), value1: speakerTime(audioEnd),
    })
    : hasSourceRange
      ? t("Source time {value0}–{value1}", {
        value0: speakerTime(sourceStart), value1: speakerTime(sourceEnd),
      })
      : t("Time unavailable");
  return <span className="caption">
    {range}{partIndex != null && partCount != null
      ? <> {"·"} {t("Part {value0} of {value1}", { value0: partIndex, value1: partCount })}</>
      : null}
  </span>;
}

function speakerPositionLabel(turn: SpeakersData["turns"][number]): string {
  const start = turn.audio_start ?? turn.start;
  const time = start === null ? t("Time unavailable") : speakerTime(start);
  return turn.part_index != null && turn.part_count != null
    ? t("Speaker for turn at {value0} in part {value1} of {value2}", {
      value0: time, value1: turn.part_index, value2: turn.part_count,
    })
    : t("Speaker for turn at {value0}", { value0: time });
}

export function SpeakersView({ data, voices, voiceError, loading, busy, detecting = false, error, onDetect, onRetry, onRename, onListen, onMerge, onReassign, onRemember, onConfirm, onForget, onSetSelf, children }: {
  data?: SpeakersData;
  voices?: VoiceProfilesData;
  voiceError?: string;
  loading: boolean;
  busy: boolean;
  detecting?: boolean;
  error: string;
  onDetect: () => void;
  onRetry: () => void;
  onRename: (speaker: string, name: string | null) => void;
  onListen: (speaker: string) => void;
  onMerge: (source: string, target: string) => void;
  onReassign: (turn: string, speaker: string) => void;
  onRemember?: (speaker: string, choice: RememberVoiceChoice) => void;
  onConfirm?: (speaker: string, profile: string) => void;
  onForget?: (profile: VoiceProfile, exampleId?: string) => void;
  onSetSelf?: (profileId: string | null) => void;
  children?: ReactNode;
}) {
  const stale = data?.state === "stale";
  const automaticBusy = data?.processing?.state === "queued" || data?.processing?.state === "processing";
  const editable = Boolean(data?.version && !stale && !loading && !busy);
  const speakers = new Map(data?.speakers.map((speaker) => [speaker.id, speaker]));
  return <section className="speakers-panel" aria-label={t("Speakers")}>
    <div className="speaker-heading">
      <h3>{data?.speakers.length ? t("{value0} {value1} detected", { value0: data.speakers.length, value1: data.speakers.length === 1 ? t("speaker") : t("speakers") }) : t("Speakers")}</h3>
      <button type="button" className="button small" onClick={onDetect}
        disabled={loading || busy || automaticBusy || !data?.configured || !data?.audio_available}>
        {detecting ? t("Detecting speakers…") : busy ? t("Saving…") : data?.state !== "idle" && data ? t("Detect speakers again") : t("Detect speakers")}
      </button>
    </div>
    {loading ? <p className="caption" role="status">{t("Loading speakers…")}</p> : null}
    {detecting ? <p className="caption" role="status">{t("Analyzing retained audio with your local speaker service. This may take a few minutes.")}</p> : null}
    {data?.processing?.state === "queued" ? <p className="caption" role="status">{t("Automatic speaker detection is queued.")}</p> : null}
    {data?.processing?.state === "processing" ? <p className="caption" role="status">{t("Automatic speaker detection is processing.")}</p> : null}
    {data?.processing?.state === "error" ? <p className="caption" role="alert">{t("Automatic speaker detection failed: {value0}", { value0: data.processing.error || t("Unknown error") })}</p> : null}
    {error ? <ErrorState message={error} retry={onRetry} /> : null}
    {data?.recognition || voices?.recognition ? <p className="caption" role="status">{t((data?.recognition || voices?.recognition)?.message || "")}</p> : null}
    <p className="caption">{t("Voice profiles stay in the selected archive. A suggestion never changes a name until you confirm it.")}</p>
    {voiceError ? <ErrorState message={voiceError} retry={onRetry} /> : null}
    {onRemember ? <MyVoiceSetup voices={voices} busy={busy || loading} onSetSelf={onSetSelf} /> : null}
    {onForget ? <VoiceProfilesManager voices={voices} busy={busy || loading} onForget={onForget} onSetSelf={onSetSelf} /> : null}
    {data && !data.configured ? <p className="caption">{t("Enable speaker detection and configure your local speaker service in")}{" "}<a href="/settings/server">{t("Settings → Advanced → Speaker detection")}</a>.</p> : null}
    {data && !data.audio_available ? <p className="caption">{t("Original audio is unavailable. Speaker detection and speaker-only playback require retained audio.")}</p> : null}
    {data && data.state !== "idle" ? <p className="caption">{t("Detecting again replaces speaker labels, names and turn assignments.")}</p> : null}
    {stale ? <p className="caption" role="status">{t("The recordings or transcript changed. Detect speakers again before editing labels or listening to their turns.")}</p> : null}
    {data?.state === "ready" && !data.speakers.length ? <p className="caption">{t("No speakers detected in this audio.")}</p> : null}
    {data?.speakers.length ? <>
      <p className="caption">{t("Names apply only to this")}{" "}{data.scope?.kind === "meeting" ? t("meeting") : t("conversation")}{data.source_count && data.source_count > 1 ? t(" across {value0} recordings", { value0: data.source_count }) : ""}{t(". Detection can make mistakes; review speaking turns below. Overlapping voices may remain audible.")}</p>
      {data.speakers.some((speaker) => speaker.remembered_example_id) ? <p className="caption">{t("Editing names or turns removes saved voice examples from this conversation. Confirm them again after correcting the speakers.")}</p> : null}
      <div className="speaker-list">
        {data.speakers.map((speaker) => <SpeakerEditor key={`${speaker.id}:${speaker.name || ""}:${speaker.voice_profile_id || ""}:${speaker.remembered_example_id || ""}`} speaker={speaker}
          speakers={data.speakers} editable={editable} canListen={editable && data.audio_available}
          voices={voices} recognition={data.recognition?.state || voices?.recognition.state || "disabled"}
          onRemember={onRemember} onConfirm={onConfirm} onRename={onRename} onListen={onListen} onMerge={onMerge} />)}
      </div>
      {!stale ? children : null}
      <details className="speaker-turns">
        <summary>{t("Review")}{" "}{data.turns.length} {t("speaking turns")}</summary>
        <p className="caption">{t("Text appears only when transcript timestamps align with a speaking turn. Your full original transcript stays unchanged.")}</p>
        <ol>{data.turns.map((turn) => <li key={turn.id}>
          <div className="speaker-turn-heading">
            <strong>{speakers.has(turn.speaker_id) ? speakerName(speakers.get(turn.speaker_id)!) : t("Unknown speaker")}</strong>
            <SpeakerPosition sourceStart={turn.start} sourceEnd={turn.end}
              audioStart={turn.audio_start} audioEnd={turn.audio_end}
              partIndex={turn.part_index} partCount={turn.part_count} />
            <label>{t("Speaker")}{" "}<select aria-label={speakerPositionLabel(turn)}
              value={turn.speaker_id} disabled={!editable} onChange={(event) => onReassign(turn.id, event.target.value)}>
              {data.speakers.map((speaker) => <option key={speaker.id} value={speaker.id}>{speakerName(speaker)}</option>)}
            </select></label>
          </div>
          {turn.text ? <p className="transcript-text">{turn.text}</p> : null}
        </li>)}</ol>
      </details>
    </> : null}
    {data?.utterances?.length ? <details className="speaker-turns speaker-transcript">
      <summary>{t("Attributed transcript")}</summary>
      <ol>{data.utterances.map((utterance) => <li key={utterance.id}>
        <div className="speaker-turn-heading">
          <strong>{utterance.speaker_name || utterance.speaker_label || t("Unknown speaker")}</strong>
          <SpeakerPosition sourceStart={utterance.start} sourceEnd={utterance.end}
            audioStart={utterance.audio_start} audioEnd={utterance.audio_end}
            partIndex={utterance.part_index} partCount={utterance.part_count} />
        </div>
        <p className="transcript-text">{utterance.text}</p>
      </li>)}</ol>
    </details> : null}
  </section>;
}

function SpeakerEditor({ speaker, speakers, voices, recognition, editable, canListen, onRename, onListen, onMerge, onRemember, onConfirm }: {
  speaker: Speaker; speakers: Speaker[]; editable: boolean; canListen: boolean;
  voices?: VoiceProfilesData; recognition: "disabled" | "unvalidated" | "ready";
  onRename: (speaker: string, name: string | null) => void;
  onListen: (speaker: string) => void;
  onMerge: (source: string, target: string) => void;
  onRemember?: (speaker: string, choice: RememberVoiceChoice) => void;
  onConfirm?: (speaker: string, profile: string) => void;
}) {
  const [name, setName] = useState(speaker.name || "");
  const [mergeTarget, setMergeTarget] = useState("");
  const [voiceTarget, setVoiceTarget] = useState(speaker.voice_profile_id || "");
  const [voiceName, setVoiceName] = useState(speaker.name || "");
  const [dismissedSuggestion, setDismissedSuggestion] = useState(false);
  const [isSelf, setIsSelf] = useState(false);
  const ownVoice = voices?.profiles.find((profile) => profile.is_self);
  const target = isSelf && ownVoice ? ownVoice.id : voiceTarget;
  const label = speakerName(speaker);
  return <div className="speaker-card">
    <div className="speaker-heading"><strong>{label}</strong>{speaker.is_self ? <span className="caption">{t("My voice")}</span> : null}<span className="caption">{speakerTime(speaker.seconds)} {t("speaking")}</span>
      <button type="button" className="button small" disabled={!canListen} onClick={() => onListen(speaker.id)}>{t("Listen to")}{" "}{label} {t("only")}</button>
    </div>
    {speaker.suggestion && !dismissedSuggestion ? <div className="speaker-suggestion">
      <p className="caption">{t("Suggested:")}{" "}{speaker.suggestion.name}{t(". Listen first, then confirm the person.")}</p>
      <div className="button-row">
        <button type="button" className="button small" disabled={!canListen || recognition !== "ready" || !onConfirm}
          onClick={() => onConfirm?.(speaker.id, speaker.suggestion!.profile_id)}>{t("Confirm")}{" "}{speaker.suggestion.name}</button>
        <button type="button" className="text-button" onClick={() => setDismissedSuggestion(true)}>{t("Leave")}{" "}{label} {t("unchanged")}</button>
      </div>
    </div> : null}
    {speaker.remembered_example_id ? <p className="caption">{t("Saved as a voice example for future conversations.")}</p> : null}
    <form className="speaker-name-form" onSubmit={(event) => { event.preventDefault(); onRename(speaker.id, name.trim() || null); }}>
      <label>{t("Name for")}{" "}{label}<input value={name} placeholder={speaker.label} maxLength={120} disabled={!editable} onChange={(event) => setName(event.target.value)} /></label>
      <button className="button small" type="submit" disabled={!editable || (name.trim() || null) === speaker.name}>{t("Save name")}</button>
      {speaker.name ? <button className="text-button" type="button" disabled={!editable} onClick={() => onRename(speaker.id, null)}>{t("Reset to")}{" "}{speaker.label}</button> : null}
    </form>
    {onRemember ? <details className="speaker-remember">
      <summary>{t("Remember speaker for future conversations")}</summary>
      <p className="caption">{t("After listening, explicitly choose whose voice this is. Saved examples can suggest a name in later conversations; you confirm every suggestion. A new profile remains separate even if its name matches another profile.")}</p>
      <label className="speaker-self-choice"><input type="checkbox" checked={isSelf} disabled={!editable || !voices}
        onChange={(event) => setIsSelf(event.target.checked)} />{t("This is my voice")}</label>
      <form className="speaker-name-form" onSubmit={(event) => {
        event.preventDefault();
        if (isSelf && !voices) return;
        onRemember(speaker.id, { ...(target ? { profile_id: target } : { name: voiceName.trim() }),
          ...(isSelf ? { is_self: true, voice_revision: voices!.revision } : {}) });
      }}>
        {isSelf && ownVoice ? <p className="caption">{t("My voice")}: {ownVoice.name}</p> : <label>{t("Save voice to")}{" "}<select value={voiceTarget} onChange={(event) => setVoiceTarget(event.target.value)} disabled={!editable || !voices}>
          <option value="">{t("Create a new voice profile")}</option>
          {voices?.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.example_count} {t("examples")}</option>)}
        </select></label>}
        {!target ? <label>{t("New voice profile name")}<input value={voiceName} maxLength={120} disabled={!editable}
          onChange={(event) => setVoiceName(event.target.value)} placeholder={t("Name")} /></label> : null}
        <button className="button small" type="submit" disabled={!canListen || !voices || recognition === "disabled" || (!target && !voiceName.trim())}>{t("Confirm and remember voice")}</button>
      </form>
    </details> : null}
    {speakers.length > 1 ? <form className="speaker-merge-form" onSubmit={(event) => { event.preventDefault(); if (mergeTarget) onMerge(speaker.id, mergeTarget); }}>
      <label>{t("Same person as")}{" "}<select value={mergeTarget} disabled={!editable} onChange={(event) => setMergeTarget(event.target.value)}>
        <option value="">{t("Choose speaker…")}</option>
        {speakers.filter((other) => other.id !== speaker.id).map((other) => <option key={other.id} value={other.id}>{speakerName(other)}</option>)}
      </select></label>
      <button className="text-button" type="submit" disabled={!editable || !mergeTarget}>{t("Merge turns")}</button>
    </form> : null}
  </div>;
}

export function MyVoiceSetup({ voices, busy, onSetSelf }: { voices?: VoiceProfilesData; busy: boolean;
  onSetSelf?: (profileId: string | null) => void }) {
  const ownVoice = voices?.profiles.find((profile) => profile.is_self);
  return <section className="my-voice-setup" aria-label={t("My voice")}>
    <div className="speaker-heading"><h3>{t("My voice")}</h3>
      {ownVoice && onSetSelf ? <button type="button" className="text-button" disabled={busy} onClick={() => onSetSelf(null)}>{t("Clear my voice")}</button> : null}
    </div>
    {!voices ? <p className="caption">{t("Loading saved voices…")}</p> : ownVoice ? <>
      <p><strong>{ownVoice.name}</strong> · {ownVoice.example_count} {t("examples ·")} {ownVoice.conversation_count} {t("conversations")}</p>
      <p className="caption">{ownVoice.conversation_count < 2
        ? t("Add at least ten seconds of clear speech from two separate conversations.")
        : t("Your voice examples are saved. Suggestions still require a passing evaluation and your confirmation.")}</p>
    </> : <><p className="caption">{t("No voice has been marked as yours in this archive.")}</p>
      <p className="caption">{t("Add at least ten seconds of clear speech from two separate conversations.")}</p></>}
    <p className="caption">{t("Detect speakers, listen to your turns, then choose “This is my voice” when remembering a speaker.")}</p>
  </section>;
}

export function VoiceProfilesManager({ voices, busy, onForget, onSetSelf }: { voices?: VoiceProfilesData; busy: boolean;
  onForget: (profile: VoiceProfile, exampleId?: string) => void; onSetSelf?: (profileId: string | null) => void }) {
  return <details className="voice-profiles">
    <summary>{t("Saved voices")}{voices ? ` (${voices.profiles.length})` : ""}</summary>
    <p className="caption">{t("Only examples you explicitly confirm are saved. Removing an example stops using it for recognition. Forgetting a voice removes all its saved examples. Conversation names stay in their conversations.")}</p>
    {!voices ? <p className="caption">{t("Loading saved voices…")}</p> : !voices.profiles.length ? <p className="caption">{t("No saved voices in this archive.")}</p> : voices.profiles.map((profile) => <div className="voice-profile" key={profile.id}>
      <div className="speaker-heading"><strong>{profile.name}</strong>{profile.is_self ? <span className="caption">{t("My voice")}</span> : null}<span className="caption">{profile.example_count} {t("examples ·")}{" "}{profile.conversation_count} {t("conversations")}</span>
        {!profile.is_self && onSetSelf ? <button type="button" className="text-button" disabled={busy} onClick={() => onSetSelf(profile.id)}>{t("Use as my voice")}</button> : null}
        <button type="button" className="text-button danger" disabled={busy} onClick={() => onForget(profile)}>{t("Forget voice")}</button>
      </div>
      <ul>{profile.examples.map((example) => <li key={example.id}>
        <span>{speakerTime(example.seconds)} · {example.scope.kind === "meeting" ? t("Meeting") : t("Conversation")} {example.scope.id.slice(0, 8)} · {example.created_at.slice(0, 10)}</span>
        <button type="button" className="text-button" disabled={busy} onClick={() => onForget(profile, example.id)}>{t("Remove example")}</button>
      </li>)}</ul>
    </div>)}
  </details>;
}
