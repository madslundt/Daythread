# Audio preprocessing research

Investigated 2026-10-16 against upstream documentation and source. Recommendations below are engineering judgments, not measured recognition improvements. Implementation and evaluation belong in a separate plan.

## Existing behavior and priorities

At investigation time, `src/omi_local/audio.py` had an RMS gate over 100 ms PCM16 windows and fixed-duration transcription slices. `worker.py` skipped transcription when every window was below `silence_threshold` (default 35), then marked that recording complete. `inference.py` requested a local OpenAI-compatible STT endpoint, supported optional segment timestamps and language routing, and offset slice timestamps into the original recording. There was no noise suppression, voice detector, speaker separation, or speech amplification. The archive accepts mono PCM16 at 16 kHz; originals, capture bounds, stable IDs and provenance must stay authoritative. See [architecture](architecture.md).

For quiet/distant voices, avoiding deletion is the first improvement. A positive amplitude threshold cannot distinguish quiet speech from quiet noise. Removing pauses or letting a denoiser suppress a distant voice can lose relevant evidence. Digital-zero samples contain no recoverable signal; that narrow case can be skipped without imposing a speech loudness threshold. Keep broader silence gating an explicit operator tradeoff.

Do not equate these distinct tasks:

- **Capture:** preserve all received audio and missing-frame evidence.
- **Enhancement:** improve a derived signal before transcription; never overwrite the archive.
- **Detection:** estimate whether/where speech occurs, with false negatives possible.
- **Diarization:** estimate which speaker spoke when.
- **Separation:** produce candidate signals for simultaneous speakers, then transcribe them independently.

## Local denoising choices

