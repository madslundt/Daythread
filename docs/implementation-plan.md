# Implementation plan

Reviewed 2026-10-16 for the expanded scope: finish the backend, native iPhone companion, and a responsive local web application. Application/API authentication is intentionally disabled as requested. The earlier starter-only boundary is superseded.

## Review decisions

1. One FastAPI backend serves the built web client and shared capture, timeline, diary, rewind, chat and settings APIs. Native CoreBluetooth remains necessary for iPhone background wearable capture.
2. Preserve original capture time and durable IDs in the phone outbox; persist gaps and originals before upload. Network failure must not erase unsent audio or toggle a meeting twice.
3. Implement hold when verified, double click as its fallback, then phone controls. Preserve the 60-second flag cooldown and minimum meeting duration, and treat button timestamps as approximate contextual hints.
4. Complete archive browsing/correction/versioning, persistent chat, date-aware retrieval, runtime settings, explicit retention, and portable backup/restore. Generation endpoints alone are insufficient for daily use.
5. Empty, pending, disconnected and local-model failure states are part of the app. No sample recordings or fabricated AI responses belong in the live archive.
6. Software completion requires regression tests, a web build and responsive browser checks, native core tests, and an unsigned iPhone app build. Physical reliability and local model quality remain acceptance checks on the actual devices.

## Software delivery status

Backend, native companion, responsive web client, combined packaging, shared settings/archive/chat APIs, durable capture/gaps, retention, and backup/restore are implemented. The hardware-readiness review on 2026-10-16 passes **167 backend tests, 39 native core tests, and 26 web tests**. It fixes interrupted-frame capture pauses, duplicate packet timestamps, failed outbox saves, storage reservations, exact upload receipts, pause synchronization races, concurrent settings updates and backup/retention races. Launchers explicitly select one backend worker. See [current validation and installation](README.md) for build checks and rollout order.

The combined semantic-retrieval and editable-inbox integration passes **258 backend tests, 41 native core tests, and 28 web tests**, Ruff, the production web build, an unsigned iOS Simulator build and Python packaging checks. These tests use controlled inference, not a verified embedding model.

Earlier desktop/mobile web interactions and uploads were tested against the backend; populated flows used a separate disposable archive and simulated inference. The Python wheel was inspected to verify that it contains the web assets. Physical pairing and all-day screen-locked endurance remain unverified. Mixing web meeting controls with queued offline phone toggles can temporarily display an incorrect local meeting indicator until synchronization and refresh; exact offline reconciliation needs a richer server event snapshot.

The steps below remain the acceptance criteria for the delivered code and its hardware rollout. Actual pendant/iPhone pairing, screen-locked all-day endurance, local model quality, and deployment to home-core require the physical devices and a reachable home network. Optional embeddings now have an implemented, disabled-by-default local adapter/index/retrieval path. Speaker identity and pendant storage import remain unimplemented; no embedding model is chosen speculatively.

## Manual conversation preparation — implemented

The first release adds manual person/project/topic preparation from Today/Day without a new primary tab. Literal aliases/qualifiers and optional server-calendar dates find newest matching recordings; the user selects evidence and confirms the latest relevant recording. A separate ephemeral user-context draft accompanies structured recap, promises to check, recorded questions and suggested questions. Every generated item requires an exact source excerpt. Content/retrieval fingerprints reject stale generation and focus/foreground validation removes stale displayed evidence while keeping the draft.

Deliberate first-release limits: individual recordings instead of heuristic discussion grouping; literal substring search instead of semantic retrieval; anonymous commitment owners and unknown completion state; no persistent preparation/context/cache, calendar, notifications or messaging. Long sources are excerpted with explicit coverage and omitted-character counts. Preparation does not consume inbox statuses, decision records or the archive’s optional hybrid retrieval; its future adapters must preserve that distinction from original recorded evidence. Transcript-only imports are supported, and corrections remain distinct from user-authored context.

Regression coverage includes recency versus older keyword density, literal names/aliases/qualifiers and Unicode folding, strict citation/excerpt validation, selected evidence budgets, context separation, imported audio provenance, stale selections, correction/deletion/retention/new-recording/pending/gap/timezone races and Copenhagen DST boundaries. Browser checks use a separate disposable archive and fake inference. The preparation build passes 195 backend tests, 28 web tests, 39 native core tests, a production web build and an unsigned iOS Simulator build. The native typed prep client also completed a search/generate/validate round trip against the disposable fixture server. Mock tests and successful builds do not verify local-model factual quality or physical capture reliability.

## 1. Backend foundation — implemented

Python/FastAPI service, SQLite WAL, audio files, local HTTP inference adapters, durable transcription retry, event-based meeting projection, important markers with 60-second cooldown, rejection of meeting hints shorter than one minute, surrounding-context hint metadata, exact audio rewind with gap information, recap scheduler, Markdown diary export, and evidence chat. Complete the shared archive/settings/chat APIs and serve the web build from the same backend. No application authentication is required.

