import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "./api";
import { SpeakersRequests, speakerName, speakerTime, type Speaker, type SpeakersData } from "./speakersRequests";
import { ErrorState } from "./UI";
import "./speakers.css";

export function Speakers({ kind, id }: { kind: "source" | "meeting"; id: string }) {
  return <SpeakersPanel key={`${kind}:${id}`} kind={kind} id={id} />;
}

function SpeakersPanel({ kind, id }: { kind: "source" | "meeting"; id: string }) {
  const [requests] = useState(() => new SpeakersRequests(kind, id));
  const [data, setData] = useState<SpeakersData>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [listening, setListening] = useState<string>();
  const [audioError, setAudioError] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const activeMutation = useRef(false);
  const currentScope = useRef<SpeakersData["scope"]>(undefined);
  currentScope.current = data?.scope;
  const bindAudio = useCallback((element: HTMLAudioElement | null) => {
    audio.current = element;
    if (element) return () => { element.pause(); audio.current = null; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setBusy(false);
    activeMutation.current = false;
    setDetecting(false);
    setListening(undefined);
    setData(undefined);
    setError("");
    requests.run("GET").then((result) => {
      if (active && result) { setData(result); setLoading(false); }
    }).catch((failure) => {
      if (active) { setError(errorMessage(failure)); setLoading(false); }
    });
    return () => { active = false; requests.cancel(); };
  }, [requests, revision]);

  useEffect(() => {
    const refresh = () => {
      requests.cancel();
      audio.current?.pause();
      setListening(undefined);
      setData(undefined);
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
    window.addEventListener("focus", refresh);
    return () => {
      audio.current?.pause();
      window.removeEventListener("omi:archive-changed", refresh);
      window.removeEventListener("omi:settings-changed", refresh);
      window.removeEventListener("omi:speakers-changed", speakersChanged);
      window.removeEventListener("focus", refresh);
    };
  }, [requests]);

  async function mutate(method: "POST" | "PATCH", suffix = "", body?: unknown) {
    if (loading || busy) return;
    activeMutation.current = true;
    setBusy(true);
    setDetecting(method === "POST" && suffix === "");
    setError("");
    audio.current?.pause();
    setListening(undefined);
    try {
      const result = await requests.run(method, suffix, body);
      if (result) {
        setData(result); setBusy(false); setDetecting(false);
        window.dispatchEvent(new CustomEvent("omi:speakers-changed", { detail: { origin: requests, scope: result.scope } }));
      }
    } catch (failure) { setError(errorMessage(failure)); setBusy(false); setDetecting(false); }
    finally { activeMutation.current = false; }
  }

  const selected = data?.speakers.find((speaker) => speaker.id === listening);
  return <SpeakersView data={data} loading={loading} busy={busy} detecting={detecting} error={error}
    onRetry={() => setRevision((value) => value + 1)} onDetect={() => {
      const reset = data?.state !== "idle";
      if (reset && !window.confirm("Detect speakers again? This replaces the existing speaker labels, names and turn assignments for this conversation or meeting.")) return;
      void mutate("POST", "", reset ? { reset: true } : undefined);
    }}
    onRename={(speaker, name) => void mutate("PATCH", `/${encodeURIComponent(speaker)}`, { name, version: data?.version })}
    onMerge={(source, target) => void mutate("POST", "/merge", { source_id: source, target_id: target, version: data?.version })}
    onReassign={(turn, speaker) => void mutate("PATCH", `/turns/${encodeURIComponent(turn)}`, { speaker_id: speaker, version: data?.version })}
    onListen={(speaker) => { setAudioError(false); setListening(speaker); }}>
    {selected && data?.version ? <div className="speaker-playback">
      <p className="caption">Listening to {speakerName(selected)} · speaking turns joined in order.</p>
      <audio key={`${selected.id}:${data.version}`} ref={bindAudio} controls autoPlay preload="none"
        aria-label={`Listen to ${speakerName(selected)} only`} src={requests.audioUrl(selected.id, data.version)}
        onError={() => setAudioError(true)} />
      {audioError ? <p className="caption" role="alert">Speaker audio is unavailable. The original audio may have expired. Refresh speakers to check again.</p> : null}
    </div> : null}
  </SpeakersView>;
}

export function SpeakersView({ data, loading, busy, detecting = false, error, onDetect, onRetry, onRename, onListen, onMerge, onReassign, children }: {
  data?: SpeakersData;
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
  children?: ReactNode;
}) {
  const stale = data?.state === "stale";
  const editable = Boolean(data?.version && !stale && !loading && !busy);
  const speakers = new Map(data?.speakers.map((speaker) => [speaker.id, speaker]));
  return <section className="speakers-panel" aria-label="Speakers">
    <div className="speaker-heading">
      <h3>{data?.speakers.length ? `${data.speakers.length} ${data.speakers.length === 1 ? "speaker" : "speakers"} detected` : "Speakers"}</h3>
      <button type="button" className="button small" onClick={onDetect}
        disabled={loading || busy || !data?.configured || !data?.audio_available}>
        {detecting ? "Detecting speakers…" : busy ? "Saving…" : data?.state !== "idle" && data ? "Detect speakers again" : "Detect speakers"}
      </button>
    </div>
    {loading ? <p className="caption" role="status">Loading speakers…</p> : null}
    {detecting ? <p className="caption" role="status">Analyzing retained audio with your local speaker service. This may take a few minutes.</p> : null}
    {error ? <ErrorState message={error} retry={onRetry} /> : null}
    {data && !data.configured ? <p className="caption">Enable speaker detection and configure your local speaker service in <a href="#settings/server">Settings → Models &amp; server → Speaker detection</a>.</p> : null}
    {data && !data.audio_available ? <p className="caption">Original audio is unavailable. Speaker detection and speaker-only playback require retained audio.</p> : null}
    {data && data.state !== "idle" ? <p className="caption">Detecting again replaces speaker labels, names and turn assignments.</p> : null}
    {stale ? <p className="caption" role="status">The recordings or transcript changed. Detect speakers again before editing labels or listening to their turns.</p> : null}
    {data?.state === "ready" && !data.speakers.length ? <p className="caption">No speakers detected in this audio.</p> : null}
    {data?.speakers.length ? <>
      <p className="caption">Names apply only to this {data.scope?.kind === "meeting" ? "meeting" : "conversation"}{data.source_count && data.source_count > 1 ? ` across ${data.source_count} recordings` : ""}. Detection can make mistakes; review speaking turns below. Overlapping voices may remain audible.</p>
      <div className="speaker-list">
        {data.speakers.map((speaker) => <SpeakerEditor key={`${speaker.id}:${speaker.name || ""}`} speaker={speaker}
          speakers={data.speakers} editable={editable} canListen={editable && data.audio_available}
          onRename={onRename} onListen={onListen} onMerge={onMerge} />)}
      </div>
      {!stale ? children : null}
      <details className="speaker-turns">
        <summary>Review {data.turns.length} speaking turns</summary>
        <p className="caption">Text appears only when transcript timestamps align with a speaking turn. Your full original transcript stays unchanged.</p>
        <ol>{data.turns.map((turn) => <li key={turn.id}>
          <div className="speaker-turn-heading">
            <strong>{speakers.has(turn.speaker_id) ? speakerName(speakers.get(turn.speaker_id)!) : "Unknown speaker"}</strong>
            <span className="caption">{speakerTime(turn.start)}–{speakerTime(turn.end)} in recording {turn.source_id.slice(0, 8)}</span>
            <label>Speaker <select aria-label={`Speaker for turn at ${speakerTime(turn.start)} in recording ${turn.source_id}`}
              value={turn.speaker_id} disabled={!editable} onChange={(event) => onReassign(turn.id, event.target.value)}>
              {data.speakers.map((speaker) => <option key={speaker.id} value={speaker.id}>{speakerName(speaker)}</option>)}
            </select></label>
          </div>
          {turn.text ? <p className="transcript-text">{turn.text}</p> : null}
        </li>)}</ol>
      </details>
    </> : null}
  </section>;
}

function SpeakerEditor({ speaker, speakers, editable, canListen, onRename, onListen, onMerge }: {
  speaker: Speaker; speakers: Speaker[]; editable: boolean; canListen: boolean;
  onRename: (speaker: string, name: string | null) => void;
  onListen: (speaker: string) => void;
  onMerge: (source: string, target: string) => void;
}) {
  const [name, setName] = useState(speaker.name || "");
  const [mergeTarget, setMergeTarget] = useState("");
  const label = speakerName(speaker);
  return <div className="speaker-card">
    <div className="speaker-heading"><strong>{label}</strong><span className="caption">{speakerTime(speaker.seconds)} speaking</span>
      <button type="button" className="button small" disabled={!canListen} onClick={() => onListen(speaker.id)}>Listen to {label} only</button>
    </div>
    <form className="speaker-name-form" onSubmit={(event) => { event.preventDefault(); onRename(speaker.id, name.trim() || null); }}>
      <label>Name for {label}<input value={name} placeholder={speaker.label} maxLength={120} disabled={!editable} onChange={(event) => setName(event.target.value)} /></label>
      <button className="button small" type="submit" disabled={!editable || (name.trim() || null) === speaker.name}>Save name</button>
      {speaker.name ? <button className="text-button" type="button" disabled={!editable} onClick={() => onRename(speaker.id, null)}>Reset to {speaker.label}</button> : null}
    </form>
    {speakers.length > 1 ? <form className="speaker-merge-form" onSubmit={(event) => { event.preventDefault(); if (mergeTarget) onMerge(speaker.id, mergeTarget); }}>
      <label>Same person as <select value={mergeTarget} disabled={!editable} onChange={(event) => setMergeTarget(event.target.value)}>
        <option value="">Choose speaker…</option>
        {speakers.filter((other) => other.id !== speaker.id).map((other) => <option key={other.id} value={other.id}>{speakerName(other)}</option>)}
      </select></label>
      <button className="text-button" type="submit" disabled={!editable || !mergeTarget}>Merge turns</button>
    </form> : null}
  </div>;
}
