# Omi local

A local audio memory system: **Omi → iPhone → your backend and local models → rewind, diary, and chat**. The native app captures wearable audio; the responsive web app works from a computer or phone. Both share one backend. Application and API authentication are intentionally disabled.

## Run web and backend

Requires Python 3.12+, uv, and Node.js 22.12+ (or a supported newer Node release). From `~/Documents/omi`:

```sh
bash scripts/build.sh
uv run python scripts/init_config.py
# Review local STT/LLM addresses and model names in .env.
bash scripts/dev.sh
```

Open **http://localhost:8787** on the computer, or `http://<computer-LAN-address>:8787` from a phone on the same network. The server binds `0.0.0.0`; shell environment variables can override the address, for example `OMI_HOST=127.0.0.1 OMI_PORT=8787 bash scripts/dev.sh`. The launcher explicitly uses one worker, including when `WEB_CONCURRENCY` is inherited from another application. One process serves the web client and `/v1` API. No account, token, Firebase, hosted vector database, or commercial AI provider is required.

The web app has Today, Rewind, Diary, Chat, Inbox, and Settings. It supports recording uploads/conversion, searchable transcripts and source audio, corrections/history, deletion, recap generation/history/Markdown export, persistent chat, and shared settings. Both apps offer System, Light, and Dark appearance in Settings; the choice is saved on each device, and System follows device appearance changes. The live archive contains no sample recordings or demonstration answers.

### Prepare for a conversation

Choose **Prepare for a conversation** from web Today or iPhone Day. Enter a person, project or topic as a literal phrase, optional alternate names (one per line), a qualifier, and optional inclusive dates in the server timezone. Search uses Unicode case-insensitive substrings: aliases are OR alternatives and the qualifier must also occur in the same recording. Names such as “May” are never parsed as dates. Results are newest first, ten per page. A mention does not establish participation or identity.

Inspect transcripts/audio, include relevant sources across pages, then explicitly confirm the latest relevant recording. **Prepare from confirmed evidence** produces a dated, cited recap, promises to check, recorded questions and separately labeled suggested questions. Promises retain **completion unknown** and unknown owners in this first release; recorded completion/conflicts can be described with evidence, but preparation does not read or mutate inbox commitment statuses. Conditional promises must preserve their conditions, and suggestions must remain separate. Generated interpretation still needs review against transcripts/audio.

**My context and goals** is editable user-authored context, sent separately to the local model. It is never recorded evidence. Preparation, selection and context exist only in the current screen; closing discards them, with a draft safeguard. There is no saved preparation history, offline prep cache, calendar integration, automatic notification or messaging.

At most forty sources can be selected. Generation reviews up to 4,000 characters around a literal match per source, with 16,000 transcript characters total; select fewer sources if the budget is exceeded. Coverage details disclose unselected matches, the exact reviewed excerpts, omitted character counts, known pending chunks and capture gaps. Long imports can contain relevant evidence beyond the reviewed excerpt. Old sources are labeled after thirty days. Unknown phone uploads and unnamed discussions remain retrieval limits; try alternate phrases or browse Rewind.

Every generated item requires a selected source UUID and exact excerpt; unknown sources, fabricated excerpts and malformed output fail visibly. Content fingerprints include transcripts, segment timing, audio availability and provenance. The service checks its retrieval snapshot before and after inference, including new recordings/pending work/gaps in searched dates. Both clients revalidate on focus/foreground; web also responds to archive changes. Changed evidence clears the stale preparation while preserving the draft context. Unrelated changes within the searched dates may conservatively require refresh. Imported transcript-only audio and expired local audio remain distinct. These checks establish structural traceability, not factual model accuracy.

For frontend-only development, run the backend plus `npm --prefix src/web run dev`; Vite forwards `/v1` to port 8787. After frontend changes, rebuild and run `uv run python scripts/package_web.py` to refresh the combined server. `scripts/build.sh` also packages the web assets into the Python wheel/source distribution.

## Decision register

