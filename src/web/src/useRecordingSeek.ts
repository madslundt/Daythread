import { useCallback, useRef } from "react";

// Topic selection works before metadata loads and always leaves playback paused.
export function useRecordingSeek() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const pending = useRef<number | null>(null);
  const audioRef = useCallback((element: HTMLAudioElement | null) => { audio.current = element; }, []);
  const onReady = useCallback(() => {
    if (!audio.current || pending.current === null) return;
    audio.current.pause();
    audio.current.currentTime = pending.current;
    pending.current = null;
  }, []);
  const seekTo = useCallback((seconds?: number) => {
    audio.current?.pause();
    pending.current = seconds !== undefined && Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
    if (audio.current && audio.current.readyState >= 1) onReady();
  }, [onReady]);
  return { audioRef, onReady, seekTo };
}