Software validation covers the processing pipeline, idempotent ingestion, restart recovery, reordered events, configurable meeting expiry, WAV boundaries, DST day boundaries, and late evidence refreshing a diary. Live local models and physical capture have not been tested.

## Weekly review — first web/backend slice implemented

Diary → Weeks provides on-demand adjacent-week review with supported decisions, qualified commitments, recurring discussions and cited changes. Original transcripts (including transcript-only imports) enter bounded extraction without chat sampling or saved recap prerequisites. Local Monday boundaries, DST, partial cutoffs, missing comparison evidence and received-archive coverage are explicit. Exact quote/schema validation, immutable versions, force regeneration, durable jobs, scoped chat handoff and Markdown export are implemented. Fingerprint validation prevents in-flight evidence mutations from publishing; deletion/transcript retention purge weekly versions/jobs and restored export projections. Backups preserve versions and durable work.

Integrated validation: 315 backend tests and 36 web tests, Ruff, TypeScript/Vite production build and Python package build pass alongside the existing inbox, decision register and optional semantic retrieval. Added weekly tests cover both-window arrivals, cross-batch explicit resolutions, restart/idempotency, correction/deletion/retention/timezone races, DST, citation/category validation and backup/restore cleanup. Disposable-archive desktop/mobile browser checks cover generation, correction/staleness, history, regeneration, export, chat handoff, empty partial weeks, failure and deletion. Real local-model quality and hardware capture remain unverified.

Next slices: native typed review/source UI; opt-in local Monday scheduling with catch-up and debounced late-evidence refresh; then consumption of existing structured inbox identity/state with explicit completion/cancellation provenance. Weekly synthesis currently reads original transcripts directly and should later consume that read interface without mutating commitment state. Notifications and semantic retrieval remain separate work.

## 2. Prove the actual pendant and iPhone capture path

The native Swift iPhone app implements pairing, device/firmware/codec and battery display, BLE frame reassembly, real Opus decoding and persistent recording. Review [device research](device-research.md) before physical validation.

Acceptance:

1. Capture and play back a 10-minute real conversation with known elapsed duration.
2. Record discontinuities on BLE packet loss and reconnect, without joining unrelated speech.
3. Run with screen locked, app backgrounded, normal phone use, and movement in/out of range. Verify iOS restoration and distinguish suspend/termination from a working connection.
4. Verify the actual single-click behavior and double-click timing. Prefer a distinguishable hold event when available, otherwise map resolved double-click events to the meeting action; do not assume constants in source mean firmware sends them. A recognized meeting gesture must not also emit important flags for its component taps.
5. Test stock power-off behavior before considering a custom meeting gesture. Record model and firmware identity. Any firmware change is a separate implementation and physical acceptance task.
6. Block Internet access and verify capture still works without an Omi account or external service.

Deliverable: a reliable capture spike and hardware capability matrix. This is the gate before promising an all-day recorder.

## 3. Durable phone queue and home-core connection

Persist original compressed audio, capture clock anchors, stable chunk UUIDs, and received button events before upload. Use file-backed background transfers, acknowledgements, retry with backoff, and local storage bounds. Decode 15–30-second WAV chunks into the backend's upload contract. Store backend configuration and credentials safely; trust your local TLS CA through normal platform mechanisms.

Acceptance: turn off Wi-Fi/mobile data for an hour while recording, restart the app, reconnect through LAN/VPN, and prove each chunk arrives once with original capture timestamps. Interrupt uploads after the server persists data and verify safe retries. Acknowledge a chunk only after the API confirms persistence. Display unuploaded backlog and genuine capture gaps separately. Verify behavior when storage is nearly full.

Deliverable: cloud-free ingestion from the real pendant through the iPhone to home-core. Add storage import only after testing supported hardware commands; no assumption that disconnected button gestures are retained.

## 4. Phone controls and rewind

Add connection/capture status, pause, mark-important, meeting toggle and timeout, a selected rewind duration (10 minutes by default), and audio playback with transcript/summary. The physical mapping is single click → important; verified click-and-hold → meeting toggle, with **double click as the fallback** and the phone control always available. Match the backend's capture-time cooldown in the UI, suppressing extra flag feedback during the minute without delaying meeting gestures.

Acceptance: toggle one meeting on/off, let another expire, restart phone/server during a meeting, and reconcile offline events. Show the chosen hold/double-click mapping. Reject a stopped meeting below 60 seconds and qualify one at 60 seconds; a provisional active indicator should acknowledge the first gesture. Repeated flags at 0, 20, 59 and 60 seconds must create only the 0- and 60-second flags; ignored attempts do not extend the cooldown. Interpret both types as approximate contextual hints, including speech before/after the presses. Verify no meeting double-click creates important flags. Fix an absolute rewind end timestamp before requesting audio and summary; show pending transcription and gaps. Later allow local-buffer rewind when the server is offline, with summaries deferred until a local model is reachable.

