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

For frontend-only development, run the backend plus `npm --prefix src/web run dev`; Vite forwards `/v1` to port 8787. After frontend changes, rebuild and run `uv run python scripts/package_web.py` to refresh the combined server. `scripts/build.sh` also packages the web assets into the Python wheel/source distribution.

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

Optional `OMI_STT_SEGMENT_TIMESTAMPS=true` requests verbose segment timestamps from compatible providers. A conservative local audio-energy silence gate avoids transcribing quiet chunks; it is not a diarization model. Retrieval combines keywords with local date scoping, including today/yesterday/ISO dates and English/Danish weekdays. Broad day queries sample bounded evidence; semantic embeddings and speaker identification are not enabled. Answers supply source references and reject unknown cited UUIDs, but model accuracy still needs checking against source audio.

For a private TLS CA, export `SSL_CERT_FILE=/path/to/trusted-ca-bundle.pem` into the process environment, or mount/set it in Compose. Certificate verification remains enabled.

## Archive and backup

The default `data/` directory holds SQLite WAL, WAV recordings, and generated `diary/YYYY-MM-DD.md` files. Keep one backend worker. Transcription retries survive restart; daily generation uses the configured local calendar, including DST boundaries, and catches up after downtime. Late evidence and corrections invalidate derived recaps. Transcript corrections and generated diary versions are auditable; chat sessions persist locally.

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
| `POST /v1/meetings/{uuid}/recap` | Qualified meeting notes with surrounding context |
| `POST /v1/chat`, `GET /v1/chat/sessions`, `/v1/chat/sessions/{uuid}` | Persistent evidence-based chat |

Fix the same absolute `end` timestamp for rewind summary and audio requests. Missing audio is disclosed and rendered as silence; transcript boundaries remain chunk-level unless upstream segment timestamps are enabled.

Event responses include the persisted `event_id`, device, capture time, action and captured policy. Native uploads request `POST /v1/events?receipt_only=true` to keep the acknowledgement bounded as history grows. Update/restart the backend before installing this companion version: the phone keeps events pending if an older server returns only a timeline. An audio retry may return `state=expired` after retention; a matching receipt acknowledges the existing retention decision without recreating expired audio.

## Validation and remaining acceptance

- **167 Python tests**: pipeline/retries, policies, offline event snapshots, archive/versioning/settings/chat, retention/deletion races, citations, gaps, backup corruption, static routing, hosted Omi import, concurrent settings updates, compact event receipts, backup/delete/retention concurrency and rollback preservation.
- **39 native core tests**: real Opus encode/decode, packet fragments/loss/rollover/duplicate timestamps, interrupted final frames, crash reconstruction, failed-save retries, metadata reconstruction, original IDs/policies, exact receipts, storage reservations, local rewind, calendar behavior and Omi discovery matching. A temporary harness also exercised the actual AppModel with controlled suspended requests to verify pause and backend-change races; it is not part of the retained core test suite.
- **26 web tests**, including appearance persistence, system changes, tab synchronization, blocked storage and calendar behavior; production TypeScript/Vite build. Earlier Omi import flows were checked with mocked upstream responses in desktop/mobile browsers: configuration, pagination, errors/retry and transcript-only playback states.
- Unsigned iOS Simulator (arm64/x86_64) and generic iOS device builds succeed after the hardware-readiness fixes. Physical signing, installation and Omi acceptance remain outstanding.
- Desktop/mobile browser checks cover actual web/API interactions, settings persistence, short meetings, rewind/empty states, archive correction and persistent chat. Populated tests use a separate temporary archive and simulated inference, never the live archive.
- Plain and age-encrypted backup/restore round trips, no-overwrite and unsafe-path rejection were verified.
- Docker/Compose image build and a read-only-container runtime smoke check passed for the web page, status API and settings API. Desktop/phone browser upload was also verified against a separate temporary archive.

The repository-prescribed `@Codex-flow/cli` security scanner is unavailable from npm (invalid/unpublished package); it could not run. Local AI connectivity and hardware endurance remain the material integration limits.

[Reviewed plan](implementation-plan.md) · [Architecture](architecture.md) · [Open-source inspiration](open-source-inspiration.md)