Today and Diary include a separate decision register. Create a record from an original transcript, or choose **Find decisions for this day** to ask the configured local model for proposals. Extraction processes bounded batches; use **Continue** until coverage is complete. Long imported transcripts resume in overlapping fragments. Coverage reports pending transcription and recorded capture gaps; it cannot establish that every real conversation was captured or understood.

Each choice, context, rationale and explicitly discussed alternative has its own exact transcript quotation. Leave unstated rationale empty; use separately attributed owner notes for later interpretation. Opposing passages are stored separately with the field they contradict; saving them flags a conflict until you explicitly resolve it after review. Resolution preserves the opposing evidence. Review and confirm accuracy explicitly. Confirmation preserves the recorded tentative/final state. To record an actual change of choice, create a new record and select the earlier confirmed decision when confirming it. Revision links preserve both choices across days; later timestamps alone do not establish a revision. Links require ordered capture starts, so choices within one chunk cannot currently be linked as revisions. Wording/evidence edits retain the record ID, create history, and require review again.

Transcript corrections mark dependent records stale until evidence is explicitly reattached. Source deletion or transcript retention purges dependent records and their content history; audio-only expiry preserves transcript evidence. Deleting a decision retains content-free suppression metadata so extraction retries do not restore it. Exports and backups include a deterministic decision section with review state, provenance and revision IDs, including days without a generated recap. Historical backups retain the data they contained when created.

The iPhone register uses online requests and memory only; decision content is not added to its persistent cache or capture outbox. Optional project/person/commitment/meeting labels do not require those entities to exist and do not identify a speaker or assign a task. Automatic background extraction, semantic duplicate merging, resolved entity relationships, backdated decision-time fields and decision-aware chat are deferred. Original transcripts remain the evidence; local-model classification and rationale quality still require evaluation on real recordings.

## iPhone companion

Open [OmiLocal.xcodeproj](../src/ios/OmiLocal.xcodeproj) in Xcode, choose a signing team and your iPhone, and run. The deployment target is iOS 17+. In app Settings, enter the reachable backend address including **port 8787**. `localhost` on an iPhone refers to the phone itself. There is no mobile API token.

For the first physical install, connect and trust the iPhone in Xcode, enable Developer Mode on the phone if prompted, and allow Bluetooth and local-network access when the app requests them. The checked-in Xcode project is ready to open; XcodeGen is needed only to regenerate it after changing `project.yml`. Allow package resolution to finish before building. Keep the backend machine awake and allow inbound TCP port 8787 on the private LAN. First open the backend URL in Safari on the iPhone to check reachability, then use **Save & test connection** in app Settings.

The native Capture tab discovers and connects to the pendant, displays device/firmware/codec/battery, pauses/resumes capture, shows the outbox/gaps, and provides manual important/meeting controls. Day, Rewind, Chat, and Settings use the same backend as the web app. Recent audio can be played from the phone buffer while the server is offline.

In iPhone **Settings → Uploads**, enable **Upload only on Wi-Fi** to prevent recordings, flags and capture gaps from uploading over cellular. The preference saves immediately on that phone and also applies to restored/background transfers and **Sync now**. Changing it requeues pending transfers with the same IDs; recordings stay in the outbox until acknowledged. Capture continues locally while uploads wait for Wi-Fi. The default is off, preserving cellular upload support. Browsing, chat and settings requests use the current connection independently of this capture-upload preference.

Capture uses CoreBluetooth restoration, real libopus decoding for codecs 20/21, and PCM16 codec 0. Unsupported codecs are rejected explicitly; codec 1 is currently ambiguous in upstream specifications. Original compressed frames, capture times, audio chunks, semantic events and gaps are persisted locally before upload. Background URLSession retries use stable IDs and validate acknowledgements before marking items delivered. The phone budget is configurable (1/2/4/8 GB, default 2 GB); unsent data is never silently evicted. Synced audio ages out of the recent local playback buffer, independently of server retention.

Native build/core checks:

```sh
cd src/ios
swift test
# Only after changing project.yml (requires XcodeGen):
# xcodegen generate
xcodebuild -project OmiLocal.xcodeproj -scheme OmiLocal \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/omi-ios-build CODE_SIGNING_ALLOWED=NO build
```

