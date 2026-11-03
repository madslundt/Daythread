"""Sample-exact audio crops for native-envelope forced alignment."""

import io
import math
import wave

_SAMPLE_RATE = 16000
_MAX_ALIGNMENT_SECONDS = 30
_PADDING_SECONDS = 0.25


def alignment_source_duration(audio: bytes) -> float:
    """Read a PCM source timeline even when it is a full recording, not a chunk."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            params = source.getparams()
            if ((params.nchannels, params.sampwidth, params.framerate, params.comptype)
                    != (1, 2, _SAMPLE_RATE, "NONE")):
                raise ValueError("invalid_alignment_audio")
            frames = source.getnframes()
            if frames <= 0:
                raise ValueError("empty_alignment_audio")
            return frames / _SAMPLE_RATE
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError("invalid_alignment_audio") from exc


def crop_native_envelope(audio: bytes, start: float, end: float) -> tuple[bytes, float]:
    """Crop a native segment plus at most 250 ms of context on either side."""
    if (isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
            or not math.isfinite(start) or not math.isfinite(end)):
        raise ValueError("invalid_native_time")
    with wave.open(io.BytesIO(audio), "rb") as source:
        params = source.getparams()
        if ((params.nchannels, params.sampwidth, params.framerate, params.comptype)
                != (1, 2, _SAMPLE_RATE, "NONE")):
            raise ValueError("invalid_alignment_audio")
        frames = source.getnframes()
        duration = frames / _SAMPLE_RATE
        if not 0 <= start < end <= duration:
            raise ValueError("native_time_outside_audio")
        max_frames = _MAX_ALIGNMENT_SECONDS * _SAMPLE_RATE
        native_left = math.floor(start * _SAMPLE_RATE)
        native_right = math.ceil(end * _SAMPLE_RATE)
        if native_right - native_left > max_frames:
            raise ValueError("native_segment_too_long")

        want_left = native_left - math.ceil(
            max(0, start - _PADDING_SECONDS) * _SAMPLE_RATE - 1e-8
        )
        want_right = math.floor(
            min(duration, end + _PADDING_SECONDS) * _SAMPLE_RATE + 1e-8
        ) - native_right
        want_left, want_right = max(0, want_left), max(0, want_right)
        spare = max_frames - (native_right - native_left)
        take_left = min(want_left, (spare + 1) // 2)
        take_right = min(want_right, spare - take_left)
        spare -= take_left + take_right
        extra_left = min(want_left - take_left, spare)
        take_left += extra_left
        spare -= extra_left
        take_right += min(want_right - take_right, spare)
        left, right = native_left - take_left, native_right + take_right
        source.setpos(left)
        data = source.readframes(right - left)
        if len(data) != (right - left) * 2:
            raise ValueError("truncated_alignment_audio")
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setparams(params)
            target.writeframes(data)
    return output.getvalue(), left / _SAMPLE_RATE
