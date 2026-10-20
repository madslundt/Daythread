# Daythread

The web and iPhone interfaces support English, Danish, German, Spanish, French, Norwegian Bokmål, and Swedish through separate on-device language preferences. Translation contributors can follow the [localization guide](localization.md) to add a language and verify both catalogs.

A local audio memory system: **Omi → iPhone → your backend and local models → rewind, diary, and chat**. The native app captures wearable audio; the responsive web app works from a computer or phone. Both share one backend. Application and API authentication are intentionally disabled.

## Android companion

The [Android guide](../src/android/README.md) covers installation, native microphone/Omi capture, offline uploads, permissions, and device acceptance. Build with `bash scripts/build_android.sh` using JDK 17+ and Android SDK 36; install the resulting `dist/android/Daythread-debug.apk`. Android's Archive screen uses the existing responsive interface and the same backend. Native capture profiles are selected separately in Connection.

## Run web and backend

Requires Python 3.15.0+, uv 0.13.1+, Node.js 26.12.0+ and npm 12.2.0+. The repository pins Python/Node in `.python-version` and `.node-version`; upgrade older uv installations before running the scripts. Install npm with `npm install --global npm@12.2.0`. From `~/Documents/omi`:

```sh
bash scripts/build.sh
uv run python scripts/init_config.py
# Set STT/LLM endpoints, API keys and model names in Settings after starting.
bash scripts/dev.sh
```

Open **http://localhost:8787** on the computer, or `http://<computer-LAN-address>:8787` from a phone on the same network. The server binds `0.0.0.0`; shell environment variables can override the address, for example `OMI_HOST=127.0.0.1 OMI_PORT=8787 bash scripts/dev.sh`. The launcher explicitly uses one worker, including when `WEB_CONCURRENCY` is inherited from another application. One process serves the web client and `/v1` API. No account, token, Firebase, hosted vector database, or commercial AI provider is required.

The web app has Today, Rewind, Diary, Chat, Inbox, and Settings. It supports recording uploads/conversion, searchable transcripts and source audio, corrections/history, deletion, recap generation/history/Markdown export, persistent chat, and shared settings. Both apps offer System, Light, and Dark appearance in Settings; the choice is saved on each device, and System follows device appearance changes. The live archive contains no sample recordings or demonstration answers.

Timeline recordings are grouped into compact sessions on web and iPhone: recordings from the same capture source stay together when the gap is at most five minutes. Longer gaps and different capture sources start separate groups. Repeated identical uploads remain nested under their original recording, and silent/empty-transcript runs do not bridge distant speech sessions. These are browsing groups based on supplied timestamps, not inferred exact conversation boundaries; forwarded audio keeps its estimated-time disclosure.

Open a session to browse its original recordings, with audio, transcripts, corrections and duplicates preserved. Sessions are grouped before pagination; their recording bodies load in pages only when opened. Search filters matching recordings before grouping. Topic sections within a recording favor broad, sustained subjects rather than individual details or brief tangents. Adjacent matching topic titles merge, including across processing windows, without changing transcript words or their order. Existing topic analyses are refreshed automatically under the new format.


### Prepare for a conversation

Choose **Prepare for a conversation** from web Today or iPhone Day. Enter a person, project or topic as a literal phrase, optional alternate names (one per line), a qualifier, and optional inclusive dates in the server timezone. Search uses Unicode case-insensitive substrings: aliases are OR alternatives and the qualifier must also occur in the same recording. Names such as “May” are never parsed as dates. Results are newest first, ten per page. A mention does not establish participation or identity.

Inspect transcripts/audio, include relevant sources across pages, then explicitly confirm the latest relevant recording. **Prepare from confirmed evidence** produces a dated, cited recap, promises to check, recorded questions and separately labeled suggested questions. Promises retain **completion unknown** and unknown owners in this first release; recorded completion/conflicts can be described with evidence, but preparation does not read or mutate inbox commitment statuses. Conditional promises must preserve their conditions, and suggestions must remain separate. Generated interpretation still needs review against transcripts/audio.

**My context and goals** is editable user-authored context, sent separately to the local model. It is never recorded evidence. Preparation, selection and context exist only in the current screen; closing discards them, with a draft safeguard. There is no saved preparation history, offline prep cache, calendar integration, automatic notification or messaging.

At most forty sources can be selected. Generation reviews up to 4,000 characters around a literal match per source, with 16,000 transcript characters total; select fewer sources if the budget is exceeded. Coverage details disclose unselected matches, the exact reviewed excerpts, omitted character counts, known pending chunks and capture gaps. Long imports can contain relevant evidence beyond the reviewed excerpt. Old sources are labeled after thirty days. Unknown phone uploads and unnamed discussions remain retrieval limits; try alternate phrases or browse Rewind.

Every generated item requires a selected source UUID and exact excerpt; unknown sources, fabricated excerpts and malformed output fail visibly. Content fingerprints include transcripts, segment timing, audio availability and provenance. The service checks its retrieval snapshot before and after inference, including new recordings/pending work/gaps in searched dates. Both clients revalidate on focus/foreground; web also responds to archive changes. Changed evidence clears the stale preparation while preserving the draft context. Unrelated changes within the searched dates may conservatively require refresh. Imported transcript-only audio and expired local audio remain distinct. These checks establish structural traceability, not factual model accuracy.

For frontend-only development, run the backend plus `npm --prefix src/web run dev`; Vite forwards `/v1` to port 8787. After frontend changes, rebuild and run `uv run python scripts/package_web.py` to refresh the combined server. `scripts/build.sh` also packages the web assets into the Python wheel/source distribution.

## Decision register

Today and Diary include a separate decision register. The configured local model finds decision proposals automatically when transcripts arrive, including late additions to earlier days. You can also create a record from an original transcript. Extraction processes and resumes bounded batches automatically until coverage is complete. Long imported transcripts resume in overlapping fragments. Pending transcription and recorded capture gaps can leave decisions undiscovered; extraction cannot establish that every real conversation was captured or understood.

Each choice, context, rationale and explicitly discussed alternative has its own exact transcript quotation. Leave unstated rationale empty; use separately attributed owner notes for later interpretation. Opposing passages are stored separately with the field they contradict; saving them flags a conflict until you explicitly resolve it after review. Resolution preserves the opposing evidence. Review and confirm accuracy explicitly. Confirmation preserves the recorded tentative/final state. To record an actual change of choice, create a new record and select the earlier confirmed decision when confirming it. Revision links preserve both choices across days; later timestamps alone do not establish a revision. Links require ordered capture starts, so choices within one chunk cannot currently be linked as revisions. Wording/evidence edits retain the record ID, create history, and require review again.

Transcript corrections mark dependent records stale until evidence is explicitly reattached. Source deletion or transcript retention purges dependent records and their content history; audio-only expiry preserves transcript evidence. Deleting a decision retains content-free suppression metadata so extraction retries do not restore it. Exports and backups include a deterministic decision section with review state, provenance and revision IDs, including days without a generated recap. Historical backups retain the data they contained when created.

The iPhone register uses online requests and memory only; decision content is not added to its persistent cache or capture outbox. Optional project/person/commitment/meeting labels do not require those entities to exist and do not identify a speaker or assign a task. Automatic background extraction, semantic duplicate merging, resolved entity relationships, backdated decision-time fields and decision-aware chat are deferred. Original transcripts remain the evidence; local-model classification and rationale quality still require evaluation on real recordings.

## Official app audio forwarding

The optional receiver preserves native capture and cloud transcript import. Save **Audio forwarding token** in either client's server settings under **Omi connections**, then enable forwarding in **Settings → Omi connection**. The environment variable `OMI_AUDIO_FORWARDING_TOKEN` remains available as an initial default. Configure the official app’s **Settings → Developer Mode → Realtime audio bytes** URL and interval. Accepted raw PCM becomes playable 16 kHz WAV and enters the durable local worker. Receiver status, last receipt and sanitized failures are shown in Settings; source details disclose receipt-estimated times. Last receipt is not proof of complete capture.

Current upstream sends webhooks from **Omi’s backend**, whose SSRF checks reject private destinations. Phone LAN/VPN access alone is insufficient; do not expose this unauthenticated archive publicly. See the [complete walkthrough](official-audio-forwarding.md) for the network prerequisite, token/proxy protection, contract evidence, retry/duplicate/timing policy, away-from-home limitations and pending consumer-hardware acceptance. No public network setup is performed by the feature.