The project file is included; regenerate it when changing `project.yml`. Physical signing/install, actual pendant pairing, button timing, and screen-locked/background endurance still need the purchased hardware. See [device evidence and acceptance checks](device-research.md).

## Weekly review (web)

Open **Diary → Weeks** to generate an on-demand review of the last completed Monday–Sunday week, compared with the previous calendar week. Week boundaries use the saved backend IANA timezone, including DST; **This week so far** shows a partial cutoff. Daily recaps are not required. Every item includes an exact quote and an inspectable original transcript, with correction, history, deletion and audio when available. The four groups cover supported decisions, commitments needing attention, recurring discussions and supported changes.

Commitments are qualified as **explicitly still open** or **follow-through unconfirmed**. Silence or disappearance never establishes completion. This two-week view is not an enduring backlog of older commitments. Cross-week changes require distinct sources in both weeks; recurrence requires two local dates and selected-week evidence. Whole overlapping chunks are preserved; boundary chunks supply context but cannot alone establish a dated decision, change or recurrence. Coverage distinguishes pending/empty/expired transcripts, recorded intervals, known gaps and unavailable audio. Unuploaded phone backlog remains unknown.

Generation runs in a durable local queue independently of transcription, survives leaving the page and recovers interrupted work after backend restart (at most three interrupted attempts). Reads never invoke the model; unchanged requests reuse a version, while **Regenerate** forces a new one. The UI polls only the active job. Model/schema failures and processing-budget failures remain visible; they never publish a partial review. The initial bounds are 2 million transcript characters, 256 model calls and eight reduction rounds, with bounded per-call evidence. All eligible evidence enters extraction; no chat sampling is used.

Late evidence in either week, corrections, source states, hints, timezone and model configuration make saved reviews stale. Publication rechecks the full scoped fingerprint inside a write transaction. Historical versions retain the original evidence and timezone; source inspection shows the current transcript. Deletion or transcript expiry conservatively purges all weekly versions/jobs and restored Markdown projections. Downloaded exports and earlier backups are independent copies. Backups preserve version history and queued jobs. Set `OMI_LLM_REVISION` when replacing a model behind the same route name.

Native weekly UI, scheduled generation, notifications and weekly consumption of the existing structured inbox lifecycle are follow-ons. Weekly synthesis currently uses original transcripts directly; it does not read or mutate inbox status or decision records. Current range chat remains useful for follow-up, but its retrieval is not exhaustive. Quote validation checks traceability and format; semantic claim support and real local-model quality still require evaluation.

## Editable inbox

Open **Inbox** on the web or **Day → Inbox** on iPhone to review promises, standalone deadlines and unanswered questions across all days. Items keep pending/completed/dismissed status, editable title/kind/owner/deadline, explicit unknown values, transcript excerpts, review history and duplicate links. User edits and status survive re-extraction. Questions mean no answer was found in the scanned context; they are never automatically marked answered. Model proposals need review and do not establish that every obligation was recorded or found.

New transcripts, including Omi imports and corrections, queue independent extraction after a short settling period. Older transcripts already in an upgraded archive are scanned only when you select dates (default seven days; up to 31 per request). Scan/retry controls and coverage distinguish unscanned, waiting, failed and completed windows. The backend worker must be enabled. Extraction uses only the configured local LLM, retries durably, and reduces truncated output into smaller windows. Real model precision, recall and throughput have not been verified.

Every proposal needs validated exact transcript spans. Ownership stays unknown when unsupported; I/we and speaker labels do not identify the wearer. Automatic deadline resolution intentionally handles only explicit ISO dates and unambiguous today/tomorrow (including Danish) against capture time in the archive timezone. Other phrases remain unresolved for manual review. Date-only deadlines retain their precision; changing archive timezone does not rewrite accepted reviewed deadlines. There are no notifications or external task-manager synchronization.

Corrections flag affected evidence for review and re-extract with new revision IDs. Historical excerpts are labeled as prior transcript evidence. Audio expiry keeps transcript-backed items but disables playback. Source deletion or transcript expiry conservatively removes affected items, manual edits, history and jobs, including items whose extraction used that source as context. Content-free source tombstones prevent automatic inbox resurrection on replay. Existing exported backups remain historical snapshots. Inbox review requires backend connectivity; failed saves preserve the open draft, while leaving the editor discards unsaved changes. Inbox content is not added to the phone capture outbox or persistent cache.

