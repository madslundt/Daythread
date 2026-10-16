# Omi local

A local audio memory system: **Omi → iPhone → your backend and local models → rewind, diary, and chat**. The native app captures wearable audio; the responsive web app works from a computer or phone. Both share one backend. Application and API authentication are intentionally disabled.

## Run web and backend

Requires Python 3.15.0+, uv 0.13.1+, Node.js 26.12.0+ and npm 12.2.0+. The repository pins Python/Node in `.python-version` and `.node-version`; upgrade older uv installations before running the scripts. Install npm with `npm install --global npm@12.2.0`. From `~/Documents/omi`:

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

## Official app audio forwarding

The optional receiver preserves native capture and cloud transcript import. Save **Audio forwarding token** in either client's server settings under **Omi connections**, then enable forwarding in **Settings → Omi connection**. The environment variable `OMI_AUDIO_FORWARDING_TOKEN` remains available as an initial default. Configure the official app’s **Settings → Developer Mode → Realtime audio bytes** URL and interval. Accepted raw PCM becomes playable 16 kHz WAV and enters the durable local worker. Receiver status, last receipt and sanitized failures are shown in Settings; source details disclose receipt-estimated times. Last receipt is not proof of complete capture.

Current upstream sends webhooks from **Omi’s backend**, whose SSRF checks reject private destinations. Phone LAN/VPN access alone is insufficient; do not expose this unauthenticated archive publicly. See the [complete walkthrough](official-audio-forwarding.md) for the network prerequisite, token/proxy protection, contract evidence, retry/duplicate/timing policy, away-from-home limitations and pending consumer-hardware acceptance. No public network setup is performed by the feature.

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

Settings → Omi connection can import existing cloud conversations using Omi's [personal Developer API](https://docs.omi.me/api-reference/conversations/get-conversations). Create an `omi_dev_…` key with `conversations:read` in Omi's Developer settings, then save it as **Omi API key** under **Models, keys & server settings → Omi connections** in app or web. `OMI_OMI_API_KEY` remains an environment default. The saved key stays on the backend, outside public settings and application archive exports. Leave it empty to use direct BLE capture without an Omi account.

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
unambiguous assignment; enable `OMI_STT_SEGMENT_TIMESTAMPS` with a compatible STT
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

Use **web Settings → Models, keys & server settings** or **iPhone Settings → Transcription → Models, keys & all server settings** to configure every backend setting. Search or browse the groups for transcription models and language routes, LLM task routes, endpoints, reasoning options, gateway credentials, Omi API/forwarding keys, embeddings, capture hints, retention and processing. Route editors support inherited defaults, route-specific keys and no authentication. Saved keys are never returned to either client; select Keep, Replace or Clear explicitly. Concurrent edits are rejected with a reload message so one client cannot silently overwrite another.

STT/LLM configuration, keys and ordinary preferences apply to subsequent work immediately. Embedding configuration, worker enablement, web assets and archive paths are saved with a **restart required** marker; the response lists pending fields. Restart the backend process using your deployment's normal procedure. Enabling embeddings still requires a model, immutable revision and dimension in one valid save. Changing `data_dir` selects that archive on restart; it does not migrate existing recordings.

The backend stores service overrides and keys in `.private-settings.json` in the **bootstrap** archive directory (the initial `OMI_DATA_DIR` location). Writes are atomic with file mode `0600`; `.env` is preserved. This private file is excluded from application archive export/restore, and keys never enter the SQLite runtime-settings table. Keep a separate private copy when moving the server; general filesystem backups of the directory will include it. Existing archive preferences continue to use SQLite. Saved overrides take precedence over environment defaults. The settings API returns write-only key status and uses `Cache-Control: no-store`; the configuration screen keeps entered replacements only in memory until save/reload.

The example uses the inspected HomeCompute repository configuration:

| Setting | Example |
|---|---|
| HTTP gateway base | `http://ai.home.arpa:18080/v1` |
| Danish transcription route | `stt-danish` |
| Multilingual alternative | `whisper-turbo` |
| LLM route | `home` |
| Language | `auto` omits a forced STT language |

Configure `OMI_STT_BASE_URL`, `OMI_LLM_BASE_URL`, `OMI_STT_MODEL`, and `OMI_LLM_MODEL` in `.env`. `OMI_INFERENCE_TOKEN` is optional authentication for your own inference gateway and is never an app login credential. Endpoints accept private IPs and local DNS/service names. There is no cloud fallback or redirect following.

