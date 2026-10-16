# Recall-first audio processing

## Objective and evidence

Improve transcription of quiet speech and noisy conversations without deleting evidence,
changing capture clocks, or mistaking a quieter simultaneous speaker for noise. Research and
primary-source references are in [audio-processing-research.md](audio-processing-research.md).

The archive contains mono 16 kHz PCM16. The phone already decodes and journals audio before
uploading. The backend currently rejects low RMS recordings before STT and splits long uploads
at fixed request boundaries. It has no speech detector, enhancement, or speaker separation.
Keep capture, ingestion, retry IDs, original playback, and audio hashes unchanged.

## Implementation

1. **Preserve quiet voices by default.** Replace the default positive RMS gate with an exact
   digital-zero check. Any nonzero sample is eligible for recognition, including faint voices,
   noise, and overlapping speech. Retain the old configurable gate behind an explicit opt-out
   of `stt_preserve_quiet`. Never interpret an empty transcript as proof of no speech.
2. **Make a conservative transcription copy.** Apply uniform gain only when the recording is
   quiet: target -25 dBFS RMS, at most +12 dB by default, and a peak ceiling of 0.9 full scale.
   Never attenuate, clip, gate, compress, or change sample count in this pass. Gain cannot
   improve signal-to-noise ratio or uncover a voice masked by another speaker.
3. **Improve request boundaries without discarding pauses.** Search the last 0.5 seconds of
   each maximum-duration request for a quiet gap of at least 100 ms. Split inside that gap;
   otherwise use the existing fixed boundary. Retain every sample exactly once and preserve
   source-relative offsets. This only affects internal request splits, not phone chunk edges.
   Do not deduplicate repeated words or concatenate disjoint speech as continuous audio.
4. **Add bounded local enhancement.** `stt_enhancement_mode=on_empty` by default tries a gentle
   FFmpeg noise-reduced/leveled copy only for chunks whose original pass returned empty.
   `compare` also transcribes enhanced versions of nonempty chunks for human comparison;
   `off` disables it. Always finish every original pass first. Limit all optional work to
   15 seconds per source and the remaining inference deadline; failures retain original
   results and disclose a sanitized status. FFmpeg is optional; no model downloads or hosted
   fallback. Use fixed mild FFT reduction (6 dB), bounded dynamic leveling (4x), one filter
   thread, raw pipes, no shell, and a strict output-length bound. Compensate the FFT filter's
   400-sample delay by padding the tail and trimming the head. Preserve exactly N samples.
5. **Keep conflicting readings separate.** Store versioned processing details, gains, chunk
   offsets, selected pass, enhancement availability, and candidate text/segments atomically
   with the completed transcript. Original results always remain authoritative, including an
   empty original with a nonempty cleanup candidate. Never merge different readings using an
   LLM, longest-text heuristic, or word overlap. Show alternatives and recovery notices beside
   original playback in the web source view; use the existing audited correction editor to
   save reviewed changes. Candidates never enter search or generated evidence before correction.
   Corrections clear stale candidates; transcript retention/deletion clears all candidate text.
6. **Make acoustic quality measurable.** Add a local fixture evaluation command with WER,
   deletion rate, required phrase recall, false speech on noise-only fixtures, and latency,
   grouped by clean/noisy/quiet/overlap and language. Require explicit live inference opt-in;
   no live archive access or automatic fixture/model downloads. Provide a manifest example.

Settings are validated by the existing server configuration API and exposed by its generated
editor. Document optional FFmpeg setup and tradeoffs. Existing recordings are not reprocessed
automatically; new work uses the active settings. Offer explicit retry for retained recordings
with empty/silence-gated automatic outcomes. Do not overwrite nonempty or corrected transcripts
through this retry path.

## Review before implementation

The research review identified four risks and the plan incorporates their mitigations:

- A VAD false negative can permanently hide faint/overlapping speech: do not use VAD to discard
  samples or skip nonzero recordings. Defer neural VAD until measured on actual pendant audio.