## Optional Omi API import

Settings → Omi connection can import existing cloud conversations using Omi's [personal Developer API](https://docs.omi.me/api-reference/conversations/get-conversations). Create an `omi_dev_…` key with `conversations:read` in Omi's Developer settings, set `OMI_OMI_API_KEY` in the backend's `.env`, and restart the backend. The key stays on the backend, outside public settings and backups. Leave it empty to use direct BLE capture without an Omi account.

Import the latest 25 conversations, then use **Load next 25** to continue. Each page requests `include_transcript=true`; pagination keeps the same end timestamp and honors Omi's continuation headers, including filtered empty pages. Imports preserve conversation times, upstream IDs and speaker segments. Finished conversations with transcripts become searchable sources for rewind, diary and chat without running STT. Unfinished or empty conversations are counted as skipped. Reimporting an existing conversation preserves local corrections and retention decisions; this is a snapshot import, not continuous cloud synchronization. Existing local BLE capture continues independently.

This API supplies **transcripts only**. Imported sources have no original audio and do not offer playback. Source labels distinguish these from local recordings whose audio expired. Hosted import needs internet access and an Omi key; direct BLE capture and local inference remain independent of Omi's hosted service. The published multi-user Integration App API uses different credentials and is not this connector.

Foreground iPhone discovery accepts Omi name advertisements as well as the audio service, then validates required GATT capabilities. Reconnection uses the saved peripheral identifier. Unsupported/custom firmware still requires its own verified protocol. Pendant storage downloads and direct Wi-Fi capture are not implemented; official CV1 documentation says Wi-Fi is disabled in current firmware. Source comparisons with the official project, omibutfree, Boswell Phone, omi-whisperx and Omi Offline are in [device research](device-research.md).

## Gesture and hint rules

- Single click flags nearby speech. The default cooldown is **60 seconds from the last accepted flag**; extra clicks do not extend the wait.
- Meetings prefer a verified hold gesture, with **double click as the stock-firmware fallback** and phone controls always available. Stock production source uses a three-second hold for power-off, so hold mode must be selected only on verified compatible firmware. One resolved meeting gesture produces no component flags.
- Meeting hints stopped before **60 seconds** are ignored. An ongoing candidate is provisional for its first minute. Auto-stop defaults to **120 minutes** and labels the meeting without stopping ordinary daily capture.
- Flags and meetings are **approximate contextual hints**. By default, the model sees a minute of surrounding speech before/after relevant gestures. Original button timestamps are retained for provenance, not presented as exact conversation boundaries.
- Captured timeout, minimum-duration and cooldown values travel with new offline phone events, so later server setting changes do not reinterpret the queued gestures. Older clients remain compatible with server defaults.

Capture and archive settings are editable in the web app and persisted in SQLite. Appearance is stored locally in the browser or iPhone. The iPhone synchronizes shared pause/timeout/policy settings; an offline local pause is preserved until it can be synchronized. Backlog uploads continue during capture pause. Environment variables provide initial defaults; saved runtime settings take precedence.

If meeting controls are mixed between the web app and an offline phone, the phone's meeting indicator can temporarily differ from the server while local toggles wait to sync. The server replays received events by their original timestamps; refresh after the queue drains to confirm the reconciled meeting state. Exact offline reconciliation across both control surfaces is still outstanding.

## Local inference

The example uses the inspected HomeCompute repository configuration:

| Setting | Example |
|---|---|
| HTTP gateway base | `http://ai.home.arpa:18080/v1` |
| Danish transcription route | `stt-danish` |
| Multilingual alternative | `whisper-turbo` |
| LLM route | `home` |
| Language | `auto` omits a forced STT language |

Configure `OMI_STT_BASE_URL`, `OMI_LLM_BASE_URL`, `OMI_STT_MODEL`, and `OMI_LLM_MODEL` in `.env`. `OMI_INFERENCE_TOKEN` is optional authentication for your own inference gateway and is never an app login credential. Endpoints accept private IPs and local DNS/service names. There is no cloud fallback or redirect following.