**STT chunk seconds** in the **Transcription** settings group controls maximum audio duration per model request (1–120 seconds, default 30). Changes apply to subsequent processing without restarting. Set `OMI_STT_CHUNK_SECONDS` for an environment default. Choose a duration every configured speech route accepts; gateway model lists may expose only token limits, so audio duration is not discovered automatically. Longer recordings are split internally, their text joined in order and segment timestamps adjusted; the archived audio, source identity and capture times stay intact. All pieces share one inference deadline, and no partial transcript is published if a piece fails.

STT can use a different model/server for each language. Use the **Transcription** group in either client's server settings to edit the default model/endpoint and add language routes. Alternatively, configure initial environment routes and restart:

```dotenv
# Use aliases available on your own gateway.
OMI_STT_MODEL=whisper-turbo
OMI_STT_ROUTES__DA__MODEL=stt-danish
OMI_STT_ROUTES__EN__MODEL=whisper-turbo
# Some detectors return full English names rather than ISO codes:
OMI_STT_ROUTES__DANISH__MODEL=stt-danish
```

With a language selected in web/iPhone Settings, transcription uses its route directly in one request. Route keys are case-insensitive; regional codes match an exact route first, then the base language (`da-DK` → `da`). Unconfigured languages use the shared STT settings. Each route can override `MODEL`, `BASE_URL`, and `TOKEN` independently; omitted values inherit `OMI_STT_MODEL`, `OMI_STT_BASE_URL`, and `OMI_INFERENCE_TOKEN`. An explicitly empty `TOKEN` removes authentication. Routes use the same local HTTP transcription contract; credentials remain stored on the backend and are write-only in the editors.

With **Automatic / mixed languages** and language routes configured, the initial STT receives `response_format=verbose_json` without a forced language. Configure a multilingual model that supports that format and returns a `language` string. When that string matches a route code or configured provider-name key (for example `da` or `danish`), a different route transcribes the same original chunk once more. Only the final result and its timestamps are saved. Missing, unknown or mixed-language metadata keeps the initial result; the same resolved model/endpoint/token is never called twice. Detection and specialist requests share the configured inference deadline. Specialist failures use the existing durable retry path, without publishing preliminary detector text.

An optional `OMI_STT_ROUTES__AUTO__MODEL` (also `BASE_URL`/`TOKEN`) overrides the initial automatic model. With no language routes, automatic transcription retains its existing single request. Automatic routing is per chunk, based on provider metadata; it does not split code-switched speech between models. Use a multilingual route for mixed recordings. Language names are matched literally, so add the names returned by your provider alongside ISO-code routes when needed. No models are downloaded, and language detection/quality still requires validation against your local service.

Every LLM task inherits that shared model, endpoint, token and revision by default. Optional backend-only overrides let each task use a different local model or server:

```dotenv
OMI_LLM_MODEL=home
# Optional examples; use aliases actually configured on your local gateway.
OMI_LLM_ROUTES__CHAT__MODEL=fast-chat
OMI_LLM_ROUTES__SUMMARY__MODEL=long-context
```

Supported tasks are `CHAT` (final answers), `SUMMARY` (diary, meeting, rewind and large-chat evidence reduction), `EXTRACTION` (inbox and decisions), `PREPARATION` (conversation briefs), `WEEKLY` (weekly extraction and synthesis), and `RETRIEVAL` (alternate query wording after a miss). Each also accepts `BASE_URL`, `TOKEN`, and `REVISION`, for example `OMI_LLM_ROUTES__SUMMARY__BASE_URL=http://summarizer.local:4000/v1`. Omitted fields inherit the corresponding shared setting; an explicitly empty `TOKEN` removes authentication for that task. Use `REVISION` when replacing weights behind the same model alias. Routes require the same private/local endpoints and HTTP chat-completions contract; they do not download models or enable hosted inference. Restart the backend after editing environment configuration. STT and embeddings retain their separate settings.

Generated output records the selected model. Diary and weekly fingerprints include the task's model, endpoint and revision, and extraction jobs use the extraction route identity. Changing only the chat route leaves diary/weekly caches valid. Task credentials are excluded from public settings, generated output, cache identities and backups.

