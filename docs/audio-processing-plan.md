# Recall-first audio processing

## Objective and evidence

Improve transcription of quiet speech and noisy conversations without deleting evidence,
changing capture clocks, or mistaking a quieter simultaneous speaker for noise. Research and
primary-source references are in [audio-processing-research.md](audio-processing-research.md).

The archive contains mono 16 kHz PCM16. The phone decodes and journals audio before
uploading. The backend has gain, pause-aware request boundaries, optional speech filtering,
and separately reviewable enhancement candidates. These mechanisms establish processing
contracts; they do not establish recognition accuracy on real conversations.
Keep capture, ingestion, retry IDs, original playback, and audio hashes unchanged.

## Quality programme: transcription before interpretation

Priority confirmed on 2026-11-05: Danish, English and language switching; missing/incorrect
words first, followed by bloated summaries and labels. Work advances in the order below.
Passing synthetic or mocked tests is not evidence of matching or exceeding Plaud.

1. **Transcription foundation.** Keep full audio eligible for recognition by default.
   WebRTC non-speech predictions can miss quiet words and short replies, so destructive
   `stt_filter_speech` is opt-in. Exact digital silence still needs no model request.
   Keep canonical text separate from readable rewrites and enhancement candidates.
   Preserve timestamps, language switches and spelling hints through every provider adapter.
   Compare the exact same audio and human-checked reference with the offline evaluator.
2. **Recognition quality.** Freeze held-out recordings before tuning models or settings.
   Include clean, quiet, distant, noisy and overlapping speech; Danish, English and mixed
   turns; names, jargon, numbers, negation, short answers and silence/noise negatives.
   Split by conversation and speaker, not adjacent clips from the same conversation.
   Report corpus WER/CER, deletion/insertion counts, phrase recall and false speech for each
   language and acoustic condition. Review names, numeric values and negations against audio
   separately: punctuation-normalized word metrics cannot establish their semantic accuracy.
   Compare latency/cost on the actual deployment and record effective upstream model/revision,
   configuration and audio/reference hashes. `managed-stt` alone is not model provenance.
   Establish numerical release thresholds from this baseline, before selecting a candidate;
   an aggregate gain cannot excuse worse quiet-voice omissions or mixed-language performance.
3. **Speakers.** Evaluate anonymous speaker turns and word attribution before identity.
   Measure diarization against human timing/labels, including overlap and short interjections.
   Keep uncertain identity unassigned; apply names only with supported recognition or explicit
   confirmation. Speaker labels do not recover words lost during recognition.
4. **Summaries and labels.** Measure factual support, important omissions and useful compression.
   Use the minimum detail needed to retain decisions, reasons, changes, open questions and
   supported actions. Do not manufacture actions, owners or deadlines. Every substantive
   claim needs traceable transcript/audio evidence. Remove repeated points and redundant
   synonymous labels; use specific topic names in the source language. Do not force ordinary
   conversations into meeting templates or reward summaries for being longer.
5. **Daily recap, then chat.** Reconcile repeated subjects across recordings, preserve chronology
   and changed decisions, and select what matters rather than concatenate summaries. Chat
   retrieves original evidence within the requested scope, provides playable references and
   acknowledges missing support. Transcription/speaker corrections must invalidate derivatives.