Adapters use HTTP `/audio/transcriptions` and `/chat/completions`; the existing home-core Wyoming service on port 10300 needs an HTTP bridge before it can be used here. The configured gateway and previous `192.168.30.122:18080` address were unreachable from this Mac during validation, so real STT/LLM quality and latency have not been verified. When models are unreachable, audio remains queued and summary/chat errors remain visible.

Optional `OMI_STT_SEGMENT_TIMESTAMPS=true` requests verbose segment timestamps from compatible providers. A conservative local audio-energy silence gate avoids transcribing quiet chunks; it is not a diarization model. Retrieval combines keywords with local date scoping, including today/yesterday/ISO dates and English/Danish weekdays. Broad day queries sample bounded evidence; semantic retrieval is opt-in and speaker identification is not implemented. Answers supply source references and reject unknown cited UUIDs, but model accuracy still needs checking against source audio.

For a private TLS CA, export `SSL_CERT_FILE=/path/to/trusted-ca-bundle.pem` into the process environment, or mount/set it in Compose. Certificate verification remains enabled.

## Optional local semantic retrieval

Semantic retrieval is implemented behind **`OMI_EMBEDDING_ENABLED=false` by default**. It uses the same SQLite archive; no vector server or model download is required. No reachable embedding model, bilingual quality, target-machine latency or hardware quality has been verified.

Choose and serve a local model yourself, then configure the separate `OMI_EMBEDDING_*` fields in [the environment example](../config/omi.env.example). Enabling requires a model name, immutable revision identifier and output dimension. The adapter expects an OpenAI-compatible `POST /embeddings` response with indexed float vectors. Configure query/document prefixes according to that model's documented contract. Tokens use a separate `OMI_EMBEDDING_TOKEN`, never the STT/LLM credential. Endpoints use the existing local-address validation, TLS verification, no redirects and no cloud fallback. Requests have a total deadline, input/batch ceilings and a response-byte ceiling; malformed, zero, nonfinite or wrong-dimension vectors mark the adapter unavailable.

`OMI_EMBEDDING_PASSAGE_CHARS` defaults to 1000 and `OMI_EMBEDDING_MAX_INPUT_CHARS` to 4000. These are **character bounds, not token estimates**. Confirm the model accepts all configured text plus prefixes without provider-side truncation; this implementation cannot detect a provider that silently truncates or changes weights behind an unchanged alias. Queries exceeding the character ceiling fall back to keywords. Text is never silently shortened for embedding. Change `OMI_EMBEDDING_REVISION` whenever weights change. Endpoint, dimension, prefixes and splitter changes also produce separate embedding sets.

On startup, a separate bounded worker backfills current nonempty transcripts, including existing Omi imports. It processes at most 16 passages per call and resumes after restart; STT does not wait for embeddings. New recordings and corrections are searchable with keywords immediately. SQLite triggers invalidate obsolete versions on transcription, correction, deletion and transcript expiry, including imports that write directly to SQLite. Final writes and retrieval recheck source versions. Audio-only expiry keeps transcript vectors. Backups include index tables and manifests; restored jobs resume when the same set is configured. Old inactive sets are retained as rebuildable data; edits/deletion remove that source across all sets. Model changes immediately select the new set and temporarily use partial semantic coverage plus keywords during backfill; vectors from different sets are never compared.

`POST /v1/search` accepts the same question, limit, day/range/meeting/session scope as chat and returns sources plus retrieval diagnostics **without generating an answer or creating a conversation**. Saved explicit scopes win over conflicting question dates. Both candidate branches apply the effective scope before their top 50 results. Keyword BM25 and cosine candidates are deduplicated by recording UUID, combined with reciprocal rank fusion (`k=60`), and protect up to two strongest lexical matches for literal names. An exact streamed vector scan avoids a new dependency or arbitrary recent-history cutoff. Scan cost still grows with archive size; target-machine performance at 10k/100k passages is not yet measured.

