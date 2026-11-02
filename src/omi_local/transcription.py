"""Speech-only request copies with timestamps anchored to original recordings."""

import asyncio
import math
import re
from uuid import uuid4

from .audio import amplify_quiet_audio, contains_speech_energy, transcription_chunks
from .domain import stamp, utc_now, wav_duration
from .inference_errors import InferenceError
from .inference_usage import recording_usage
from .processing_progress import ProgressCallback
from .settings import Settings, STTRoute
from .speech_processing import enhance_audio
from .stt_languages import language_code
from .transcription_filter import FILTER_VERSION, filter_transcription_audio
from .transcription_vocabulary import vocabulary_fields


class Transcription:
    async def transcribe(self, data: bytes) -> str:
        return (await self.transcribe_result(data))["text"]

    async def transcribe_result(
        self, data: bytes, on_progress: ProgressCallback | None = None,
        *, recording_id: str | None = None,
        voice_activity: dict | None = None,
    ) -> dict:
        self.settings.require_inference("stt")
        with recording_usage(recording_id or str(uuid4())):
            return await self._transcribe_filtered(data, on_progress, voice_activity)

    async def _transcribe_filtered(
        self, data: bytes, on_progress: ProgressCallback | None, voice_activity: dict | None,
    ) -> dict:
        settings = self.settings
        chunks = []
        originals = []
        alignment_inputs = []
        try:
            # Finish every original pass before optional work can consume its budget.
            async with asyncio.timeout(settings.inference_timeout_seconds) as deadline:
                texts, segments = [], []
                filtered = await asyncio.to_thread(
                    filter_transcription_audio, data, enabled=settings.stt_filter_speech,
                    activity=voice_activity,
                )
                filtering = {
                    "version": FILTER_VERSION,
                    **{key: value for key, value in filtered.items() if key != "spans"},
                }
                self.record_usage({
                    "kind": "filter", "filter_status": filtered["status"], **filtering,
                })
                parts = [
                    (source_offset + chunk_offset, chunk)
                    for source_offset, span in filtered["spans"]
                    for chunk_offset, chunk in transcription_chunks(
                        span, settings.stt_chunk_seconds, settings.stt_boundary_search_seconds,
                    )
                ]
                if on_progress:
                    on_progress("transcribing", 0, len(parts))
                for offset, chunk in parts:
                    quiet = settings.stt_preserve_quiet and not contains_speech_energy(chunk, None)
                    prepared, gain = await asyncio.to_thread(
                        amplify_quiet_audio, chunk, settings.stt_max_gain_db
                    )
                    result = (
                        {"text": "", "segments": []} if quiet
                        else await self._transcribe_chunk(prepared, settings)
                    )
                    if result["text"]:
                        text_offset = sum(map(len, texts)) + len(texts)
                        alignment_inputs.append((offset, chunk, result["text"],
                                                 result.get("recognition", {}).get(
                                                     "detected_language"), text_offset))
                        texts.append(result["text"])
                    segments.extend(
                        {**segment, "start": segment["start"] + offset,
                         "end": segment["end"] + offset}
                        for segment in result["segments"]
                    )
                    status = "pending"
                    if quiet:
                        status = "digital_silence"
                    elif settings.stt_enhancement_mode == "off":
                        status = "disabled"
                    elif result["text"] and settings.stt_enhancement_mode == "on_empty":
                        status = "not_needed"
                    chunks.append({
                        "start": offset, "end": offset + wav_duration(chunk),
                        "gain_db": gain, "selected": "original",
                        "enhancement_status": status,
                        **({"timestamp_status": result["timestamp_status"]}
                           if "timestamp_status" in result else {}),
                        **result.get("recognition", {}),
                    })
                    originals.append((prepared, result))
                    if on_progress:
                        on_progress("transcribing", len(originals), len(parts))
        except TimeoutError as exc:
            self.health["stt"] = {"state": "unavailable", "checked_at": stamp(utc_now())}
            raise InferenceError("Inference deadline exceeded") from exc
        # Optional cancellation/cleanup is outside the required-pass timeout so it
        # cannot discard successful recognition. Its budget still uses that deadline.
        remaining = deadline.when() - asyncio.get_running_loop().time()
        alignment_status = {"status": "disabled"}
        if settings.alignment_enabled:
            alignment_budget = min(settings.alignment_timeout_seconds, max(0, remaining))
            aligned, alignment_status = await self._align_chunks(
                alignment_inputs, settings, alignment_budget,
            )
            if aligned:
                segments = aligned
            remaining = deadline.when() - asyncio.get_running_loop().time()
        budget = min(settings.stt_enhancement_timeout_seconds, remaining)
        await self._enhancement_candidates(chunks, originals, settings, budget, on_progress)
        return {
            "text": "\n".join(texts), "segments": segments,
            "processing": {
                "version": 2, "preserve_quiet": settings.stt_preserve_quiet,
                "filtering": filtering,
                "enhancement_mode": settings.stt_enhancement_mode, "chunks": chunks,
                "alignment": alignment_status,
            },
        }

    async def _align_chunks(self, parts, settings: Settings, budget: float):
        """Keep validated timing fragments; status makes incomplete coverage explicit."""
        if not parts:
            return [], {"status": "empty", "model": None}
        if budget <= 0:
            return None, {"status": "timeout", "model": None}
        output = []
        model = None
        overall = "aligned"
        languages = set()
        try:
            async with asyncio.timeout(budget):
                for offset, audio, text, language, text_offset in parts:
                    language = language or settings.alignment_language
                    languages.add(language)
                    response = await self.client.post(
                        f"{settings.alignment_base_url}/align",
                        files={"file": ("chunk.wav", audio, "audio/wav")},
                        data={"text": text, "language": language},
                        timeout=settings.alignment_timeout_seconds,
                        follow_redirects=False,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError
                    model = payload.get("model")
                    if not isinstance(model, str):
                        return output or None, {"status": "invalid", "model": None,
                                                "languages": sorted(languages)}
                    if payload.get("language") != language:
                        overall = "invalid"
                        continue
                    segments = payload.get("segments")
                    expected = list(re.finditer(r"\S+", text))
                    if not isinstance(segments, list):
                        overall = "partial"
                        continue
                    duration = wav_duration(audio)
                    previous = 0.0
                    last_character = 0
                    local = []
                    covered_ranges = set()
                    if len(segments) != len(expected) or payload.get("status") != "aligned":
                        overall = "partial"
                    covered_chars = set()
                    for segment in segments:
                        if not isinstance(segment, dict):
                            overall = "invalid"
                            continue
                        first, last = segment.get("start_offset"), segment.get("end_offset")
                        start, end = segment.get("start"), segment.get("end")
                        if (isinstance(first, bool) or isinstance(last, bool)
                                or not isinstance(first, int) or not isinstance(last, int)
                                or not 0 <= first < last <= len(text)
                                or text[first:last] != segment.get("text")
                                or any(first < match.end() and last > match.start()
                                       for match in expected
                                       if match.start() != first or match.end() != last)
                                or first < last_character):
                            overall = "invalid"
                            continue
                        if any(index in covered_chars for index in range(first, last)):
                            overall = "invalid"
                            continue
                        if (isinstance(start, bool) or isinstance(end, bool)
                                or not isinstance(start, (int, float))
                                or not isinstance(end, (int, float))
                                or not math.isfinite(start) or not math.isfinite(end)
                                or not previous <= start < end <= duration):
                            overall = "invalid"
                            continue
                        previous = end
                        last_character = last
                        covered_chars.update(range(first, last))
                        covered_ranges.add((first, last))
                        local.append({"start": start + offset, "end": end + offset,
                                      "text": text[first:last],
                                      "start_offset": first + text_offset,
                                      "end_offset": last + text_offset, "kind": "word"})
                    if covered_ranges != {(match.start(), match.end()) for match in expected}:
                        overall = "partial" if overall == "aligned" else overall
                    output.extend(local)
        except TimeoutError:
            return output or None, {"status": "timeout", "model": model,
                                    "languages": sorted(languages)}
        except Exception:
            # Optional alignment must never retry or discard successful recognition.
            return output or None, {"status": "unavailable", "model": model,
                                    "languages": sorted(languages)}
        return output, {"status": overall, "model": model, "languages": sorted(languages)}

    async def _enhancement_candidates(
        self, chunks: list[dict], originals: list[tuple[bytes, dict]],
        settings: Settings, budget: float,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        health = self.health["stt"]
        pending = [chunk for chunk in chunks if chunk["enhancement_status"] == "pending"]
        if budget <= 0:
            for chunk in pending:
                chunk["enhancement_status"] = "budget_exhausted"
            return
        status = "budget_exhausted"
        completed = 0
        if on_progress and pending:
            on_progress("enhancing", completed, len(pending))
        try:
            async with asyncio.timeout(budget):
                for chunk, (audio, original) in zip(chunks, originals, strict=True):
                    if chunk["enhancement_status"] != "pending":
                        continue
                    try:
                        enhanced = await enhance_audio(audio)
                        candidate = await self._transcribe_chunk(enhanced, settings)
                        chunk["enhancement_status"] = "complete"
                        chunk["candidate"] = {
                            "text": candidate["text"],
                            "differs": candidate["text"] != original["text"],
                            "segments": [
                                {**s, "start": s["start"] + chunk["start"],
                                 "end": s["end"] + chunk["start"]}
                                for s in candidate["segments"]
                            ],
                        }
                    except FileNotFoundError:
                        status = "unavailable"
                        break
                    except TimeoutError:
                        chunk["enhancement_status"] = "timeout"
                    except Exception:
                        # Do not leak provider bodies, executable paths or audio in diagnostics.
                        chunk["enhancement_status"] = "failed"
                    completed += 1
                    if on_progress:
                        on_progress("enhancing", completed, len(pending))
        except TimeoutError:
            status = "timeout"
        finally:
            self.health["stt"] = health
            for chunk in pending:
                if chunk["enhancement_status"] == "pending":
                    chunk["enhancement_status"] = status

    async def _transcribe_chunk(self, data: bytes, settings: Settings) -> dict:
        route = settings.stt_route("auto")
        detect = any(key != "auto" for key in settings.stt_routes)
        payload = await self._transcription(data, route, settings, detect)
        detected = payload.get("language")
        if detect and isinstance(detected, str):
            detected = detected.strip().lower()
            if detected != "auto" and settings.stt_override(detected) is not None:
                specialist = settings.stt_route(detected)
                if specialist != route:
                    # Routing chooses a model, never a forced output language: mixed
                    # conversations must retain the languages actually spoken.
                    payload = await self._transcription(data, specialist, settings)
                    route = specialist
        result = self._transcription_result(payload, data)
        result["recognition"] = {"recognition_model": route.model}
        if isinstance(detected, str):
            code = language_code(detected)
            if code and len(code) <= 32:
                result["recognition"]["detected_language"] = code
        return result

    async def _transcription(
        self,
        data: bytes,
        route: STTRoute,
        settings: Settings,
        detect: bool = False,
    ) -> dict:
        fields = {
            "model": route.model,
            "response_format": (
                "verbose_json" if settings.stt_segment_timestamps or detect else "json"
            ),
        }
        fields.update(vocabulary_fields(route.vocabulary_mode, settings.stt_vocabulary))
        token = route.token.get_secret_value()
        response = await self.post(
            "stt",
            f"{route.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            files={"file": ("chunk.wav", data, "audio/wav")},
            data=fields,
            follow_redirects=False,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise InferenceError("Invalid STT response") from exc
        if not isinstance(payload, dict):
            raise InferenceError("Invalid STT response")
        return payload

    @staticmethod
    def _transcription_result(payload: dict, data: bytes) -> dict:
        text = payload.get("text")
        if not isinstance(text, str) or len(text) > 20000:
            raise InferenceError("Invalid STT response")
        segments = []
        duration = wav_duration(data)
        raw_segments = payload.get("segments", [])
        if not isinstance(raw_segments, list):
            return {"text": text.strip(), "segments": [],
                    "timestamp_status": "discarded_invalid"}
        for segment in raw_segments:
            try:
                start, end = float(segment["start"]), float(segment["end"])
                segment_text = segment["text"]
                if (not 0 <= start < min(end, duration) or not end <= duration + 0.5
                        or not isinstance(segment_text, str)):
                    raise ValueError
                segments.append(
                    {"start": start, "end": min(end, duration), "text": segment_text[:20000]}
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                # Timing is optional. Do not invent alignment or repeat a successful
                # recognition request because the provider's timing is unusable.
                return {"text": text.strip(), "segments": [],
                        "timestamp_status": "discarded_invalid"}
        return {"text": text.strip(), "segments": segments}

