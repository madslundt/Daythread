import { useCallback, useEffect, useRef, useState, type RefCallback } from "react";
import { displayLocale, t } from "./i18n";
import { Icon } from "./Icon";
import { playbackTime, recordingPeaks } from "./recordingWaveform";
import "./recordingAudioPlayer.css";

type Props = {
  src: string;
  waveformSrc?: string;
  label?: string;
  active?: boolean;
  durationHint?: number;
  autoPlay?: boolean;
  audioRef?: RefCallback<HTMLAudioElement>;
  onError?: () => void;
  onPositionChange?: (seconds: number) => void;
  onReady?: () => void;
  initialSpeed?: number;
  bookmarks?: { label: string; seconds: number; onSelect: () => void }[];
};

export function RecordingAudioPlayer(props: Props) {
  return <Player key={props.src} {...props} />;
}

function Player({ src, waveformSrc, label, active = true, durationHint = 0, autoPlay, audioRef, onError, onPositionChange, onReady, initialSpeed = 1, bookmarks }: Props) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const scrubbing = useRef(false);
  const resumeAfterScrub = useRef(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(Number.isFinite(durationHint) ? Math.max(0, durationHint) : 0);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(Number.isFinite(initialSpeed) ? Math.min(2, Math.max(0.5, initialSpeed)) : 1);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [waveformState, setWaveformState] = useState("loading");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  // Topic links retain their exact audio boundaries, including replay and seeking.
  const fragment = src.match(/#t=([\d.]+)(?:,([\d.]+))?/);
  const start = fragment ? Number(fragment[1]) : 0;
  const requestedEnd = fragment?.[2] ? Number(fragment[2]) : Infinity;
  const clipEnd = useRef(Infinity);
  const bindAudio = useCallback((element: HTMLAudioElement | null) => {
    audio.current = element;
    const cleanup = audioRef?.(element);
    return () => { element?.pause(); cleanup?.(); audio.current = null; };
  }, [audioRef]);

  useEffect(() => {
    const element = audio.current;
    if (!active) { element?.pause(); return; }
    const controller = new AbortController();
    setWaveformState("loading");
    void (async () => {
      try {
        if (waveformSrc) {
          const response = await fetch(waveformSrc, { signal: controller.signal });
          if (!response.ok) throw new Error(String(response.status));
          const waveform: { peaks: number[]; duration: number } = await response.json();
          controller.signal.throwIfAborted();
          setPeaks(waveform.peaks);
          setWaveformState("ready");
          return;
        }
        const response = await fetch(src.split("#")[0], { signal: controller.signal });
        if (!response.ok) throw new Error(String(response.status));
        const bytes = await response.arrayBuffer();
        controller.signal.throwIfAborted();
        // An offline context avoids opening an audio device or requiring a gesture.
        const context = new OfflineAudioContext(1, 1, 16000);
        const buffer = await context.decodeAudioData(bytes);
        controller.signal.throwIfAborted();
        const end = Math.min(buffer.duration, requestedEnd);
        const first = Math.min(buffer.length, Math.floor(start * buffer.sampleRate));
        const last = Math.max(first, Math.ceil(end * buffer.sampleRate));
        const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) =>
          buffer.getChannelData(index).subarray(first, last));
        setPeaks(recordingPeaks(channels));
        setWaveformState("ready");
        // MediaRecorder WebM files can report an infinite native duration.
        if (element && !Number.isFinite(element.duration)) {
          clipEnd.current = Math.max(start, end);
          setDuration(clipEnd.current - start);
          if (element.readyState >= 1) setReady(true);
        }
      } catch {
        if (!controller.signal.aborted) setWaveformState("unavailable");
      }
    })();
    return () => { controller.abort(); element?.pause(); };
  }, [active, src, waveformSrc, retry, start, requestedEnd]);

  async function play() {
    const element = audio.current;
    if (!element) return;
    if (element.currentTime >= clipEnd.current || element.ended) element.currentTime = start;
    element.playbackRate = speed;
    try { await element.play(); setError(""); }
    catch (error) {
      // A topic selection or Pause can cancel a pending play request normally.
      if (error instanceof DOMException && error.name === "AbortError") return;
      setError(t("Unable to play recording."));
    }
  }

  function seek(seconds: number) {
    const element = audio.current;
    if (!element || !ready) return;
    const next = Math.min(duration, Math.max(0, seconds));
    element.currentTime = start + next;
    setPosition(next);
    if (next >= duration) element.pause();
  }

  function finishScrubbing() {
    if (!scrubbing.current) return;
    scrubbing.current = false;
    if (resumeAfterScrub.current && audio.current && audio.current.currentTime < clipEnd.current) void play();
    resumeAfterScrub.current = false;
  }

  function changeSpeed(delta: number) {
    const next = Math.min(2, Math.max(0.5, speed + delta));
    setSpeed(next);
    if (audio.current) audio.current.playbackRate = next;
  }

  const available = ready && duration > 0 && !error;
  return <div className="recording-player" role="group" aria-label={label ?? t("Recording")}>
    <audio ref={bindAudio} src={src} preload={active ? "metadata" : "none"} autoPlay={autoPlay && active}
      onLoadedMetadata={(event) => {
        const element = event.currentTarget;
        const knownDuration = Number.isFinite(element.duration) ? element.duration
          : Number.isFinite(clipEnd.current) ? clipEnd.current : duration;
        if (!knownDuration) return;
        clipEnd.current = Math.max(start, Math.min(knownDuration, requestedEnd));
        setDuration(clipEnd.current - start);
        element.currentTime = Math.min(start, element.duration);
        element.playbackRate = speed;
        setReady(true);
        onReady?.();
      }}
      onTimeUpdate={(event) => {
        const element = event.currentTarget;
        if (scrubbing.current) return;
        setPosition(Math.max(0, Math.min(duration, element.currentTime - start)));
        onPositionChange?.(element.currentTime - start);
        if (element.currentTime >= clipEnd.current) element.pause();
      }}
      onPlay={(event) => {
        const current = event.currentTarget;
        document.querySelectorAll("audio").forEach((element) => { if (element !== current) element.pause(); });
        setPlaying(true);
      }} onPause={() => setPlaying(false)}
      onEnded={() => { setPlaying(false); setPosition(duration); }}
      onError={() => { setReady(false); setPlaying(false); setError(t("Unable to play recording.")); onError?.(); }} />
    <div className="recording-player-time"><span>{playbackTime(position)}</span><span> / {playbackTime(duration)}</span></div>
    <div className="recording-player-waveform">
      {waveformState === "ready" ? <svg viewBox="0 0 800 64" preserveAspectRatio="none" aria-hidden="true">
        {peaks.map((peak, index) => {
          const height = Math.max(1, peak * 64);
          return <rect key={index} x={index * 5} y={(64 - height) / 2} width="2.75" height={height} rx="1"
            className={duration > 0 && index / peaks.length <= position / duration ? "played" : undefined} />;
        })}
      </svg> : <span className="caption" role="status">{waveformState === "loading" && !error ? t("Loading audio…") : t("Waveform unavailable")}</span>}
      <input type="range" min="0" max={duration || 1} step="0.01" value={position} disabled={!available}
        aria-label={t("Playback position")} aria-valuetext={`${playbackTime(position)} / ${playbackTime(duration)}`}
        onChange={(event) => seek(Number(event.currentTarget.value))}
        onKeyDown={(event) => {
          if (["ArrowLeft", "ArrowDown", "ArrowRight", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            seek(event.key === "Home" ? 0 : event.key === "End" ? duration
              : position + (["ArrowLeft", "ArrowDown"].includes(event.key) ? -15 : 15));
          }
        }}
        onPointerDown={(event) => {
          if (!available) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          scrubbing.current = true;
          resumeAfterScrub.current = playing;
          audio.current?.pause();
        }} onPointerUp={finishScrubbing} onPointerCancel={finishScrubbing} onLostPointerCapture={finishScrubbing} />
    </div>
    {duration > 0 && bookmarks?.length ? <div className="recording-player-bookmarks" role="group" aria-label={t("Topics")}>
      {bookmarks.filter((bookmark) => Number.isFinite(bookmark.seconds) && bookmark.seconds >= 0 && bookmark.seconds <= duration)
        .map((bookmark, index) => <button key={`${bookmark.seconds}:${index}`} type="button"
          style={{ left: `${bookmark.seconds / duration * 100}%` }}
          title={`${playbackTime(bookmark.seconds)} · ${bookmark.label}`}
          aria-label={`${playbackTime(bookmark.seconds)} · ${bookmark.label}`}
          onClick={bookmark.onSelect}><svg width="12" height="16" viewBox="0 0 12 16" fill="currentColor" aria-hidden="true"><path d="M1 1h10v14l-5-4-5 4Z" /></svg></button>)}
    </div> : null}
    <div className="recording-player-controls">
      <div className="recording-player-transport">
        <button type="button" className="recording-player-play" disabled={!available}
          aria-label={playing ? t("Pause playback") : t("Play original audio")}
          onClick={() => { if (playing) audio.current?.pause(); else void play(); }}>
          <Icon name={playing ? "pause" : "play"} size={24} />
        </button>
        <button type="button" disabled={!available} aria-label={t("Skip backward 15 seconds")} onClick={() => seek(position - 15)}><SkipIcon /></button>
        <button type="button" disabled={!available} aria-label={t("Skip forward 15 seconds")} onClick={() => seek(position + 15)}><SkipIcon forward /></button>
      </div>
      <div className="recording-player-speed">
        <button type="button" disabled={speed <= 0.5} aria-label={t("Slower playback")} onClick={() => changeSpeed(-0.25)}><SpeedIcon /></button>
        <span aria-label={t("Playback speed")} aria-live="polite">{speed.toLocaleString(displayLocale(), { maximumFractionDigits: 2 })}×</span>
        <button type="button" disabled={speed >= 2} aria-label={t("Faster playback")} onClick={() => changeSpeed(0.25)}><SpeedIcon plus /></button>
      </div>
    </div>
    {error ? <p className="caption recording-player-error" role="alert">{error} <button type="button" className="text-button" onClick={() => {
      setError(""); setReady(false); setRetry((value) => value + 1); audio.current?.load();
    }}>{t("Try again")}</button></p> : null}
  </div>;
}

function SkipIcon({ forward = false }: { forward?: boolean }) {
  return <svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M7 6a10 10 0 1 1-3 8M7 2v5H2" transform={forward ? "translate(28 0) scale(-1 1)" : undefined} />
    <text x="14" y="18" textAnchor="middle" fill="currentColor" stroke="none" fontSize="10" fontWeight="600">15</text>
  </svg>;
}

function SpeedIcon({ plus = false }: { plus?: boolean }) {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" aria-hidden="true">
    <circle cx="12" cy="12" r="9" /><path d="M8 12h8" />{plus ? <path d="M12 8v8" /> : null}
  </svg>;
}