Topical chat uses the same search. Broad questions retain the sampling/diary path and reduce **full selected transcripts**, including long transcript tails, through the existing bounded-batch map/reduce path; generic follow-ups reload current source recordings. Topical evidence is bounded to the requested recording limit and 12,000 excerpt characters, with up to 2,000 characters of derived diary navigation notes. Broad-query metadata instead declares full selected transcripts and no total excerpt-character ceiling; source sampling remains explicit. Passage overlap and up to 150 characters around a hit preserve context inside a recording. Topical matches may add at most one immediately contiguous prior/next completed local recording on the same device, excluding imports, capture gaps and records outside the effective scope. Neighbors keep their own source UUID/version and fill spare source slots only, without displacing ranked hits or exceeding the shared excerpt budget; context is therefore limit-dependent. FTS highlight positions keep lexical excerpts aligned with actual Unicode token/diacritic matches. Diary notes whose dependencies were omitted are excluded. Full `Source.text` remains authoritative for corrections and verification; additive `matches` contain excerpts and offsets. Clients display retrieval mode, indexing coverage and bounded-evidence notices. Source timing still covers whole overlapping recording chunks, including long imports; character offsets do not claim precise speech timestamps.

An unavailable service falls back to keywords and is disclosed separately from no matches. Partial indexing is disclosed per scope. Corrected historical answers are marked stale and excluded from model history; source identifiers can still resolve to the current transcript for a follow-up. Deletion/expiry retains the existing derived-chat cleanup. Cached iPhone conversations disclose that availability/corrections may have changed. UUID citation validation establishes allowed source identity, not factual support or complete citation coverage.

Evaluate retrieval independently of answer generation:

```sh
# Safe default: fictional temporary archive, keyword baseline, no inference calls.
uv run python scripts/evaluate_retrieval.py
# Explicit local model test: export reviewed OMI_EMBEDDING_* variables first.
# This ignores .env, never opens the live archive, and contacts your embedding service only.
uv run python scripts/evaluate_retrieval.py --live-embeddings
```

The fixture in `tests/fixtures/retrieval_eval.json` contains 24 fictional source scenarios and 36 queries: literal/Danish names, Danish and English paraphrases, cross-language/code-switching, suggestion-versus-commitment and changed-plan context, scoped distractors and six unanswerable questions. Dev/heldout labels keep related translations together. The runner reports keyword/semantic/hybrid Recall@5/@12, MRR@10, nDCG@10, required-context coverage, negative-query rejection, scope violations, p95 latency, indexing time and database size. It scores the sources actually selected for bounded evidence; it never calls an answer model. Semantic/hybrid quality evaluation fails without producing a report if any query falls back, uses the wrong mode or has partial/unavailable indexing. Negative rejection measures empty retrieval, not answer abstention.

The initial **keyword-only fictional baseline** retrieved 71.7% of labeled sources at five results, rejected four of six negatives, and had zero scope violations. This is not a passing semantic-quality result. Before enabling a real model for daily use: require no scope/stale/deletion violations, no aggregate literal/name Recall@5 loss, a proposed 15-percentage-point heldout paraphrase/cross-language Recall@5 improvement, at least five of six negatives rejected, and an acceptable measured storage/latency envelope on the target machine. The default cosine floor (`0.5`) is uncalibrated; tune only on dev examples and report heldout results by language/category. Test answer factual support, citations, abstention and transcript-instruction resistance separately using fixed retrieved evidence. Controlled-vector tests establish mechanics only.

## Archive and backup

The default `data/` directory holds SQLite WAL, WAV recordings, and generated `diary/YYYY-MM-DD.md` files. Weekly review versions and durable jobs live in SQLite; backups also render immutable `weekly/<version-uuid>.md` projections. Keep one backend worker. Transcription retries survive restart; daily generation uses the configured local calendar, including DST boundaries, and catches up after downtime. Late evidence and corrections invalidate derived recaps. Transcript corrections and generated diary versions are auditable; chat sessions persist locally.

Audio and transcript retention are separate, configurable in Settings; **0 keeps data indefinitely**. Deletion/expiry updates search and derived outputs. No backup includes `.env` or inference credentials.