## iPhone companion

Open [Daythread.xcodeproj](../src/ios/Daythread.xcodeproj) in Xcode, choose a signing team and your iPhone, and run. The deployment target is iOS 17+. The app is named **Daythread** and new installs default to **https://daythread.home.arpa**. Existing saved connections are preserved. In app Settings, use that address or a reachable LAN/VPN address including **port 8787** when connecting directly. `localhost` on an iPhone refers to the phone itself. There is no mobile API token.

For the first physical install, connect and trust the iPhone in Xcode, enable Developer Mode on the phone if prompted, and allow Bluetooth and local-network access when the app requests them. The checked-in Xcode project is ready to open; XcodeGen is needed only to regenerate it after changing `project.yml`. Allow package resolution to finish before building. Keep the backend machine awake and allow inbound TCP port 8787 on the private LAN. First open the backend URL in Safari on the iPhone to check reachability, then use **Save & test connection** in app Settings.

The native Capture tab discovers and connects to the pendant, displays device/firmware/codec/battery, provides **Start recording** and **Stop recording** on this iPhone, shows the outbox/gaps, and provides manual important/meeting controls. Day, Rewind, Chat, and Settings use the same backend as the web app. Recent audio can be played from the phone buffer while the server is offline.

In iPhone **Settings → Uploads**, enable **Upload only on Wi-Fi** to prevent recordings, flags and capture gaps from uploading over cellular. The preference saves immediately on that phone and also applies to restored/background transfers and **Sync now**. Changing it requeues pending transfers with the same IDs; recordings stay in the outbox until acknowledged. Capture continues locally while uploads wait for Wi-Fi. The default is off, preserving cellular upload support. Browsing, chat and settings requests use the current connection independently of this capture-upload preference.

Capture uses CoreBluetooth restoration, real libopus decoding for codecs 20/21, and PCM16 codec 0. Unsupported codecs are rejected explicitly; codec 1 is currently ambiguous in upstream specifications. Original compressed frames, capture times, audio chunks, semantic events and gaps are persisted locally before upload. Background URLSession retries use stable IDs and validate acknowledgements before marking items delivered. The phone budget is configurable (1/2/4/8 GB, default 2 GB); unsent data is never silently evicted. Synced audio ages out of the recent local playback buffer, independently of server retention.

Native build/core checks:

```sh
cd src/ios
swift test
# Only after changing project.yml (requires XcodeGen):
# xcodegen generate
xcodebuild -project Daythread.xcodeproj -scheme Daythread \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/omi-ios-build CODE_SIGNING_ALLOWED=NO build
```

The project file is included; regenerate it when changing `project.yml`. Physical signing/install, actual pendant pairing, button timing, and screen-locked/background endurance still need the purchased hardware. See [device evidence and acceptance checks](device-research.md).

## Conversation tags

See [manual conversation tags](conversation-tags.md) for adding/removing tags on web and iPhone, combined tag/location filtering, source provenance, and API behavior.

## GPS and nearby reminders

See [conversation locations and nearby reminders](location-support.md) for the opt-in iPhone capture setting, place confirmation, permissions, API fields, retention, and physical-device checks.

## Completion notifications on iPhone

Rewinds, daily and meeting recaps, and transcript translations requested from the iPhone use durable home-core jobs. Once accepted, work continues when the app is closed, with saved results available from the completion notification. Weekly reviews and automatically generated daily recaps also publish completion notices. While Daythread is active, a foreground feed shows internal banners and the Notifications list; the notification delegate suppresses system banners and sounds even when a push races with reopening the app.

To enable background delivery, configure **Push notifications** in server configuration with the Apple developer team ID (`apns_team_id`), APNs signing key ID (`apns_key_id`), private `.p8` key contents (`apns_private_key`), and app bundle ID (`apns_topic`, normally `com.daythread.app`). Equivalent environment variables are `OMI_APNS_TEAM_ID`, `OMI_APNS_KEY_ID`, `OMI_APNS_PRIVATE_KEY`, and `OMI_APNS_TOPIC`. The private key remains on home-core and is redacted from configuration reads and archive exports. Home-core needs outbound HTTPS access to Apple's APNs service. Inference remains on the configured model service; notification titles, previews and routing metadata go through Apple.

Build the iPhone app with a provisioning profile that enables Push Notifications for its bundle ID. The project includes the `aps-environment` entitlement; `APS_ENVIRONMENT` defaults to `development` for Debug and `production` for Release. Enable push notifications in the app's Settings and grant iOS permission. No Apple credentials or provisioning changes are included in the repository. Without configured APNs credentials or permission, foreground internal notifications still work.

Messages include the rewind duration and window time, recap date and preview, translation language and recording, or weekly review date. Tapping opens the saved result. Control lock-screen previews in iPhone notification settings. Foreground devices renew a 60-second presence lease and acknowledge consumed events; unacknowledged events become eligible for push after backgrounding or lease expiry. Failed delivery retries for up to one day. Accepted generation jobs recover after a backend restart. Device registrations and delivery queues are excluded from backups and must be registered again after restoration.

Simulator builds and mocked APNs tests validate code paths; physical-device delivery with valid Apple credentials, permission, background/terminated state, and a foreground transition still requires an end-to-end device check.

## Weekly review (web)

Open **Diary → Weeks** to generate an on-demand review of the last completed Monday–Sunday week, compared with the previous calendar week. Week boundaries use the saved backend IANA timezone, including DST; **This week so far** shows a partial cutoff. Daily recaps are not required. Every item includes an exact quote and an inspectable original transcript, with correction, history, deletion and audio when available. The four groups cover supported decisions, commitments needing attention, recurring discussions and supported changes.

Commitments are qualified as **explicitly still open** or **follow-through unconfirmed**. Silence or disappearance never establishes completion. This two-week view is not an enduring backlog of older commitments. Cross-week changes require distinct sources in both weeks; recurrence requires two local dates and selected-week evidence. Whole overlapping chunks are preserved; boundary chunks supply context but cannot alone establish a dated decision, change or recurrence. Coverage distinguishes pending/empty/expired transcripts, recorded intervals, known gaps and unavailable audio. Unuploaded phone backlog remains unknown.

Generation runs in a durable local queue independently of transcription, survives leaving the page and recovers interrupted work after backend restart (at most three interrupted attempts). Reads never invoke the model; unchanged requests reuse a version, while **Regenerate** forces a new one. The UI polls only the active job. Model/schema failures and processing-budget failures remain visible; they never publish a partial review. The initial bounds are 2 million transcript characters, 256 model calls and eight reduction rounds, with bounded per-call evidence. All eligible evidence enters extraction; no chat sampling is used.

Late evidence in either week, corrections, source states, hints, timezone and model configuration make saved reviews stale. Publication rechecks the full scoped fingerprint inside a write transaction. Historical versions retain the original evidence and timezone; source inspection shows the current transcript. Deletion or transcript expiry conservatively purges all weekly versions/jobs and restored Markdown projections. Downloaded exports and earlier backups are independent copies. Backups preserve version history and queued jobs. Update **LLM revision** in Settings when replacing a model behind the same route name.

Scheduled generation and weekly consumption of the existing structured inbox lifecycle are follow-ons. Native completion notifications are described above. Weekly synthesis currently uses original transcripts directly; it does not read or mutate inbox status or decision records. Current range chat remains useful for follow-up, but its retrieval is not exhaustive. Quote validation checks traceability and format; semantic claim support and real local-model quality still require evaluation.

## Editable inbox

Open **Inbox** on the web or **Day → Inbox** on iPhone to review promises, standalone deadlines and unanswered questions across all days. Items keep pending/completed/dismissed status, editable title/kind/owner/deadline, explicit unknown values, transcript excerpts, review history and duplicate links. User edits and status survive re-extraction. Questions mean no answer was found in the scanned context; they are never automatically marked answered. Model proposals need review and do not establish that every obligation was recorded or found.

New transcripts, including Omi imports and corrections, queue independent extraction after a short settling period. Older transcripts already in an upgraded archive are scanned only when you select dates (default seven days; up to 31 per request). Scan/retry controls and coverage distinguish unscanned, waiting, failed and completed windows. The backend worker must be enabled. Extraction uses only the configured local LLM, retries durably, and reduces truncated output into smaller windows. Real model precision, recall and throughput have not been verified.