Deliverable: useful daily controls without needing to open the backend API.

## 5. Archive quality and diary

Tune STT on Danish, English and mixed-language examples using local fixtures. Add VAD and sensible segmentation/overlap handling; avoid forcing a single language on mixed recordings. Add optional diarization without inventing speaker identity. Store transcript corrections as auditable revisions, source offsets, and generated output versions.

Choose separate retention periods for phone uploads, server audio, transcripts, and diaries; implement deletion propagation through FTS and derived outputs. Add encrypted backups and test restoration. Provide pause and consent-aware recording controls in the phone UX. Use an outbound-network audit to verify that chosen inference services and dependencies do not route data to hosted providers.

Acceptance: a complete day produces a readable diary with decisions, commitments, important moments, open questions, and source playback. Late audio regenerates the correct day. Missing audio and STT uncertainty remain visible. Daily generation survives server downtime and a restored backup. Verify actual storage use and service latency on home-core.

## 6. Long-term knowledge and conversational recall

Date-aware day/range/meeting/session retrieval, persistent chat and optional local embeddings with hybrid search are implemented. Marker-only filters remain future work. Bounded contiguous local neighbors now fill spare topical source slots within the same scope/device and without crossing capture gaps. The initial embedding release uses versioned SQLite passages, independent bounded backfill, exact scoped vector scans, keyword fallback, bounded excerpt evidence and additive client provenance. New model sets backfill with partial coverage; no model is enabled by default. Retrieve from summaries for navigation and original transcripts for final evidence. Link each answer to a timestamp and audio source; reject unknown citation IDs. Keep personal facts distinct from inferred or uncertain claims and make correction possible.

Acceptance: answer questions across days, resolve “yesterday/last Tuesday,” find the exact source for a commitment, and abstain when evidence is missing. Test misleading transcript instructions as data. Evaluate retrieval separately from LLM answer quality. Do not claim a fact was learned merely because it appears in a model-generated summary.

The retrieval implementation is validated with controlled vectors and mock HTTP, long-tail broad reduction, degraded-evaluation rejection, FTS-normalized excerpts, history revalidation, adjacent-source bounds, a fictional 24-scenario/36-query baseline, the existing scope/DST regression suite and correction/deletion races. Real semantic quality, cosine calibration, target-machine scale benchmarks and answer-quality evaluation remain acceptance gates. Run [the independent retrieval evaluation](README.md#optional-local-semantic-retrieval) before enabling a selected model.

## 7. Responsive web app and operation

Implement Today, Rewind, Diary, Chat, and Settings with the same real backend as the iPhone app. Desktop sidebar navigation becomes bottom navigation on narrow screens. Include date navigation/search, source audio, transcript corrections, WAV upload, manual flags/meetings, recap generation/export, persistent chat sessions, and editable capture/policy/language/timezone/retention settings. Build the web client into the Python package/container so one process serves both interfaces on the private LAN, with no sign-in.

Acceptance: test actual controls and settings persistence in desktop and phone-sized viewports, inspect console/runtime errors, test model-unavailable and empty states, and compare the rendered UI to the design concept. Provide build/run scripts, a container recipe, and tested backup/restore without copying credentials. Native code must compile and its packet/outbox core must pass host-runnable tests.

The configured local gateway `ai.home.arpa:18080` and previous `192.168.30.122:18080` address were unreachable from this Mac during implementation. Real local STT/LLM quality and all-day Omi/iPhone background reliability require the acceptance checks above. Optional speaker diarization and the implemented semantic adapter require explicit local services; hardware storage import requires a verified model protocol and no speculative destructive commands.

## Borrow selectively

[Open-source inspiration](open-source-inspiration.md) identifies code and patterns to examine before implementing each phase. Keep this backend small: borrow capture contracts, reliability patterns, and retrieval ideas first. Evaluate licenses before copying code; no third-party implementation has been vendored here.

## 8. Editable inbox — implemented

The shared backend, responsive web Inbox and native Day → Inbox now support evidenced commitment/deadline/question proposals, cross-day status, manual edits/resets, duplicate dismissal, bounded historical scans and progress. Independent durable extraction jobs cover STT and Omi transcript imports. Transcript revisions, exact-span validation, conservative date resolution, overlap reconciliation, optimistic mutation IDs, correction/retention/deletion propagation, truncation splitting and backup/restore are covered by software tests. The on-demand transcript-backed weekly review is implemented separately. Connecting accepted inbox state to weekly synthesis, project histories, briefings and external task synchronization remains future work.

Acceptance includes durable explicit-null overrides/status after re-extraction, no stale results after source correction/deletion/expiry, original capture-relative dates and timezone precision, bounded complete coverage of long transcripts, recovery/retry without duplicating items, and shared web/native contracts. Real Danish/English/mixed-language model precision/recall, owner/date accuracy, duplicate rate and throughput still need evaluation on the configured local model before relying on automatic proposals. Existing physical capture acceptance remains unchanged.