| Choice | Primary-source evidence | Practical fit |
|---|---|---|
| FFmpeg `afftdn` | FFT denoiser with noise-reduction dB, noise-floor, noise tracking and gain smoothing controls. [Official filter reference](https://ffmpeg.org/ffmpeg-filters.html#afftdn) | Practical optional first implementation: no model assets or Python numerical runtime. Start with mild attenuation, retain raw fallback, benchmark real speech. |
| FFmpeg `anlmdn` | Broadband Non-Local Means filter with strength and search/patch radius controls. [Official filter reference](https://ffmpeg.org/ffmpeg-filters.html#anlmdn) | Another model-free candidate. Its context search adds a tuning/resource dimension; no reason to adopt it before an A/B comparison. |
| FFmpeg `arnndn` | Recurrent neural speech denoiser; requires an explicitly supplied model and supports processed/raw mixing. [Official filter reference](https://ffmpeg.org/ffmpeg-filters.html#arnndn) | Useful optional experiment when a specific local model is installed and versioned. Model choice becomes part of deployment and evaluation. |
| RNNoise | Native speech/noise suppression. Official demo uses raw mono PCM16 at 48 kHz; build setup downloads model assets. [Upstream README](https://github.com/xiph/rnnoise) | Lightweight native deployment possible, but needs explicit asset provisioning and resampling for this archive. Do not copy the demo's I/O contract directly. |
| DeepFilterNet | Full-band speech enhancement; upstream binary expects 48 kHz WAV, supports delay compensation; Python path uses Torch/Torchaudio. [Upstream README](https://github.com/Rikorose/DeepFilterNet) | Strong evaluation candidate in an isolated runtime or separately installed binary; unsuitable as an unexamined mandatory dependency in this small Python 3.15 backend. |

FFmpeg's documented `afftdn` default reduction is 12 dB. A conservative initial experiment can use lower reduction, for example 6 dB, with raw audio available. This number is a starting hypothesis, not a quality result. Avoid assuming the first part of every recording is noise; it can contain speech. Noise profiling and floor tracking need representative tests. [FFmpeg options and examples](https://ffmpeg.org/ffmpeg-filters.html#afftdn)

DeepFilterNet's implementation defaults to a named model that may download missing assets. Its `enhance` function offers attenuation limits and delay compensation; uncompensated output can be shorter and delayed. Explicit local paths, installation-time downloads, pinned revisions and output-length/onset tests are prerequisites for an offline deployment. [Enhancement implementation](https://github.com/Rikorose/DeepFilterNet/blob/main/DeepFilterNet/df/enhance.py)

The DeepFilterNet paper reports real-time speech enhancement on a notebook CPU; those are its experimental conditions, not a latency guarantee for this installation or proof of lower word error rate. [Authors' paper](https://arxiv.org/abs/2305.08227)

No reviewed denoising source establishes that its model preserves every quiet overlapping speaker in Omi pendant recordings. Treat suppression of speech-like interference as a risk to test. Uniform gain raises noise and speech together; it does not improve their signal-to-noise ratio or undo clipping. Bounded gain can still make low-amplitude samples usable for endpoints with level sensitivity, provided sample count and timing are unchanged and peaks cannot clip.

## Voice detection and silence

Silero supports 8/16 kHz, JIT/ONNX runtimes, and a small model. Its published CPU speed is an upstream benchmark, not a deployment guarantee. [Project documentation](https://github.com/snakers4/silero-vad)

Its timestamp utility documents a tunable probability threshold, speech padding, minimum silence and minimum speech duration. The current default minimum speech duration is 250 ms and shorter detected speech is discarded. A short “yes”, quiet interruption or distant voice therefore requires explicit recall testing; do not adopt defaults as a lossless filter. [Upstream timestamp implementation](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py)

WebRTC's Python binding accepts mono PCM16 in 10/20/30 ms frames at supported rates including 16 kHz. Aggressiveness ranges 0–3, with 0 least aggressive about rejecting non-speech. It uses a native binding rather than a downloaded neural model. [Binding's documented interface](https://github.com/wiseman/py-webrtcvad)

Recommended policy: no hard model-based rejection by default. If a detector is later added, first use it as advisory segmentation, with generous context and raw fallback when it finds no speech. “No detections → transcribe raw” alone does not protect a quiet voice missed between two detected voices; interval deletion needs its own low-recall-risk evaluation. Missing/runtime-failed VAD should fail open to the full recording. Do not enable `torch.hub` network fetches in processing.

The faster-whisper library integrates Silero, describes its ordinary default as only removing silence longer than two seconds, and enables VAD by default for batched transcription. Inspect the actual local server: client-side full audio does not prevent a provider's internal gate from removing it. [Upstream VAD documentation](https://github.com/SYSTRAN/faster-whisper#vad-filter)

## Boundaries, timestamps, and overlapping speakers

Whisper's reference transcription processes sliding 30-second windows. Its source combines no-speech probability with token log probability rather than using amplitude alone, and warns that previous-text conditioning can cause repetition or timestamp failure loops. Compatible HTTP endpoints do not universally expose these settings; configure them in the local STT service and test the deployed model. [Reference usage](https://github.com/openai/whisper#python-usage), [transcription implementation](https://github.com/openai/whisper/blob/main/whisper/transcribe.py)

Fixed cuts can split words. Prefer a bounded search near each configured maximum for a local energy minimum, preserving every sample. Never select an earlier cut and discard the remaining audio; the next request begins at exactly the prior end. At a persistent busy/noisy boundary, retain full coverage even if a clean pause cannot be found. This improves internal cuts only: existing phone/upload recording boundaries are a separate context limitation. Arbitrary overlap requires reliable timestamp/text deduplication and can create duplicated words; do not add it without that contract.

For any enhancement, require mono PCM16, 16 kHz, the original number of frames, and no time stretching or concatenation of separated intervals. Exact length alone is insufficient: test impulses/onsets for filter delay and compensate using a documented method. Offset validated provider segments by original sample positions. Reject malformed output and fall back to raw audio instead of accepting shifted evidence. If text-only responses lack segments, retain chunk-level timing and avoid fabricated alignment.

For `afftdn`, current source sets `sample_advance = sample_rate / 80`, `window_length = 3 * sample_advance`; `output_frame` appends input after an offset of `window_length - sample_advance` and emits from the start of the output buffer. This implies 400 samples (25 ms) of signal delay at 16 kHz, consistent with the implementation team's impulse experiment. Compensate by appending that many zero input samples before filtering, then removing that many leading output samples, retaining exactly the original frame count. Test impulses near both ends; trimming only the leading output loses the recording's final 25 ms. This is a source-derived inference about the inspected implementation, not a stable public delay API: keep a real FFmpeg onset regression test for deployed upgrades. [Upstream `afftdn` implementation](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/af_afftdn.c)

WhisperX explicitly lists overlapping speech as poorly handled by both Whisper and WhisperX, and imperfect diarization as a limitation. Adding speaker labels alone therefore does not meet the requirement to hear simultaneous speakers. [Upstream limitations](https://github.com/m-bain/whisperX#limitations-%EF%B8%8F)

A future local overlap route can detect candidate intervals, run a pinned separation model, transcribe each candidate stream and store overlapping timestamps plus anonymous speaker labels/provenance. Keep the original mixture and original-path transcript as evidence; do not silently concatenate incompatible alternative hypotheses. A pyannote separation model is available, but its model page requires agreement to share contact information before access. [Model card and access terms](https://huggingface.co/pyannote/speech-separation-ami-1.0)

Pyannote provides diarization/overlap components; its documented optional telemetry has an explicit disabling environment variable. Any later local installation must set offline assets and telemetry policy deliberately. [Upstream features and telemetry](https://github.com/pyannote/pyannote-audio#telemetry)

## Recommended first implementation

1. Change the out-of-box gate to retain low nonzero audio. Keep explicit legacy threshold configuration documented, since stored overrides may survive changed defaults.
2. Add time-preserving, clipping-safe and capped gain on a disposable transcription copy. Keep raw as a selectable mode; do not claim improved SNR.
3. Offer optional mild FFmpeg enhancement with no model download. Run bounded subprocesses outside the event loop; limit input/output size, stderr, threads and elapsed time; kill/reap on cancellation. Missing executable, nonzero exit, malformed/empty/length-changed output must use raw fallback. Do not let stderr disclose recordings, paths or provider bodies.
4. If an enhanced successful STT call returns empty while raw input has signal, perform at most one raw rescue pass within the existing total inference deadline. A nonempty transcript is not proof all speakers survived, so avoid aggressive defaults. Do not merge enhanced/raw text using an LLM as though new words were established evidence.
5. Preserve full sample coverage when splitting; use bounded near-boundary pause search where possible. Keep source IDs, stored audio, capture bounds and segment offsets intact.
6. Document the overlap limitation and the advanced local separation route. Keep optional detector/neural models opt-in until available hardware and realistic recordings support them.

An even more conservative first release keeps the raw/gain-only transcript canonical and presents the enhanced transcript as a separate optional review candidate. Do not index or automatically merge unreviewed alternative words. This makes partial suppression/differences inspectable; it also means cleanup does not automatically improve downstream recall until reviewed. Default such a second pass off pending real-speech evaluation. Give it a separate bounded budget after baseline success, and make failures nonfatal to baseline completion. The gain-only path and all preprocessing choices still need clear provenance. This is the recommended plan-review adjustment when there is no representative speech evaluation set available yet.

## Review and acceptance evidence

Review the plan for false-negative paths before coding: silence gate, detector deletion, denoiser suppression, peak clipping, internal cuts, existing capture boundaries, provider VAD and language routing can each omit words. A raw-rescue pass addresses empty enhanced results, not partial omissions. An unbounded second-pass policy can double all-day inference cost; keep retry count and total deadline explicit.

Automated regression checks should cover low nonzero speech-like signal below the old threshold, exact silence, quiet material after a loud transient, clipping prevention, unchanged frames, internal boundary coverage and timestamp offsets, malformed/failed/timed-out enhancement, cancellation cleanup, empty enhanced result rescue, language routing, and unchanged archive bytes/identity. Synthetic waveforms establish these contracts, not transcription accuracy.

Before enabling a stronger default denoiser/VAD/separator, use a consented local evaluation set and human reference text: Danish and English; close/quiet/distant speakers; fan/traffic/cafe/wind/clothing noise; clean speech; short interjections; single/alternating/overlapping speakers at unequal levels; clipping and packet gaps. Compare raw, gain-only, mild FFT, and optional neural enhancement using the same STT model/configuration. Report word/character error, omissions of the quieter speaker, false words during noise, onset/offset error, processing p50/p95, peak memory and queue backlog. Listen to removed-noise output when available to find speech suppression. Require no added quiet/overlap omissions in the acceptance set before adopting stronger processing. Real-device placement, capture loss, local endpoint behavior and model availability remain necessary empirical checks.