Every proposal needs validated exact transcript spans. Ownership stays unknown when unsupported; I/we and speaker labels do not identify the wearer. Automatic deadline resolution intentionally handles only explicit ISO dates and unambiguous today/tomorrow (including Danish) against capture time in the archive timezone. Other phrases remain unresolved for manual review. Date-only deadlines retain their precision; changing archive timezone does not rewrite accepted reviewed deadlines. There are no notifications or external task-manager synchronization.

Corrections flag affected evidence for review and re-extract with new revision IDs. Historical excerpts are labeled as prior transcript evidence. Audio expiry keeps transcript-backed items but disables playback. Source deletion or transcript expiry conservatively removes affected items, manual edits, history and jobs, including items whose extraction used that source as context. Content-free source tombstones prevent automatic inbox resurrection on replay. Existing exported backups remain historical snapshots. Inbox review requires backend connectivity; failed saves preserve the open draft, while leaving the editor discards unsaved changes. Inbox content is not added to the phone capture outbox or persistent cache.

## Optional Omi API import

Settings → Omi connection can import existing cloud conversations using Omi's [personal Developer API](https://docs.omi.me/api-reference/conversations/get-conversations). Create an `omi_dev_…` key with `conversations:read` in Omi's Developer settings, then save it as **Omi API key** under **Models, keys & server settings → Omi connections** in app or web. `OMI_OMI_API_KEY` remains an environment default. The saved key stays on the backend, outside public settings and application archive exports. Leave it empty to use direct BLE capture without an Omi account.

Import the latest 25 conversations, then use **Load next 25** to continue. Each page requests `include_transcript=true`; pagination keeps the same end timestamp and honors Omi's continuation headers, including filtered empty pages. Imports preserve conversation times, upstream IDs and speaker segments. Finished conversations with transcripts become searchable sources for rewind, diary and chat without running STT. Unfinished or empty conversations are counted as skipped. Reimporting an existing conversation preserves local corrections and retention decisions; this is a snapshot import, not continuous cloud synchronization. Existing local BLE capture continues independently.

This API supplies **transcripts only**. Imported sources have no original audio and do not offer playback. Source labels distinguish these from local recordings whose audio expired. Hosted import needs internet access and an Omi key; direct BLE capture and local inference remain independent of Omi's hosted service. The published multi-user Integration App API uses different credentials and is not this connector.

Foreground iPhone discovery accepts Omi name advertisements as well as the audio service, then validates required GATT capabilities. Reconnection uses the saved peripheral identifier. Unsupported/custom firmware still requires its own verified protocol. Pendant storage downloads and direct Wi-Fi capture are not implemented; official CV1 documentation says Wi-Fi is disabled in current firmware. Source comparisons with the official project, omibutfree, Boswell Phone, omi-whisperx and Omi Offline are in [device research](device-research.md).

## Gesture and hint rules

- Single click flags nearby speech. The fixed cooldown is **60 seconds from the last accepted flag**; extra clicks do not extend the wait.
- Meetings prefer a verified hold gesture, with **double click as the stock-firmware fallback** and phone controls always available. Stock production source uses a three-second hold for power-off, so hold mode must be selected only on verified compatible firmware. One resolved meeting gesture produces no component flags.
- Meeting hints stopped before **60 seconds** are ignored. An ongoing candidate is provisional for its first minute. Meetings have no maximum duration. After the first hour, continuous recorded quiet can end the meeting marker while ordinary recording continues.
- Automatic meeting end is fixed at **10 minutes** of recorded quiet after the first hour. The earliest automatic end is therefore 70 minutes plus the 30-second evidence settling buffer. There is no meeting timing setting, and legacy saved quiet-period values cannot change this rule.
- Flags and meetings are **approximate contextual hints**. The model sees a fixed minute of surrounding speech before/after relevant gestures. Original button timestamps are retained for provenance, not presented as exact conversation boundaries.
- Quiet must be verified in continuous uploads from the associated live recording device. Speech resets the wait; missing uploads, connection gaps, unknown detection, imported files and receipt-timed audio cannot count as silence. Web starts can associate one fresh, active pendant. iPhone microphone recordings are finished file uploads and require manual meeting end.
- New web/iPhone ends target the meeting's ID. A delayed end cannot reopen an automatically closed meeting. Older queued events and their captured legacy policy fields remain upload-compatible; those fields cannot change the fixed timing rules.

Archive settings are shared through the backend. Appearance is stored locally in the browser or iPhone. **Start recording** and **Stop recording** affect the recording source on that device; the web cannot remotely start or stop this iPhone's microphone or pendant. The old shared **Pause capture** preference is retired and does not gate the official audio receiver. Existing recordings continue uploading and processing when recording stops. Environment variables provide initial defaults; saved runtime settings take precedence.

If meeting controls are mixed between the web app and an offline phone, the phone's meeting indicator can temporarily differ from the server while local events wait to sync. The server replays received events by their original timestamps, and the iPhone overlays its pending explicit starts/ends on the last known server state. Refresh after the queue drains to confirm the reconciled meeting state.

## Speaker detection and names

Open **Speakers** on a meeting or expand a recording on web; on iPhone, open a source
or the meeting's **Speakers** link. Choose **Detect speakers**, listen to each detected
speaker's turns, and replace A/B/C with a name such as Mads. All turns then use that
name within the conversation. Source controls resolve to the containing meeting when
available, otherwise the projected conversation; contiguous chunks of a browser upload
are grouped by capture time and filename. Names are shared across those chunks and do
not automatically identify someone in other conversations or update People labels.

Enable **Speaker detection** in advanced server settings and set its local service
address (default `http://127.0.0.1:8790`). Run
`uv run --no-project --python 3.12 scripts/diarization_server.py` in a separate process.
The [configuration example](../config/omi.env.example) explains initial Community-1
model access, exported runtime variables, offline weights and Docker host addressing.
Model loading is lazy; `/health` distinguishes configured, loaded and busy states.
Detection is requested explicitly, runs locally, and supports up to eight hours of
retained audio. The optional runtime has its own Python/PyTorch dependencies.

Playback joins the selected speaker's original speaking turns in order; overlapping
voices can remain audible. Names and assignments can be corrected, merged or reset.
Text is shown beside turns only when existing transcript timestamps support an
unambiguous assignment; enable **STT segment timestamps** in Settings with a compatible STT
provider to request timestamps for future recordings. The original transcript stays intact.
Transcript edits and late
chunks mark assignments stale. Detecting again explicitly replaces labels and names.
Audio expiry disables playback; transcript expiry or source deletion removes associated
speaker metadata. Profile isolation and archive backup apply to these records too.
Real recording accuracy still needs evaluation; detection can miscount or confuse speakers.

### Remembering speakers across conversations

Enable **Remember speakers** in advanced server settings, then use **Remember for future
conversations** on a reviewed speaker. Choose a saved voice identity or explicitly create
one by name. Identical names do not merge identities. Plain conversation names and model
suggestions never create training examples. You can also enroll previously named
conversations while their original audio is retained.

Enrollment rechecks up to 60 seconds of that speaker's current original turns with the
local model. It requires at least ten seconds of usable clean speech, excludes detected
overlap, and rejects distorted or mixed samples. Recognition needs confirmed examples
from at least two independent conversations per identity. Duplicate source audio and
meeting/source aliases count as one conversation. Unknown and ambiguous speakers keep
their letter labels. Names are never applied automatically: **Suggested: Mads** needs
explicit confirmation, which saves the name and a confirmed example.
Use automatic speaker-count detection for future voice suggestions; a forced speaker
count produces labels without recognition vectors. Enrollment always rechecks its sample
without forcing a speaker count.

Suggestions stay disabled until an evaluation passes on separate real recordings. Copy
the [manifest shape](speaker-evaluation-manifest.example.json) and replace all placeholders
with your held-out local 16 kHz mono PCM16 WAVs and manually checked speaker identities.
The `speakers` map must label every cluster returned by the local `/diarize` service,
using a saved profile UUID or `null` for unknown/mixed voices. Profile UUIDs are returned
by `GET /v1/voice-profiles`. Split whole conversations into calibration and test sets,
with different conversations and audio from enrollment and each other; include other
days, rooms, microphone positions, similar voices and unknown people. Do not tune on
test outcomes or use synthetic fixtures as recognition evidence.

