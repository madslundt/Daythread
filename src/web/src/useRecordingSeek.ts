import { useRef } from "react";

export function createRecordingSeekController() {
  let audio: HTMLAudioElement | null = null;
  let pending: { seconds: number; mode: "seek" | "play" | "playing" } | null = null;
  const audioRef = (element: HTMLAudioElement | null) => {
    audio = element;
    return () => { if (audio === element) audio = null; };
  };
  const start = (element: HTMLAudioElement) => {
    if (element.paused) void element.play().catch(() => {});
  };
  const onReady = () => {
    if (!audio || pending === null) return;
    const { seconds, mode } = pending;
    pending = null;
    if (mode === "seek") audio.pause();
    audio.currentTime = seconds;
    if (mode === "play") start(audio);
  };
  const seekTo = (seconds?: number) => {
    audio?.pause();
    pending = seconds !== undefined && Number.isFinite(seconds) && seconds >= 0 ? { seconds, mode: "seek" } : null;
    if (pending !== null && audio && audio.readyState >= 1) onReady();
  };
  const playFrom = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds < 0) return;
    pending = { seconds, mode: "play" };
    if (audio && audio.readyState >= 1) onReady();
    else if (audio) {
      // Keep playback inside the user gesture; only the seek waits for metadata.
      pending.mode = "playing";
      start(audio);
    }
  };
  return { audioRef, onReady, seekTo, playFrom };
}

// Defer seeks and explicit play requests until metadata is ready.
export function useRecordingSeek() {
  const controller = useRef<ReturnType<typeof createRecordingSeekController> | null>(null);
  if (!controller.current) controller.current = createRecordingSeekController();
  return controller.current;
}