```sh
uv run python scripts/archive.py backup /path/to/omi-backup.zip
uv run python scripts/archive.py restore /path/to/omi-backup.zip /path/to/new-data-dir
```

Restore requires a new, non-existing destination. It checks ZIP paths, the database, recording IDs and checksums, then rebases audio paths. Point `OMI_DATA_DIR` at the restored directory before starting the server. Stop the service during restore; close active archive writers when taking a final migration snapshot.

Optional encrypted archives use the installed `age` CLI and an existing public recipient/identity:

```sh
uv run python scripts/archive.py backup /path/to/omi-backup.zip.age --recipient AGE_PUBLIC_RECIPIENT
uv run python scripts/archive.py restore /path/to/omi-backup.zip.age /path/to/new-data-dir --identity /path/to/age-identity
```

## Container deployment

From the repository root, create `.env` with `cp config/omi.env.example .env` if it does not exist, then review the model addresses. Docker installation does not require running the host Python/Node build first.

```sh
docker compose --env-file .env -f config/compose.yaml up --build -d
docker compose --env-file .env -f config/compose.yaml logs --tail=50 backend
```

The container builds the web client, serves it with the API using one worker, and persists a named volume. Port 8787 binds the LAN by default; set `OMI_BIND_ADDRESS=127.0.0.1` in `.env` when a local reverse proxy owns access. The explicit `--env-file .env` also applies that setting to Compose's port interpolation. Docker loopback refers to the container, so inference must use a reachable LAN/service address. No remote deployment to home-core was performed during implementation.

## Before the pendant arrives