```bash
uv run python scripts/evaluate_speakers.py \
  --archive data/omi.sqlite3 \
  --manifest /absolute/path/to/held-out-recordings.json \
  --service-url http://127.0.0.1:8790 \
  --output data/voice-evaluation.json
```

The evaluator sends actual WAVs to the local model, chooses similarity/margin cutoffs
only on calibration recordings, and evaluates the untouched test recordings. The gate
requires observed precision of at least 95%, at least 50% correct coverage of known
speakers, at least 30 accepted test suggestions across five conversations, and zero
false accepts across at least ten eligible unknown-speaker cases. Reports also include
Wilson confidence intervals; these measurements do not guarantee future accuracy.

Set **Speaker recognition evaluation report** to `voice-evaluation.json` after a genuine
passing run. A relative path resolves inside the selected archive directory. Reports
bind exact model weights/code, confirmed examples and source provenance. The backend
recomputes report outcomes instead of trusting a `passed` flag. Changed examples or
models require evaluation again; a changed embedding model also needs compatible new
enrollment examples. No passing production report is bundled or generated by tests.

**Saved voices** lets you remove a wrong example or forget an identity. Transcript edits,
speaker corrections and re-detection remove affected examples, including shared-source
aliases. Source deletion and audio/transcript retention remove their dependent learned
representations. Forgetting a saved voice preserves manually confirmed conversation names.
Voice profiles and query vectors remain inside their archive and follow archive backups.
Evaluation reports contain held-out voice representations and are operator-managed files:
protect and remove them separately, including obsolete reports and backup copies.

## People, projects and manual timelines

Open **People** on web, or **Day → People & projects** on iPhone. Create people/projects with names, aliases and a distinguishing description; duplicate names are allowed. Open a source's **People & projects** controls to add an explicit association. For people, choose Related (no participation claim), Mentioned, or Participant (confirmed by you). Projects use About project. Imported speaker names/numbers remain unverified Omi metadata, and speaker numbers apply only to their source.

Each label has a cross-day timeline with server-calendar date filters and source access. Select recordings explicitly to group a conversation or write a cited personal Decision note/Open question. Personal decision notes are organizational annotations, separate from the existing decision register. Use **Link an existing decision register record** to associate a canonical record and open its current status and exact evidence without changing confirmation. These are user annotations, with source-level precision and no inferred resolution status. Manage a label to rename/edit aliases, merge into another of the same kind, split checked assignments into a new label, or delete it. Deleting a label keeps recordings but removes attached annotations; it does not redact names in transcripts.

Corrections mark associated links and annotations **Needs review**. Review current sources and save/confirm again. Transcript deletion/expiry removes associations, dependent note content and annotation history; audio expiry retains transcript-backed functionality. Deleted Omi imports are suppressed on subsequent imports through durable tombstones. Label mutations use an archive metadata revision plus a stable mutation ID: conflicting edits require refresh and review, and retrying an acknowledged request cannot resurrect deleted content. Metadata edits require a connection; failed edits keep their draft on the current screen. Native cached day/chat content is invalidated after the backend reports destructive archive changes; offline copies can only reconcile after reconnecting.

Manual labels do not influence existing recap/chat, weekly review, inbox extraction or conversation-preparation prompts. Suggested label associations, entity-scoped chat/briefings and automatic speaker naming remain unimplemented; the existing inbox, structured decision register and literal conversation-preparation flow retain their separate contracts. Remembered voice suggestions use the separate explicit enrollment and evaluation flow above. Manual labeling introduces no new model or hosted service dependency.

## Local inference

Use **web Settings → Models, keys & server settings** or **iPhone Settings → Transcription → Models, keys & all server settings** to configure backend settings. Search or browse the groups for transcription models and language routes, LLM task routes, endpoints, reasoning options, gateway credentials, Omi API/forwarding keys, embeddings, capture hints, retention and processing. Managed subscription account owners can save their own STT and embedding endpoints and credentials; these stay in that account's private server settings. Route editors support inherited defaults, route-specific keys and no authentication. Saved keys are never returned to either client; select Keep, Replace or Clear explicitly. Concurrent edits are rejected with a reload message so one client cannot silently overwrite another.

STT/LLM configuration, keys and ordinary preferences apply to subsequent work immediately. Embedding configuration, worker enablement, web assets and archive paths are saved with a **restart required** marker; the response lists pending fields. Restart the backend process using your deployment's normal procedure. Enabling embeddings still requires a model, immutable revision and dimension in one valid save. Changing `data_dir` selects that archive on restart; it does not migrate existing recordings.

The backend stores service overrides and keys in `.private-settings.json` in the **bootstrap** archive directory (the initial `OMI_DATA_DIR` location). Writes are atomic with file mode `0600`; `.env` is preserved. This private file is excluded from application archive export/restore, and keys never enter the SQLite runtime-settings table. Keep a separate private copy when moving the server; general filesystem backups of the directory will include it. Existing archive preferences continue to use SQLite. Saved overrides take precedence over environment defaults. The settings API returns write-only key status and uses `Cache-Control: no-store`; the configuration screen keeps entered replacements only in memory until save/reload.

Open **Settings → Models & server** and fill in the separate **Speech-to-text (STT)** and **Language model (LLM)** endpoint URL, API key and model fields. Provider keys can use Keep, Replace, Use shared key or No authentication. Route keys take precedence over provider keys; provider keys can inherit the shared gateway credential. These credentials are never app login credentials. Endpoints accept local HTTP(S) and explicitly configured hosted HTTPS URLs. Public HTTP, embedded URL credentials, queries and fragments are rejected. There is no automatic hosted fallback or redirect following.

The starter `.env` omits all `OMI_STT_*` and `OMI_LLM_*` entries. Existing environment defaults remain supported for compatibility, and saved Settings values take precedence. To remove old entries safely, **stop the backend** and run `uv run python scripts/migrate_model_settings.py` in the backend's environment and archive location. Use `--env-file /path/to/.env` for a different environment file. The migration preserves saved UI values, writes effective model defaults and keys to the owner-only private settings file, then removes model entries from `.env`. It preserves unrelated entries and does not open or modify the archive database. For Docker, use the container's archive volume; a host-side run against another data directory would configure the wrong installation. Keep the private settings file with the deployment: application archive exports exclude it.

Local models remain the default. Configuring a hosted endpoint sends audio to the transcription provider, prompts and selected transcripts to the language model provider, or indexed transcripts and queries to the embedding provider. Use a provider implementing the existing compatible `/audio/transcriptions`, `/chat/completions`, or `/embeddings` contract; provider-specific protocols and non-Bearer authentication are not supported. Credentials stay on the backend.

**STT chunk seconds** in the **Transcription** settings group controls maximum audio duration per model request (1–120 seconds, default 30). Changes apply to subsequent processing without restarting. Choose a duration every configured speech route accepts; gateway model lists may expose only token limits, so audio duration is not discovered automatically. Longer recordings are split internally, their text joined in order and segment timestamps adjusted; the archived audio, source identity and capture times stay intact. All pieces share one inference deadline, and no partial transcript is published if a piece fails.

STT can use a different model/server for each language. Under **Transcription options & language routes**, add routes such as `da`, `en`, or a detector's full language name such as `danish`. Enter each route's model, endpoint or API key overrides; blank overrides inherit the STT connection defaults.

With a language selected in web/iPhone Settings, transcription uses its route directly in one request. Route keys are case-insensitive; regional codes match an exact route first, then the base language (`da-DK` → `da`). Unconfigured languages use the shared STT settings. Each route can override `MODEL`, `BASE_URL`, and `TOKEN` independently; omitted values inherit the STT connection defaults, with the provider key falling back to the shared gateway credential. An explicitly empty `TOKEN` removes authentication. Routes use the same compatible transcription contract over local HTTP(S) or hosted HTTPS; credentials remain stored on the backend and are write-only in the editors.

With **Automatic / mixed languages** and language routes configured, the initial STT receives `response_format=verbose_json` without a forced language. Configure a multilingual model that supports that format and returns a `language` string. When that string matches a route code or configured provider-name key (for example `da` or `danish`), a different route transcribes the same original chunk once more. Only the final result and its timestamps are saved. Missing, unknown or mixed-language metadata keeps the initial result; the same resolved model/endpoint/token is never called twice. Detection and specialist requests share the configured inference deadline. Specialist failures use the existing durable retry path, without publishing preliminary detector text.