- A denoiser can erase another voice or produce false words from noise: preserve the unfiltered
  pass and require review for every alternative, including empty-original recovery. The final
  research review rejected automatic promotion of empty recovery before acoustic evaluation.
- Equal duration does not establish equal timing: FFmpeg `afftdn` adds 25 ms at 16 kHz.
  Pad/compensate and test first/last-frame impulses through the full denoise/leveling chain
  instead of checking only WAV duration.
- Optional work can make successful recognition fail: budget it after original passes and
  contain timeouts, missing executables, malformed output and provider errors separately.

Scope review: diarization labels speakers but does not recover simultaneous speech. A dedicated
overlap-aware ASR/separation service is a later model-deployment decision, requiring a local
endpoint, pinned model/revision, latency measurements, speaker permutation handling and
per-speaker reference transcripts. Do not claim overlap recovery from amplitude adjustment,
denoising or synthetic waveform tests. Keep the full mixture available to current/future models.

## Verification and rollout

- Regression-test sub-threshold voices, exact zero, noisy/mixed synthetic signals, bounded gain,
  no clipping, every-sample retention, quiet boundaries, unchanged archive identity and offsets.
- Exercise real installed FFmpeg for frame count, first/last events, suppression of synthetic
  stationary noise, cancellation/timeout cleanup, and missing/corrupt/short/long output fallback.
- Mock STT for empty recovery, disagreement, failure, language routes, total optional budget,
  baseline timeout, atomic persistence, stale correction races, retention and candidate isolation.
- Test source review UI and settings validation; run backend tests/lint and web tests/build.
- Before enabling `compare` for everyday capture, evaluate clean/whispered/distant speech in
  Danish/English/mixed language, cafe/traffic/wind noise, turns and simultaneous speakers with
  quiet/loud role reversals, and noise/music-only negatives. Use heldout speakers and rooms.
  Report per-category deletion/phrase recall and false speech; never accept an aggregate gain
  that hides loss of quieter speakers. Human-listen overlapping references; WER alone is
  sensitive to the arbitrary order of simultaneous words. Measure target-machine p95 latency.
- No real audio fixtures or reachable model service are assumed. Automated mechanics passing
  establishes pipeline behavior, not acoustic accuracy or recovery of every spoken word.

## Implementation and verification result

Implemented the preservation/gain/boundary pipeline, optional FFmpeg review candidates,
atomic processing details, retention/correction cleanup, explicit empty-source retry, generated
configuration descriptions, web comparison/correction flow and local evaluation command.
The Docker image now installs FFmpeg; standalone installations can fail open without it.

Validation on 2026-10-16:

- Full backend suite: **672 passed**. After the final readability change and four additional
  policy cases, all **43 focused audio/chunk/evaluation tests passed**.
- Web: **129 passed**; TypeScript/Vite production build and backend asset packaging passed.
- Repository lint and `git diff --check` passed. Docker `build --check` passed without warnings;
  a complete image build was not run for this change.
- Real installed FFmpeg passed full-chain length/onset/tail tests, including 1/399/400/401-frame
  inputs. Invalid output, timeout, cancellation, missing executable, provider failure, shared
  optional budget, candidate isolation and concurrent correction tests passed.
- In-app browser, disposable archive, default 1280×720 viewport: page identity, meaningful
  content, no framework overlay, no console warnings/errors, alternative display, canonical
  editor preservation, reviewed save and stale-alternative removal passed. Mobile viewport
  and native candidate review were not exercised; native candidate review is not implemented.
- The prescribed `uv run` command was blocked by installed uv 0.11.32 versus required >=0.13.1.
  Checks used the existing project `.venv` with Python 3.15 directly instead.
- The configured local STT model-list probe failed with `ConnectTimeout`. No live acoustic
  accuracy measurements or simultaneous-speaker recovery claims were produced. The labeled
  real-audio acceptance evaluation and dedicated overlap-aware model service remain outstanding.