Chat uses one answer-model call when bounded topic retrieval succeeds. Broad recall samples chronological source IDs in SQLite before loading transcript bodies; with a limit of one it chooses the most recent eligible recording. Database sampling runs outside the event loop. Follow-ups load only the six recent messages used for model context; opening a saved chat still returns its complete history. Short referential follow-ups carry the prior topic into retrieval, and inferred date scopes survive multiple follow-ups. Large selected transcripts are reduced in bounded summary-model batches, then answered directly by the chat model without an extra final-summary call. Full selected evidence and source validation are preserved.

If a topical chat search returns no sources but searchable transcripts exist in its scope, `OMI_CHAT_QUERY_EXPANSION=true` permits one query-rewriting call using the `RETRIEVAL` route (shared model by default). It requests at most 20 English/Danish search terms and retries retrieval within the original date/meeting scope. The default total expansion deadline is five seconds (`OMI_CHAT_QUERY_EXPANSION_TIMEOUT_SECONDS`). Failed/invalid rewrites retain the no-match result and disclose unavailability. Disable it for a strict single-answer-call path. `/v1/search` remains retrieval-only. Rewriting mechanics are tested with controlled responses; real multilingual gains still require the configured model.

Web and iPhone chat use `POST /v1/chat/stream`, a newline-delimited JSON stream of `status`, `delta`, `heartbeat`, then terminal `done` or `error` events. `done.result` has the same saved answer/source identifiers as `POST /v1/chat`, which remains compatible for other clients. Model streaming uses bounded OpenAI-compatible SSE; compatible gateways returning ordinary JSON still work. Draft text is provisional: unknown citations, source corrections, truncated/incomplete streams and model errors never publish/save a partial answer, and clients discard the draft on failure. Stop/navigation cancels generation and closes the provider stream; only final answers enter the native cache. A cancellation racing an already committed answer can still leave that completed exchange in history. Native streaming uses [URLSession.AsyncBytes](https://developer.apple.com/documentation/foundation/urlsession/bytes%28for%3Adelegate%3A%29) in an ephemeral session.

Streams send `Cache-Control: no-store` and `X-Accel-Buffering: no`; a private reverse proxy must also allow incremental response delivery. `timings` records retrieval (including expansion), expansion, generation, first token when streamed, and pipeline total milliseconds before persistence. Real answer latency depends on local model loading, context size and gateway contention; the fictional retrieval evaluation below measures search alone.

Adapters use HTTP `/audio/transcriptions` and `/chat/completions`; the existing home-core Wyoming service on port 10300 needs an HTTP bridge before it can be used here. The configured gateway and previous `192.168.30.122:18080` address were unreachable from this Mac during validation, so real STT/LLM quality and latency have not been verified. When models are unreachable, audio remains queued and summary/chat errors remain visible.

Optional `OMI_STT_SEGMENT_TIMESTAMPS=true` requests verbose segment timestamps from compatible providers. Recall-first processing skips only exact digital silence by default; faint audio and noise remain eligible. See [speech processing](#speech-processing-and-cleanup-review) for optional cleanup and limitations. Retrieval combines keywords with local date scoping, including today/yesterday/ISO dates and English/Danish weekdays. Broad day queries sample bounded evidence; semantic retrieval is opt-in and remembered voice suggestions require separate enrollment/evaluation. Answers supply source references and reject unknown cited UUIDs, but model accuracy still needs checking against source audio.

### Speech processing and cleanup review

Transcription preserves original archive files, hashes, capture times and playback. By default,
`OMI_STT_PRESERVE_QUIET=true` skips only recordings made entirely of digital zero samples.
This avoids rejecting faint voices using a volume threshold. Noise-only recordings may reach
STT too; model output still needs checking. To restore the old RMS gate explicitly, disable
preserve quiet and set `OMI_SILENCE_THRESHOLD` (35 by default; 0 disables that legacy gate).

Quiet transcription copies receive uniform amplification toward -25 dBFS RMS, with peak
headroom and at most `OMI_STT_MAX_GAIN_DB=12` dB. Set 0 to disable. This changes amplitude,
not signal-to-noise ratio, and cannot uncover a quieter voice masked by a louder one. For
internal STT request splits, `OMI_STT_BOUNDARY_SEARCH_SECONDS=0.5` searches for a 100 ms quiet
gap near the boundary; 0 restores fixed cuts. Every sample is retained exactly once. Phone
chunk boundaries and existing captured gaps are unchanged; there is no cross-source context
or word deduplication.

`OMI_STT_ENHANCEMENT_MODE=on_empty` creates a cleanup **review reading** only when a chunk's
original pass is empty. Choose `compare` to also compare noisy nonempty transcriptions, or
`off` to disable. FFmpeg must be installed on home-core and on its process `PATH`; the Docker
image includes it. On a standalone Mac, an existing Homebrew installation can supply it with
`brew install ffmpeg`. The fixed local profile applies mild FFT noise reduction plus bounded
leveling, compensates its delay, and retains the exact sample count. It uses raw in-memory
pipes, no temporary audio files, no downloaded models and no hosted fallback. Missing FFmpeg,
invalid output or provider failures leave the original transcript usable and disclose cleanup
unavailability. Each filter process has a five-second limit; the entire optional pass shares
`OMI_STT_ENHANCEMENT_TIMEOUT_SECONDS=15` seconds and the remaining inference budget. All
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

No speech detector discards nonzero audio, no denoiser removes a presumed background speaker
from the canonical pass, and no speaker names are invented. Optional diarization is available
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

Semantic retrieval is implemented behind **`OMI_EMBEDDING_ENABLED=false` by default**. It uses the same SQLite archive; no vector server or model download is required. No reachable embedding model, bilingual quality, target-machine latency or hardware quality has been verified.

Choose and serve a local model yourself, then configure the separate `OMI_EMBEDDING_*` fields in [the environment example](../config/omi.env.example). Enabling requires a model name, immutable revision identifier and output dimension. The adapter expects an OpenAI-compatible `POST /embeddings` response with indexed float vectors. Configure query/document prefixes according to that model's documented contract. Tokens use a separate `OMI_EMBEDDING_TOKEN`, never the STT/LLM credential. Endpoints use the existing local-address validation, TLS verification, no redirects and no cloud fallback. Requests have a total deadline, input/batch ceilings and a response-byte ceiling; malformed, zero, nonfinite or wrong-dimension vectors mark the adapter unavailable.

`OMI_EMBEDDING_PASSAGE_CHARS` defaults to 1000 and `OMI_EMBEDDING_MAX_INPUT_CHARS` to 4000. These are **character bounds, not token estimates**. Confirm the model accepts all configured text plus prefixes without provider-side truncation; this implementation cannot detect a provider that silently truncates or changes weights behind an unchanged alias. Queries exceeding the character ceiling fall back to keywords. Text is never silently shortened for embedding. Change `OMI_EMBEDDING_REVISION` whenever weights change. Endpoint, dimension, prefixes and splitter changes also produce separate embedding sets.

Keyword search and query embedding run concurrently. Interactive embeddings have a separate two-second total deadline (`OMI_EMBEDDING_QUERY_TIMEOUT_SECONDS`); background indexing retains its ten-second deadline. Successful query vectors use a bounded, RAM-only LRU (128 entries for 300 seconds; `OMI_EMBEDDING_QUERY_CACHE_SIZE`/`OMI_EMBEDDING_QUERY_CACHE_SECONDS`, zero disables). Keys include the complete embedding manifest, and identical simultaneous queries share one provider call. Cancelling the last consumer cancels that call. Query failures trigger a 15-second cooldown (`OMI_EMBEDDING_QUERY_RETRY_SECONDS`) before another uncached provider attempt. No retrieval results or answers are cached: transcript edits, deletion and scope checks still run on every search. Semantic candidates load current source rows in one database read.

On startup, a separate bounded worker backfills current nonempty transcripts, including existing Omi imports. It processes at most 16 passages per call and resumes after restart; STT does not wait for embeddings. New recordings and corrections are searchable with keywords immediately. SQLite triggers invalidate obsolete versions on transcription, correction, deletion and transcript expiry, including imports that write directly to SQLite. Final writes and retrieval recheck source versions. Audio-only expiry keeps transcript vectors. Backups include index tables and manifests; restored jobs resume when the same set is configured. Old inactive sets are retained as rebuildable data; edits/deletion remove that source across all sets. Model changes immediately select the new set and temporarily use partial semantic coverage plus keywords during backfill; vectors from different sets are never compared.

`POST /v1/search` accepts the same question, limit, day/range/meeting/session scope as chat and returns sources plus retrieval diagnostics **without generating an answer or creating a conversation**. Saved explicit scopes win over conflicting question dates. Both candidate branches apply the effective scope before their top 50 results. Keyword BM25 and cosine candidates are deduplicated by recording UUID, combined with reciprocal rank fusion (`k=60`), and protect up to two strongest lexical matches for literal names. An exact streamed vector scan avoids a new dependency or arbitrary recent-history cutoff. Scan cost still grows with archive size; target-machine performance at 10k/100k passages is not yet measured.

Topical chat uses the same search. Broad questions retain the sampling/diary path and reduce **full selected transcripts**, including long transcript tails, through the existing bounded-batch map/reduce path; generic follow-ups reload current source recordings. Topical evidence is bounded to the requested recording limit and 12,000 excerpt characters, with up to 2,000 characters of derived diary navigation notes. Each recording can supply up to three non-overlapping excerpts ranked by query coverage, so a name at the start does not hide a relevant deadline near the end. Ranked recordings receive their first excerpt before additional passages consume spare budget. Broad-query metadata instead declares full selected transcripts and no total excerpt-character ceiling; source sampling remains explicit. Passage overlap and up to 150 characters around a hit preserve context inside a recording. Topical matches may add at most one immediately contiguous prior/next completed local recording on the same device, excluding imports, capture gaps and records outside the effective scope. Neighbors keep their own source UUID/version and fill spare source slots only, without displacing ranked hits or exceeding the shared excerpt budget; context is therefore limit-dependent. FTS highlight positions keep lexical excerpts aligned with actual Unicode token/diacritic matches. Diary notes whose dependencies were omitted are excluded. Full `Source.text` remains authoritative for corrections and verification; additive `matches` contain excerpts and offsets. Clients display retrieval mode, indexing coverage and bounded-evidence notices. Source timing still covers whole overlapping recording chunks, including long imports; character offsets do not claim precise speech timestamps.

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

Daily recaps refresh hourly when completed evidence changes, with an evening update at `OMI_RECAP_HOUR` (default 22:00). `OMI_RECAP_INTERVAL_MINUTES=0` disables daytime synthesis; completed conversation notes are still prepared in advance. Live conversations stay pending until five minutes of contiguous observed non-speech (`OMI_RECAP_QUIET_MINUTES`), followed by a 30-second settling buffer. Local WebRTC speech detection is advisory: it never filters transcription or deletes original audio. Empty transcripts, missing uploads, failed detection and capture gaps do not prove silence. A manually open meeting defers its stream; meeting hints cannot force closure. Boundaries remain approximate, and TV/radio speech can count as activity. Imported finished Omi conversations and manually uploaded files provide explicit file boundaries; an uploaded file may contain only part of a real conversation.

Raw Omi audio webhooks carry receipt-time estimates without capture ordering or stream continuity. Their quiet payloads cannot prove a conversation ended, so use finished Omi conversation imports for recaps; raw webhook transcripts/audio remain searchable and playable.

Each completed conversation is summarized once per source/model/language/policy version, preserving original citations. Fillers and unsupported fragments can produce a cached no-content result; short meaningful answers remain in context. Daily synthesis uses these independent notes rather than repeatedly rewriting the previous recap. The apps show the included cutoff and pending conversations. Late evidence can reopen a conversation and suppress its old current recap until safe regeneration, including across midnight. Transcript corrections, deletion and transcript expiry clear prepared summaries. Silence/noise recordings keep the same audio retention as speech (seven days by default).

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

The container builds the web client, serves it with the API using one worker, and persists a named volume. Port 8787 binds the LAN by default; set `OMI_BIND_ADDRESS=127.0.0.1` in `.env` when a local reverse proxy owns access. The explicit `--env-file .env` also applies that setting to Compose's port interpolation. Docker loopback refers to the container, so inference must use a reachable LAN/service address. No remote deployment to home-core was performed during implementation.

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

Upgrade validation: 207 backend tests and Ruff passed on Python 3.15.0; 28 web tests and the TypeScript/Vite production build passed on Node 26.12.0/npm 12.2.0. Clean npm installation reported zero vulnerabilities; npm's default blocking of the optional macOS `fsevents` install script did not affect tests or builds. The Python wheel and source archive both contain the web assets. Docker built on Linux ARM64 and passed API/OpenAPI, bundled asset, upload/playback and settings checks under UID 10001 with a read-only root filesystem, dropped capabilities and no-new-privileges. Audio bytes and settings survived container replacement on a disposable named volume. The default background worker also started with container networking disabled. Desktop (1440×1000) and mobile (390×844) browser checks passed Settings save/reload against the container without console errors or failed requests. Test containers and storage were removed; no deployment, live archive access or real inference calls were performed. iOS was unchanged and its checks were not rerun for this upgrade.

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