Managed subscription users can use these same language routes with their own STT endpoint and key. Route credentials are stored in the account's private settings and are not shared across managed accounts.

An optional `auto` language route overrides the initial automatic model, endpoint and key. With no language routes, automatic transcription retains its existing single request. Automatic routing is per chunk, based on provider metadata; it does not split code-switched speech between models. Use a multilingual route for mixed recordings. Language names are matched literally, so add the names returned by your provider alongside ISO-code routes when needed. No models are downloaded, and language detection/quality still requires validation against your local service.

Every LLM task inherits the LLM connection's model, endpoint, API key and revision by default. Under **Language model options & task routes**, add overrides for **chat** (final answers), **summary** (diary, meeting, rewind and evidence reduction), **extraction** (inbox and decisions), **preparation** (conversation briefs), **weekly** (weekly extraction and synthesis), or **retrieval** (alternate query wording after a miss). Each route can override the model, endpoint, key and revision independently. Use No authentication to omit a credential or Use provider key to inherit the default. Change the revision when replacing weights behind the same model alias. Saved changes apply immediately; STT and embeddings retain their separate settings.

Generated output records the selected model. Diary and weekly fingerprints include the task's model, endpoint and revision, and extraction jobs use the extraction route identity. Changing only the chat route leaves diary/weekly caches valid. Task credentials are excluded from public settings, generated output, cache identities and backups.

Chat uses one answer-model call when bounded topic retrieval succeeds. Broad recall samples chronological source IDs in SQLite before loading transcript bodies; with a limit of one it chooses the most recent eligible recording. Database sampling runs outside the event loop. Follow-ups load only the six recent messages used for model context; opening a saved chat still returns its complete history. Short referential follow-ups carry the prior topic into retrieval, and inferred date scopes survive multiple follow-ups. Large selected transcripts are reduced in bounded summary-model batches, then answered directly by the chat model without an extra final-summary call. Full selected evidence and source validation are preserved.

If a topical chat search returns no sources but searchable transcripts exist in its scope, `OMI_CHAT_QUERY_EXPANSION=true` permits one query-rewriting call using the `RETRIEVAL` route (shared model by default). It requests at most 20 English/Danish search terms and retries retrieval within the original date/meeting scope. The default total expansion deadline is five seconds (`OMI_CHAT_QUERY_EXPANSION_TIMEOUT_SECONDS`). Failed/invalid rewrites retain the no-match result and disclose unavailability. Disable it for a strict single-answer-call path. `/v1/search` remains retrieval-only. Rewriting mechanics are tested with controlled responses; real multilingual gains still require the configured model.