For a fair Plaud comparison use **Original text**, with its transcript version recorded;
cleaned wording is a separate presentation evaluation. Plaud output can be a comparison
reference, but only listening and human correction establishes ground truth. Its current
documentation distinguishes [original and cleaned transcripts](https://support.plaud.ai/hc/en-us/articles/63429950838425-Cleaned-up-transcripts-for-v4-0-x-or-later)
and documents [one language per recording](https://support.plaud.ai/hc/en-us/articles/63429935417241-Transcription-for-v4-0-x-or-later).
Danish/English code switching is therefore an explicit Daythread requirement, not inferred
Plaud parity. Plaud's [custom vocabulary](https://support.plaud.ai/hc/en-us/articles/63429935673625-Custom-vocabulary-in-the-Plaud-Mobile-App-for-v4-0-x-or-later)
and [summary source tracing](https://support.plaud.ai/hc/en-us/articles/63429908688153-Trace-summary-content-back-to-the-transcript-for-v4-0-x-or-later)
provide useful product targets, not measured accuracy guarantees.

The initial change does not migrate saved settings or reprocess completed recordings.
An existing saved `stt_filter_speech=true` remains an explicit override; turn it off for a
full-audio baseline. Retaining more audio may increase inference time and noise exposure,
so false speech and throughput are acceptance measures alongside omissions.

Before the speaker stage, also fence duplicate-transcript reuse with alignment configuration.
The audit reproduced an upload reusing an older unaligned result after alignment was enabled;
the current recognition fingerprint only includes STT settings. This remains follow-up work,
separate from recognition accuracy and the default-filtering change.

## First private Plaud benchmark, 2026-11-05

The initial corpus contains 12 excerpts (783.28 seconds): five Danish, five English and two
mixed-language candidates from four existing Plaud recordings. Calibration and held-out
recordings are separate. There is only one sustained language transition; the other mixed
case contains a short English clause in Danish, so it cannot establish broad code-switching
quality. Audio, original exports, fixture hashes, model/runtime observations and outputs are
kept in the ignored private benchmark directory, not committed as public fixtures.

References are raw Plaud transcript excerpts, not human-corrected ground truth. Plaud processed
the full recordings while Daythread processed the excerpts: report this context advantage.
Word/character scores measure disagreement with vendor text, not accuracy. No recording is
labeled acoustically clean, quiet or noise-only without listening; `unknown` is supported for
these cases. Transcript gaps alone do not establish silence.

The live replay used the configured Whisper large-v3 engine. The first filtering comparison
removed only 4.08 seconds but increased request count from 31 to 37. It produced isolated
1.58- and 1.88-second requests; the latter returned Chinese text in an otherwise English
excerpt. This motivates selecting complete original STT windows with VAD rather than
cutting within them. Keep normal chunk limits and source offsets, and skip only windows
without any padded speech evidence. Bump the filtering contract so new processing cannot
reuse results from the previous fragmentation policy.

A separate 60-second request experiment did not justify raising the default. On the sustained
language-switch case, it rendered the Danish passage in English where both the raw Plaud
reference and 30-second Daythread outputs retained Danish. Keep the 30-second default.
Repeated identical requests also yielded different transcripts, including when requesting
zero temperature increment; effective native receipt of that override was not verified.
Do not attribute every single-run score difference to preprocessing or declare an accuracy
winner. Listening, corrected references and repeated held-out runs remain necessary.

The four-variant replay completed all 48 case runs. The implemented window-selection policy
used 31 original requests, retained all 783.28 seconds in this corpus, and avoided the Chinese
output seen in the short-fragment case on its candidate replay. Its request audio, gain and
offsets match the 30-second full-audio baseline; wording still varies between runs. The new
policy passed 180 focused tests and independent request-contract review. These are local code
changes and private measurements; the production configuration and existing recordings were
not changed.

### Identical-audio reliability investigation

The failure was reproduced with byte-identical prepared WAVs and multipart requests. Both
the production path and direct native inference returned three different texts in three
calls, including with temperature fallback disabled. Three fresh native processes returned
the same text; repeated greedy decoding in one process was also stable. This localized the
defect to persistent beam-decoder state in the pinned Whisper runtime, rather than VAD,
request boundaries or changing model weights.

The pinned whisper.cpp source sorted beam candidates but then sampled from the probability
distribution instead of using those sorted candidates. It also left decoder zero's random
generator advanced between requests. The HomeCompute patch selects distinct highest-ranked
candidates with a stable token-ID tie break and initializes every decoder's random state for
each request. It retains the model, decoding defaults and timestamp/probability metadata.
Native regression probes reproduce the original failures and verify both corrections.

`scripts/check_transcription_repeatability.py` repeats identical prepared request bytes and
fails on text changes, failed calls, reported cache hits or unverified runtime identity.
Pin the model hash and image identity when qualifying a locally patched runtime: its base
upstream commit alone cannot distinguish the original and patched binaries. Benchmark
validation uses new output directories with `--fresh` and pinned response provenance;
cached transcripts cannot demonstrate decoder repeatability. Passing these checks is
evidence for the tested recordings and configuration, not a universal determinism or
recognition-accuracy guarantee. Speaker work remains downstream of this gate.

The patched CUDA candidate completed two fresh corpus passes with filtering on and off:
48 successful outputs from 124 original-window provider responses, with no cache reuse or
enhancement extras. All 12 excerpts had identical text across their four executions. The
24 same-configuration repeated pairs also matched in segments and meaningful processing
metadata. Three default-profile repeats and three no-fallback repeats each matched exactly;
three more default repeats after the corpus matched the original default request and text.
Every response carried the pinned model, runtime, build-source and image identities.

This establishes repeatability on this corpus, not improved word accuracy. Disagreement
with the raw Plaud reference was 41.05%, versus 40.69% in the earlier unstable full-audio
30-second baseline. Danish changed from 38.59% to 39.79%, English from 35.46% to 33.39%, and
mixed-language from 56.17% to 57.74%. The sustained switch retained both English and Danish.
These vendor-reference scores and the differing full-recording/clip context do not establish
human-reference accuracy; they do not support an overall quality-gain claim. Candidate
latencies include an isolated SSH bridge and are not directly comparable to gateway timings.

The runtime fix is committed locally in HomeCompute as
`bdb35cb972abe273d64ac4087b32a2ca2a2c61b9`; the tested image is
`sha256:2a9e90e60708dbfb6c95c68b84bb93385cccdeb6a3861761347a8f045f406164`.
Production remains unchanged. Its shared promotion also exposes a full-recording route,
so release work needs representative long-request evidence, coherent qualification and
maintenance/catalog pins, publication, and the guarded privileged lifecycle. The private
benchmark report and rollout note retain the exact evidence and remaining release work.

## Implementation

1. **Preserve quiet voices by default.** Replace the default positive RMS gate with an exact
   digital-zero check. Any nonzero sample is eligible for recognition, including faint voices,
   noise, and overlapping speech. Retain the old configurable gate behind an explicit opt-out
   of `stt_preserve_quiet`. Never interpret an empty transcript as proof of no speech.
2. **Make a conservative transcription copy.** Apply uniform gain only when the recording is
   quiet: target -25 dBFS RMS, at most +12 dB by default, and a peak ceiling of 0.9 full scale.
   Never attenuate, clip, gate, compress, or change sample count in this pass. Gain cannot
   improve signal-to-noise ratio or uncover a voice masked by another speaker.
3. **Improve request boundaries without discarding pauses.** Search the last 5 seconds of
   each maximum-duration request for a quiet gap of at least 250 ms with default settings. Split inside that gap;
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