1. Build and run the backend, then check `curl --fail http://localhost:8787/v1/status`. A running worker confirms service health; inference states remain `unknown` until a real request uses each model.
2. Upload a short recording through the web app using its actual recording-start time. Confirm it leaves the transcription queue and the transcript matches speech. Generate a recap and ask a question with a source reference. These checks require reachable STT and LLM services; a successful status response alone does not validate them.
3. Install the iPhone app, save the backend address, and verify Day/Settings can reach it. Choose **Double click · stock firmware** until a different gesture is verified on the actual pendant. Set the phone storage budget and Wi-Fi upload preference before recording.
4. Run the [physical-device acceptance plan](device-research.md#physical-device-acceptance-plan) when the pendant arrives. Record the model, firmware, codec, capture duration, gaps, and final outbox count. Complete the offline/reconnect and screen-lock checks before relying on all-day capture.

Keep one archive owner and one backend process for a data directory. The app/API have no authentication, so use the intended private LAN or VPN access path.

## API

`GET /v1/openapi` returns the schema. Important routes:

| Route | Purpose |
|---|---|
| `PUT /v1/audio/{uuid}?device_id=...&started_at=...` | Idempotent raw PCM16 mono16k WAV upload, up to 120 seconds/4 MB |
| `GET/DELETE /v1/audio/{uuid}` | Source playback or archive deletion |
| `POST /v1/events`, `POST /v1/gaps` | Durable gestures and capture gaps |
| `GET /v1/status`, `GET /v1/timeline` | Queue/worker/provider state and contextual hints |
| `GET/PATCH /v1/settings` | Persist shared runtime settings |
| `GET /v1/integrations/omi` | Optional hosted Omi connector configuration status, without exposing credentials |
| `POST /v1/integrations/omi/import` | Import a page using optional aware `start_date`/`end_date`, `limit` (1–100), and `offset`; returns counts and next offset |
| `GET /v1/transcripts`, `PATCH /v1/transcripts/{uuid}` | Browse/search and correct transcripts |
| `GET /v1/transcripts/{uuid}/revisions` | Auditable corrections |
| `POST /v1/rewind`, `GET /v1/rewind/audio` | Transcript/summary and exact audio window |
| `GET/POST /v1/recaps/{day}` | Cached recap or generation |
| `GET /v1/diary`, `/v1/diary/{day}.md`, `/v1/diary/{day}/versions` | Archive, export, history |
| `GET /v1/weekly-reviews`, `GET /v1/weekly-reviews/{monday}` | Last completed week or selected cached review with live coverage/staleness; no inference |
| `POST /v1/weekly-reviews/{monday}/generate` | Durable generation; JSON `request_id` UUID and optional `force` |
| `GET /v1/weekly-reviews/jobs/{uuid}` | Durable generation state, model-call progress and sanitized errors |
| `GET /v1/weekly-reviews/{monday}/versions`, `/{monday}.md` | Review history and original-evidence Markdown; optional `version_id` on review/export |
| `POST /v1/meetings/{uuid}/recap` | Qualified meeting notes with surrounding context |
| `POST /v1/search` | Scoped retrieval and diagnostics without answer generation or chat writes |
| `POST /v1/conversation-prep/search`, `/generate`, `/validate` | Ephemeral literal source discovery, confirmed evidence preparation and freshness checks; no-store responses |
| `POST /v1/chat`, `GET /v1/chat/sessions`, `/v1/chat/sessions/{uuid}` | Persistent evidence-based chat |

Fix the same absolute `end` timestamp for rewind summary and audio requests. Missing audio is disclosed and rendered as silence; transcript boundaries remain chunk-level unless upstream segment timestamps are enabled.

Event responses include the persisted `event_id`, device, capture time, action and captured policy. Native uploads request `POST /v1/events?receipt_only=true` to keep the acknowledgement bounded as history grows. Update/restart the backend before installing this companion version: the phone keeps events pending if an older server returns only a timeline. An audio retry may return `state=expired` after retention; a matching receipt acknowledges the existing retention decision without recreating expired audio.

## Validation and remaining acceptance

- **315 Python tests**: weekly DST/evidence/queue/versioning/lifecycle/restore contracts plus pipeline/retries, policies, offline event snapshots, archive/versioning/settings/chat, retention/deletion races, citations, gaps, backup corruption, static routing, hosted Omi import, concurrent settings updates, compact event receipts, backup/delete/retention concurrency and rollback preservation; controlled hybrid retrieval, scoped top-k, long import tails, embedding validation/outages, indexing versions/restarts and correction/deletion races; editable inbox extraction, reconciliation and evidence lifecycle. No real embedding model was used.
- **41 native core tests**: real Opus encode/decode, packet fragments/loss/rollover/duplicate timestamps, interrupted final frames, crash reconstruction, failed-save retries, metadata reconstruction, original IDs/policies, exact receipts, storage reservations, local rewind, calendar behavior, Omi discovery matching and inbox model decoding. A temporary harness also exercised the actual AppModel with controlled suspended requests to verify pause and backend-change races; it is not part of the retained core test suite.
- **36 web tests**, including appearance persistence, system changes, tab synchronization, blocked storage, calendar behavior, inbox edits and decision evidence models; production TypeScript/Vite build. Earlier Omi import flows were checked with mocked upstream responses in desktop/mobile browsers: configuration, pagination, errors/retry and transcript-only playback states.
- Unsigned iOS Simulator (arm64/x86_64) and generic iOS device builds succeed after the hardware-readiness fixes. Physical signing, installation and Omi acceptance remain outstanding.
- Weekly desktop/mobile checks (1440×1080 and 390×844) cover generation, cited correction, stale refresh, versions, original-evidence export, scoped chat, partial/empty weeks, model failure and deletion with a disposable archive and simulated inference. Idle reads do not poll full transcripts. No browser console/runtime errors or mobile horizontal overflow were observed.
- Desktop/mobile browser checks cover actual web/API interactions, settings persistence, short meetings, rewind/empty states, archive correction and persistent chat. Populated tests use a separate temporary archive and simulated inference, never the live archive.
- Plain and age-encrypted backup/restore round trips, no-overwrite and unsafe-path rejection were verified.
- Docker/Compose image build and a read-only-container runtime smoke check passed for the web page, status API and settings API. Desktop/phone browser upload was also verified against a separate temporary archive.

The repository-prescribed `@Codex-flow/cli` security scanner is unavailable from npm (invalid/unpublished package); it could not run. Local AI connectivity and hardware endurance remain the material integration limits.

[Reviewed plan](implementation-plan.md) · [Architecture](architecture.md) · [Open-source inspiration](open-source-inspiration.md)