Web and iPhone chat use `POST /v1/chat/stream`, a newline-delimited JSON stream of `status`, `delta`, `heartbeat`, then terminal `done` or `error` events. `done.result` has the same saved answer/source identifiers as `POST /v1/chat`, which remains compatible for other clients. Model streaming uses bounded OpenAI-compatible SSE; compatible gateways returning ordinary JSON still work. Draft text is provisional: unknown citations, source corrections, truncated/incomplete streams and model errors never publish/save a partial answer, and clients discard the draft on failure. Stop/navigation cancels generation and closes the provider stream; only final answers enter the native cache. A cancellation racing an already committed answer can still leave that completed exchange in history. Native streaming uses [URLSession.AsyncBytes](https://developer.apple.com/documentation/foundation/urlsession/bytes%28for%3Adelegate%3A%29) in an ephemeral session.

Streams send `Cache-Control: no-store` and `X-Accel-Buffering: no`; a private reverse proxy must also allow incremental response delivery. `timings` records retrieval (including expansion), expansion, generation, first token when streamed, and pipeline total milliseconds before persistence. Real answer latency depends on local model loading, context size and gateway contention; the fictional retrieval evaluation below measures search alone.

Adapters use HTTP `/audio/transcriptions` and `/chat/completions`; the existing home-core Wyoming service on port 10300 needs an HTTP bridge before it can be used here. The configured gateway and previous `192.168.30.122:18080` address were unreachable from this Mac during validation, so real STT/LLM quality and latency have not been verified. When models are unreachable, audio remains queued and summary/chat errors remain visible.

Optional **STT segment timestamps** in Settings requests verbose segment timestamps from compatible providers. Local speech filtering skips detected silence and non-speech in transcription copies by default; original playback remains complete. See [speech processing](#speech-processing-and-cleanup-review) for optional cleanup and limitations. Retrieval combines keywords with local date scoping, including today/yesterday/ISO dates and English/Danish weekdays. Broad day queries sample bounded evidence; semantic retrieval is opt-in and remembered voice suggestions require separate enrollment/evaluation. Answers supply source references and reject unknown cited UUIDs, but model accuracy still needs checking against source audio.

### Speech processing and cleanup review

Transcription preserves original archive files, hashes, capture times and playback.
**STT filter speech** is enabled by default in Models & server. Local WebRTC VAD checks both
original and amplified audio and sends detected speech with 300 ms of context on each side.
Nearby spans merge; discarded gaps are not concatenated or retimed. Transcripts and cleanup
candidates retain offsets into the original recording. Entirely non-speech recordings make no
transcription request. If detection is unavailable or inconsistent, the full original audio is
sent instead. Short unobserved tails are retained conservatively. This needs no cloud detector.
Speech detection is approximate: quiet or overlapping speech may be missed, and background
speech or noise may be retained. Disable **STT filter speech** to transcribe full recordings.
The setting affects subsequent processing, not completed transcripts.

Summary requests also use a local text copy with unquoted hesitation sounds such as
`ummm`, `uhm`, `ehm`, `øhhh` and `øhm` removed. Whole annotation lines such as `[silence]`
or `[background noise]` are omitted. Cleanup runs before batching, so filler-only input
needs no summary request and long filler runs do not create extra reduction calls.
Original transcripts, search evidence, translations and chat input remain unchanged.
Quotes, code, URLs, ordinary words, repetitions and short meaningful answers are kept;
there is no generic stop-word removal or extra AI cleanup call. Summary pipeline traces and the persistent usage report record original/retained
character counts, removed fillers and omitted sources. Token counts are measured only
from provider responses; there is no estimated token-saving claim. The versioned policy invalidates prepared
conversation notes and daily recaps. Weekly reviews and structured extraction continue
to use original text because their exact evidence quotes must match the archive.

**STT preserve quiet** separately avoids the old volume gate and remains enabled by default.
To opt into that older gate, disable preserve quiet and set `OMI_SILENCE_THRESHOLD` (35 by
default; 0 disables that legacy gate). Keep it enabled when evaluating quiet speech.

**Settings → Audio & credit usage** reports captured audio (recordings that have started
processing), retained audio including speech context, total submitted audio including
language-specialist/cleanup passes and retries, and known transcription, summary, translation,
chat and other AI credits. Durations use readable seconds, minutes, hours, days, weeks and
fixed 365-day years, with at most two units. The month picker defaults to the current month in
the archive timezone; all totals, notices and daily rows follow the selected month. It refreshes
every 30 seconds and groups daily
totals by processing date in that timezone. These measurements start after upgrade; queued audio and earlier requests
are not reconstructed. Per-profile metadata persists across restarts, recording deletion and
archive backups without storing audio, prompts, response text or provider keys in the ledger.

The same report includes summary source characters before/after filtering, removed fillers,
omitted sources, skipped no-content summary passes, submitted request characters, request
counts and known provider input/output tokens. Original/retained text is counted once per
logical summary pass, not once per unique recording. Actual submitted request text includes
instructions, source references, reduction passes and retries, so it can exceed retained source
text. Repeated attempts count submitted text each time; token and credit receipts for the same
managed operation count once. Translation and chat each have separate request, submitted-text,
token and credit totals. Chat includes query expansion and evidence reduction, even when these
use another model route. Older requests without these measurements remain marked unknown
rather than inferred from text size.
Summary run IDs connect local filtering with all of its downstream model requests; no text is
saved to the usage ledger.

Unique summary audio measures the union of original recording intervals considered before text
filtering, including skipped content and imported transcripts. Overlaps and later summaries of the
same recordings count once across the selected month, or once within each processing day in daily
rows. The monthly total therefore need not equal the sum of daily coverage. **Audio across summary
passes** separately counts the recordings again on each logical pass; this can exceed captured
audio substantially. The API retains this cumulative measurement as `summary_source_minutes`
and exposes unique coverage as `summary_unique_source_minutes`. Reduction requests
and HTTP retries within a pass do not multiply these minutes. Cached conversation notes retain
the scope of their original recordings. Older scalar-only measurements cannot be deduplicated;
their unique coverage remains explicitly incomplete, along with missing/invalid recording times.
These are recording durations, not billable summary minutes or a conversion
from credits: summary cost depends on the actual text and tokens submitted.

Provider attempts are saved before sending and finalized afterward. Pending/interrupted
requests remain visible with unknown charges and token measurements. Actual Daythread credits come from confirmed
managed-gateway settlement headers, not estimated model prices or reserved credits. A gateway
using your own provider reports zero Daythread credits; that provider's charges are separate.
Endpoints without billing metadata leave charges unknown. `GET /v1/usage` exposes the same
profile-scoped report for the current month; `?month=YYYY-MM` selects another month. For a wearable trial, compare captured/retained/submitted
minutes and separate credit categories across workdays and weekends.

Quiet transcription copies receive uniform amplification toward -25 dBFS RMS, with peak
headroom and at most **STT max gain dB** (default 12 dB in Settings). Set 0 to disable. This changes amplitude,
not signal-to-noise ratio, and cannot uncover a quieter voice masked by a louder one. For
internal STT request splits, **STT boundary search seconds** (default 0.5 in Settings) searches for a 100 ms quiet
gap near the boundary; 0 restores fixed cuts. Every sample within each retained speech span is retained exactly once. Phone
chunk boundaries and existing captured gaps are unchanged; there is no cross-source context
or word deduplication.

**STT enhancement mode**, set to **on_empty** by default in Settings, creates a cleanup **review reading** only when a chunk's
original pass is empty. Choose `compare` to also compare noisy nonempty transcriptions, or
`off` to disable. FFmpeg must be installed on home-core and on its process `PATH`; the Docker
image includes it. On a standalone Mac, an existing Homebrew installation can supply it with
`brew install ffmpeg`. The fixed local profile applies mild FFT noise reduction plus bounded
leveling, compensates its delay, and retains the exact sample count. It uses raw in-memory
pipes, no temporary audio files, no downloaded models and no hosted fallback. Missing FFmpeg,
invalid output or provider failures leave the original transcript usable and disclose cleanup
unavailability. Each filter process has a five-second limit; the entire optional pass shares
**STT enhancement timeout seconds** (default 15 in Settings) and the remaining inference budget. All
required original passes finish first. `compare` can roughly double speech-model requests
(including language detection/specialist calls); it is not enabled by default.

In the **web recording details**, open **Alternative cleanup reading · needs review**, listen
to the original recording, then use **Review and correct transcript** to save verified wording.
Even an empty-original recovery is only a candidate: it never enters FTS/semantic search,
recaps or chat automatically. The iPhone currently shows the canonical transcript; use the
web app for candidate comparison. Corrections clear stale candidates and use existing revision
history/invalidation. Candidate text follows transcript retention, deletion and archive backups;
audio retention alone keeps it as transcript data. Raw pauses and noise remain in playback.
Old recordings are not automatically reprocessed. Retained empty/silence-gated recordings
offer **Retry empty transcription with current settings** in the web source details; corrected
or nonempty completed transcripts cannot be overwritten through this retry action.

Speech filtering changes only transcription request copies; it does not delete archive audio.
No denoiser separates a presumed background speaker from the canonical pass, and no speaker
names are invented. Optional diarization is available
through [speaker detection](#speaker-detection-and-names); reliable simultaneous-speaker
separation is not implemented. See the [research](audio-processing-research.md)
and [reviewed implementation plan](audio-processing-plan.md) for the model-service acceptance
work still needed. Synthetic tests establish timing/preservation, not recognition accuracy.

To compare actual labeled local WAV recordings (16 kHz mono PCM16, up to 120 seconds each),
copy [the manifest example](audio-evaluation.example.json), supply your own fixture paths,
references and required phrases, then run:

```sh
OMI_STT_BASE_URL=http://your-stt.local/v1 OMI_STT_MODEL=your-model \
  uv run python scripts/evaluate_audio.py /path/to/manifest.json --live > /tmp/omi-audio-evaluation.json
```

The command reads explicit `OMI_*` environment configuration, not `.env`, private settings or
the live archive. It contacts only configured local STT, with no model/fixture downloads.
Results separate raw baseline, gain/boundary processing and unreviewed cleanup; unavailable
cleanup is counted rather than scored as a success. Reports include WER, deletion rate,
required-phrase recall, false speech on empty references and per-category/language results,
plus p95 total processing/review latency. Reports contain metrics and fixture IDs, not speech
text. Measure heldout clean, quiet, noisy, overlapping and noise-only fixtures; check names,
dates and each simultaneous speaker by listening. WER alone depends on the arbitrary ordering
of overlapping words. Do not claim accuracy gains before these checks pass on real audio.

For a private TLS CA, export `SSL_CERT_FILE=/path/to/trusted-ca-bundle.pem` into the process environment, or mount/set it in Compose. Certificate verification remains enabled.

## Optional local semantic retrieval

Semantic retrieval is disabled by default. The [semantic retrieval guide](semantic-retrieval.md)
describes the implementation audit, token/topic chunking, exact source provenance, passage-level
hybrid retrieval, optional reranking, safe migrations/rebuilds and explicit CPU-only Qwen3 setup.
The backend uses configured compatible endpoints; it never downloads weights or requires cloud
inference. Keep one shared multilingual index by default, and use the actual pinned model tokenizer
for useful 320/512/48-token chunks. The dependency-free UTF-8 byte bound is conservative for Qwen3.

Source corrections, deletion and retention preserve existing citation safeguards. Late topic
boundaries requeue work without blocking initial indexing. Staging and attempt leases prevent
incomplete or obsolete jobs from publishing evidence. Keyword fallback and progress remain visible;
web Semantic search settings include an explicit safe rebuild action. New model contracts backfill
separately and never mix vector spaces. Database migrations run automatically on archive opening.

The [retrieval evaluation guide](retrieval-evaluation.md) documents synthetic multilingual fixtures,
metrics and explicit real-model comparison commands. No real embedding model or cross-language
quality was validated during this change. Exact scans remain linear in archive size; see the
recorded synthetic performance measurements before planning a multi-year deployment.

## Archive and backup

The default `data/` directory holds SQLite WAL, WAV recordings, and generated `diary/YYYY-MM-DD.md` files. Weekly review versions and durable jobs live in SQLite; backups also render immutable `weekly/<version-uuid>.md` projections. Keep one backend worker. Transcription retries survive restart; daily generation uses the configured local calendar, including DST boundaries, and catches up after downtime. Late evidence and corrections invalidate derived recaps. Transcript corrections and generated diary versions are auditable; chat sessions persist locally.

Daily recaps and decision proposals update automatically when new or corrected transcripts become available, including late recordings added to earlier days. The worker checks every five seconds and reuses unchanged evidence without new inference. Decision extraction continues through bounded batches automatically; proposals still require review before confirmation. Recaps use a small number of subject-based sections, group related discussions across recordings, and consolidate repeated points. Each topic has one coherent summary with a copy-to-clipboard control; collapsed cards show a shortened preview of the same summary, not a second short-summary version. Original transcript passages stay behind an optional disclosure and favor the selected topic when matching. Unmatched recordings remain available through an explicit full-recording control instead of displaying unrelated transcript text inline. Decisions and real unresolved questions stay with their topic; recaps omit repeated date titles, empty categories and absence-of-information boilerplate. Later evidence can rewrite or merge sections to keep the whole day readable. Format changes invalidate saved recaps so the worker can rewrite them automatically. The legacy `OMI_RECAP_HOUR` and `OMI_RECAP_INTERVAL_MINUTES` settings no longer delay updates. Uploaded recordings and finished transcript imports become eligible after transcription. Live conversations wait for five minutes of contiguous recorded non-speech by default (`OMI_RECAP_QUIET_MINUTES`), followed by a 30-second settling buffer. Transcription continues while you speak; a new spoken passage resets the pause. Missing uploads, capture gaps and failed speech detection never count as quiet. A sufficiently long observed pause can qualify a live conversation for the diary even if its meeting label remains open.

Automatic generation groups eligible recordings after 15 seconds without upload/transcription changes. Known pending file uploads can extend this batching window to 60 seconds. This limit never forces an ongoing live conversation into a recap, and ongoing live chunks do not delay an otherwise eligible uploaded file. Each multipart file waits until all parts have arrived and finished transcription, including silent parts. Explicitly finished files can qualify before matching ongoing live alternatives; their evidence is combined when both are eligible. The worker checks every five seconds; model processing and app refresh add latency. Recap generation remains independent of decision extraction. Speech detection continues filtering STT copies when enabled without deleting originals; `OMI_RECAP_QUIET_MINUTES` also controls conservative conversation grouping for speaker detection.

Older web file uploads recorded with `device_id=web` and no source kind are also treated as finished uploads for recaps. Their original metadata and retry identity stay unchanged.

Raw Omi audio webhooks carry receipt-time estimates without capture ordering or stream continuity. Their quiet payloads cannot establish a continuous recorded pause, so raw streams stay pending for daily recaps; import finished Omi conversations for explicit boundaries. Raw webhook transcripts/audio remain searchable and playable.

Each paused live conversation or completed file is summarized once per source/model/language/policy version, preserving original citations. Fillers and unsupported fragments can produce a cached no-content result; short meaningful answers remain in context. Daily synthesis uses these independent notes rather than repeatedly rewriting the previous recap. The apps show the included cutoff and recordings awaiting upload or transcription. New ongoing live conversations remain pending while previously completed ones stay visible. Late speech that invalidates an earlier pause suppresses the affected current recap until safe regeneration. New independent arrivals during inference can be included in the next version; corrections, deletion or regrouping of selected evidence still reject stale output. Cross-midnight live conversations remain whole and can appear on both overlapping days after their pause, retaining the original capture intervals. Transcript corrections, deletion and transcript expiry clear prepared summaries. Silence/noise recordings keep the same audio retention as speech (seven days by default).

Audio and transcript retention are separate, configurable in web and iPhone Settings. Defaults are **7 days for audio** and **30 days for transcripts**; **0 keeps data indefinitely**. Saved runtime settings override environment defaults, and existing explicit settings are preserved on upgrade. Shorter periods apply to existing recordings when saved. Audio expiry removes playback while retained transcripts remain searchable; transcript expiry also removes dependent summaries, notes and related inbox history. Earlier backups retain their own copies. No backup includes `.env` or inference credentials.

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

### Change the storage location

The archive location is a backend deployment setting, separate from the retention controls in web and iPhone Settings. It moves the SQLite database, recordings, transcripts, saved settings and generated notes together. It does not change the iPhone's local capture buffer or the storage used by separate model services.

**Source installation:** set an absolute path in the repository's `.env`, then restart the backend:

```dotenv
OMI_DATA_DIR=/path/to/omi-data
```

The backend creates the archive directory if needed; its user must be able to write there. For an external drive, use its full mounted path and ensure it is mounted before starting the backend. Changing this setting to an empty directory starts an empty archive; it does not move existing data.

To move an existing source archive, run these steps from the repository root:

1. Stop the backend and any other writers to the old archive.
2. Back up the old location and restore into the new location. Replace these example paths; the destination's parent directory must exist, but `omi-data` itself must not exist yet.

   ```sh
   uv run python scripts/archive.py backup /path/to/omi-migration.zip --data-dir /path/to/old-data
   uv run python scripts/archive.py restore /path/to/omi-migration.zip /path/to/omi-data
   ```

3. Set `OMI_DATA_DIR` in `.env` to the restored absolute path and restart the backend. Check that existing transcripts, notes and retained audio are available before removing the old copy. Keep only one backend writing to the selected archive.

Restore validates the database and recording checksums and updates stored audio paths. Its default extracted-size limit is 100 GB; for larger archives, pass an appropriate byte limit, for example `--max-bytes 1000000000000` for 1 TB. Allow space for the restored archive and backup, plus temporary export files on the machine creating the backup. The backup excludes `.env`; keep the existing configuration separately.

**Docker Compose:** the supplied configuration explicitly sets `OMI_DATA_DIR=/data` inside the container. Changing only `.env` does not change the host storage location. To select a host folder, replace the backend's named-volume entry in `config/compose.yaml` with an absolute bind mount:

```yaml
    volumes:
      - /path/to/omi-data:/data
```

Keep the container's `OMI_DATA_DIR=/data`. Create the host folder and make it writable by the container user, UID 10001. For standalone Docker, replace `--volume omi-local-data:/data` with `--volume /path/to/omi-data:/data`.

When moving an existing Docker archive, stop the backend first and copy the complete contents of its `/data` directory, including any SQLite WAL files, into the host folder before changing the mount. For example, before editing Compose's volume entry:

```sh
docker compose --env-file .env -f config/compose.yaml stop backend
docker compose --env-file .env -f config/compose.yaml cp backend:/data/. /path/to/omi-data
```

After updating the mount and checking permissions, recreate the backend with `docker compose --env-file .env -f config/compose.yaml up -d`. Verify the archive before removing the old named volume; do not use `down -v` during migration.

## Container deployment

From the repository root, create `.env` with `cp config/omi.env.example .env` if it does not exist, then review the model addresses. Docker installation does not require running the host Python/Node build first.

```sh
docker compose --env-file .env -f config/compose.yaml up --build -d
docker compose --env-file .env -f config/compose.yaml logs --tail=50 backend
```

The container builds the web client, serves it with the API using one worker, and persists a named volume. Port 8787 binds the LAN by default; set `OMI_BIND_ADDRESS=127.0.0.1` in `.env` when a local reverse proxy owns access. The explicit `--env-file .env` also applies that setting to Compose's port interpolation. Docker loopback refers to the container, so inference must use a reachable LAN/service address.

On home-core, Caddy serves **https://daythread.home.arpa** through the existing private service network to `omi-local-backend-1:8787`. UniFi local DNS must map `daythread.home.arpa` to `192.168.30.122`. The home-core front page includes Daythread with the shared app icon. HTTPS uses the same local certificate authority as the other `home.arpa` services; devices need to trust that authority. The web app provides browser icons, an Apple touch icon and a standalone web manifest; the iPhone companion uses the same artwork.

The repository-root [Dockerfile](../Dockerfile) also supports standalone Docker:

```sh
docker build -t omi-local .
docker run -d --name omi-local --restart unless-stopped \
  --env-file .env --env OMI_DATA_DIR=/data \
  --publish 8787:8787 --volume omi-local-data:/data omi-local
```

Keep the explicit `OMI_DATA_DIR=/data` override: the starter `.env` uses `data` for source installs. The image runs as UID 10001; a bind mount used instead of the named volume must be writable by that user. Run a single container per archive volume. Model services run separately and must be reachable from the container.

## Runtime and dependency baseline

Verified on 2026-10-16 against the [Python release page](https://www.python.org/downloads/release/python-3150/), [Node release index](https://nodejs.org/dist/index.json), official Docker [Python](https://github.com/docker-library/official-images/blob/master/library/python) and [Node](https://github.com/docker-library/official-images/blob/master/library/node) tags, [PyPI](https://pypi.org/) and the [npm registry](https://registry.npmjs.org/):

| Component | Selected version |
|---|---|
| Python build/runtime | `3.15.0-slim-trixie` |
| Node web build | `26.12.0-trixie-slim` (Current stable) |
| Build tooling | uv 0.13.1, npm 12.2.0, Hatchling 1.32.4 |
| Python direct dependencies | FastAPI 0.143.3, HTTPX 0.28.1, pydantic-settings 2.16.0, tzdata 2026.5, Uvicorn 0.54.0 |
| Python development tools | HTTPX2 2.13.1 (Starlette test client), pytest 9.1.1, Ruff 0.17.1 |
| Web | React/React DOM 19.3.1, React types 19.3.0, Vite 8.3.4, React plugin 6.1.2, TypeScript 7.0.2, Vitest 5.0.5 |

Docker uses explicit patch and Debian release tags, with pinned uv/npm installers. `uv.lock` and `src/web/package-lock.json` record the resolved dependencies; builds use frozen/clean installs. Python 3.15 and Node 26 passed the component checks without requiring an older runtime. The development group includes HTTPX2 for Starlette 1.8's test client; application inference continues to use the latest stable HTTPX 0.28.1. Vite 8's Rolldown/Oxc build works with the existing React configuration; TypeScript 7 requires the Vite client declarations for CSS imports, supplied by `src/web/src/vite-env.d.ts`. Vite's default browser floor is Safari 16.4, within the companion's iOS 17+ baseline. See [Vite's migration guide](https://vite.dev/guide/migration).

Upgrade validation: 207 backend tests and Ruff passed on Python 3.15.0; 28 web tests and the TypeScript/Vite production build passed on Node 26.12.0/npm 12.2.0. Clean npm installation reported zero vulnerabilities; npm's default blocking of the optional macOS `fsevents` install script did not affect tests or builds. The Python wheel and source archive both contain the web assets. Docker built on Linux ARM64 and passed API/OpenAPI, bundled asset, upload/playback and settings checks under UID 10001 with a read-only root filesystem, dropped capabilities and no-new-privileges. Audio bytes and settings survived container replacement on a disposable named volume. The default background worker also started with container networking disabled. Desktop (1440×1000) and mobile (390×844) browser checks passed Settings save/reload against the container without console errors or failed requests. Test containers and storage were removed; no deployment, live archive access or real inference calls were performed. The iOS core test suite and simulator build also passed for the endpoint failover update.

## Before the pendant arrives

1. Build and run the backend, then check `curl --fail http://localhost:8787/v1/status`. A running worker confirms service health; inference states remain `unknown` until a real request uses each model.
2. Upload a short recording through the web app using its actual recording-start time. Confirm it leaves the transcription queue and the transcript matches speech. Generate a recap and ask a question with a source reference. These checks require reachable STT and LLM services; a successful status response alone does not validate them.
3. Install the iPhone app, open **Settings → Backend endpoints**, add the home LAN address and (if used) a Tailscale/private-VPN address, test each, and choose the active endpoint. Verify Day/Settings can reach it. Choose **Double click · stock firmware** until a different gesture is verified on the actual pendant. Set the phone storage budget and Wi-Fi upload preference before recording.
4. Run the [physical-device acceptance plan](device-research.md#physical-device-acceptance-plan) when the pendant arrives. Record the model, firmware, codec, capture duration, gaps, and final outbox count. Complete the offline/reconnect and screen-lock checks before relying on all-day capture.

Keep one archive owner and one backend process for a data directory. The app/API have no authentication, so use the intended private LAN or VPN access path.

## API

`GET /v1/openapi` returns the schema. Important routes:

| Route | Purpose |
|---|---|
| `GET /v1/labels`, `/v1/labels/{uuid}/timeline`, `/v1/labels/sources/{uuid}` | Manual directory, dated timeline, source association/evidence state |
| `POST /v1/labels/mutations` | Typed manual create/edit/delete/merge/split commands; revision checks and idempotent mutation IDs |
| `PUT /v1/audio/{uuid}?device_id=...&started_at=...` | Idempotent raw PCM16 mono16k WAV upload, up to 120 seconds/4 MB. Optional `source_kind=manual_upload` and `source_id=<original filename>` (up to 255 characters) preserve manual import provenance and must remain identical on retries. |
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

## Completeness and repairs

Today/Capture now separates server receipt freshness, observed phone state, outbox backlog and processing delay. Rewind and daily recap show scoped audio/transcript availability, intentional pauses, known interruptions and unknown coverage. Old recordings and imported transcripts never imply active capture. Saved answers/recaps expose generation-time source selection and allow incorrect/missing-information reports, source inspection/correction and reviewed resolution. iPhone feedback drafts can remain local until explicitly sent.

See [completeness and feedback](completeness-feedback.md) for API contracts, semantics, retention/deletion behavior, validation and remaining hardware/model acceptance.

## Validation and remaining acceptance

- **391 Python tests** (including 31 conversation-preparation cases): weekly DST/evidence/queue/versioning/lifecycle/restore contracts plus pipeline/retries, policies, offline event snapshots, archive/versioning/settings/chat, retention/deletion races, citations, gaps, backup corruption, static routing, hosted Omi import, concurrent settings updates, compact event receipts, backup/delete/retention concurrency and rollback preservation; controlled hybrid retrieval, scoped top-k, long import tails, embedding validation/outages, indexing versions/restarts and correction/deletion races; editable inbox extraction, reconciliation and evidence lifecycle. No real embedding model was used.
- **48 native core tests**: real Opus encode/decode, packet fragments/loss/rollover/duplicate timestamps, interrupted final frames, crash reconstruction, failed-save retries, metadata reconstruction, original IDs/policies, exact receipts, storage reservations, local rewind, calendar behavior, Omi discovery matching and inbox model decoding. A temporary harness also exercised the actual AppModel with controlled suspended requests to verify pause and backend-change races; it is not part of the retained core test suite.
- **43 web tests**, including appearance persistence, system changes, tab synchronization, blocked storage, calendar behavior, inbox edits, decision evidence models and preparation context/citation rendering; production TypeScript/Vite build. Earlier Omi import flows were checked with mocked upstream responses in desktop/mobile browsers: configuration, pagination, errors/retry and transcript-only playback states.
- Completeness and feedback regression coverage includes first-receipt freshness, imports, interval unions, typed capture transitions, stale phone reports, blocked/retry processing, generation evidence, linked feedback, correction/deletion races and restore semantics. Combined validation includes the inbox, decision register, weekly review, conversation preparation and optional semantic retrieval; source selection and citations do not establish model accuracy. Four cross-feature cases check actual hybrid/diary inputs, stable chat IDs and correction/deletion/retention across feedback, inbox, decision, weekly and preparation outputs.
- Unsigned iOS Simulator (arm64/x86_64) and generic iOS device builds succeed after the hardware-readiness fixes. Physical signing, installation and Omi acceptance remain outstanding.
- Weekly desktop/mobile checks (1440×1080 and 390×844) cover generation, cited correction, stale refresh, versions, original-evidence export, scoped chat, partial/empty weeks, model failure and deletion with a disposable archive and simulated inference. Idle reads do not poll full transcripts. No browser console/runtime errors or mobile horizontal overflow were observed.
- Desktop/mobile browser checks cover actual web/API interactions, settings persistence, short meetings, rewind/empty states, archive correction and persistent chat. Populated tests use a separate temporary archive and simulated inference, never the live archive.
- Plain and age-encrypted backup/restore round trips, no-overwrite and unsafe-path rejection were verified.
- Docker/Compose image build and a read-only-container runtime smoke check passed for the web page, status API and settings API. Desktop/phone browser upload was also verified against a separate temporary archive.

The repository-prescribed `@Codex-flow/cli` security scanner is unavailable from npm (invalid/unpublished package); it could not run. Local AI connectivity and hardware endurance remain the material integration limits.

[Reviewed plan](implementation-plan.md) · [Architecture](architecture.md) · [Open-source inspiration](open-source-inspiration.md)

## Native microphone schedules

On iPhone, **Capture → Microphone & schedule** controls live phone and pendant capture.
On Android the same controls are on **Capture**. The enabled switch stops live capture when
off; already saved recordings and upload queues are preserved. Select weekdays, an enable
and disable time, and phone or Omi as the scheduled source. Times use the device's current
time zone; overnight windows belong to the starting weekday. No selected days or identical
times means no automatic capture. Schedules and manual overrides are device-local and persist
across launches, independently of the selected archive/profile.

Scheduled capture begins automatically when the app can run. **Start recording** / **Record
with phone**, pendant Resume/Connect, or manually enabling the microphone always overrides
the schedule. Manual off stays off. Choose **Follow schedule** to return to automatic control.
Existing microphone and Bluetooth permissions and profile/connection requirements still apply.

On iPhone, the app must be available in the foreground to start the phone microphone.
iOS cannot reliably wake a suspended or closed app for a scheduled start; reopen it to resume.
A running phone recording receives its scheduled stop deadline directly, including while the
screen is locked, and automatic sessions rotate at the existing two-hour recording limit.
Recording edits finish the current local recording before applying its replacement policy.
Pendant data is gated before decoding/storage and its audio subscription is disabled outside
the window. Pair the pendant first. The switch controls Daythread capture; it cannot guarantee
that a pendant's firmware stops sampling or saving its own hardware audio.

Android keeps its foreground capture service and notification active while waiting, releasing
the input between windows. Restart, force-stop, OS service termination, or capture failure
requires reopening and following the schedule again. Background scheduling, permission changes,
phone calls and physical pendant behavior still need verification on real devices.
