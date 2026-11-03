import { useRef } from "react";

export function createRecordingSeekController() {
  let audio: HTMLAudioElement | null = null;
  let pending: number | null = null;
  const audioRef = (element: HTMLAudioElement | null) => { audio = element; };
  const onReady = () => {
    if (!audio || pending === null) return;
    audio.pause();
    audio.currentTime = pending;
    pending = null;
  };
  const seekTo = (seconds?: number) => {
    audio?.pause();
    pending = seconds !== undefined && Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
    if (pending !== null && audio && audio.readyState >= 1) onReady();
  };
  return { audioRef, onReady, seekTo };
}

// Topic selection works before metadata loads and always leaves playback paused.
export function useRecordingSeek() {
  const controller = useRef<ReturnType<typeof createRecordingSeekController> | null>(null);
  if (!controller.current) controller.current = createRecordingSeekController();
  return controller.current;
}
