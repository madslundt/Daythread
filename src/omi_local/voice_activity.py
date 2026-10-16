"""Advisory speech activity for recap boundaries; never filters STT or saved audio."""

import io
import wave

from .audio import amplify_quiet_audio

VERSION = "webrtcvad-2.0.14-mode0-v1"


def analyze_voice(data: bytes) -> dict:
    """Observe complete 20ms frames, preserving short/quiet speech conservatively.

    A voiced frame in either the original or amplified copy counts as speech.
    Missing/failed detection is unknown, never evidence that a conversation ended.
    """
    try:
        with wave.open(io.BytesIO(data)) as stream:
            rate = stream.getframerate()
            if (stream.getnchannels(), stream.getsampwidth(), rate) != (1, 2, 16000):
                return {"version": VERSION, "status": "unknown"}
            raw = stream.readframes(stream.getnframes())
        if not any(raw):
            return {"version": VERSION, "status": "observed", "speech": [],
                    "non_speech": [[0, len(raw) / 32000]], "method": "digital_silence"}
        import webrtcvad

        boosted, _ = amplify_quiet_audio(data, 12)
        with wave.open(io.BytesIO(boosted)) as stream:
            amplified = stream.readframes(stream.getnframes())
        detectors = [webrtcvad.Vad(0), webrtcvad.Vad(0)]
        speech, quiet = [], []
        frame_bytes = 640
        for offset in range(0, len(raw) - frame_bytes + 1, frame_bytes):
            voiced = [
                vad.is_speech(pcm[offset:offset + frame_bytes], rate)
                for vad, pcm in zip(detectors, (raw, amplified), strict=True)
            ]
            target = speech if any(voiced) else quiet
            start, end = offset / 32000, (offset + frame_bytes) / 32000
            if target and abs(target[-1][1] - start) < 1e-6:
                target[-1][1] = end
            else:
                target.append([start, end])
        return {"version": VERSION, "status": "observed", "speech": speech,
                "non_speech": quiet, "method": "webrtcvad"}
    except Exception:
        return {"version": VERSION, "status": "unknown"}
