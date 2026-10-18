# Daythread web app

The interface supports English, Danish, German, Spanish, French, Norwegian Bokmål, and Swedish. Choose **Settings → Daily preferences → Interface language** to follow the browser language or select one on this device. To contribute another language or add interface copy, follow the [localization guide](../../docs/localization.md); run `npm run i18n:check` with the web checks.

The React app talks to the backend on the same origin. It has no sign-in, remote fonts, analytics, or cloud dependencies. The backend serves the built app at `/`.

Daythread uses the user-selected artwork in `public/icons/daythread-master.png`, copied from the approved attachment without altering the design. PNG sizes 32/192/512, `favicon.ico` and `apple-touch-icon.png` cover browser tabs, desktop installation and phone home screens. The same artwork is packaged in the iPhone AppIcon and DaythreadMark asset catalogs and used by the home-core front page. `public/site.webmanifest` provides the installation name and icons. Icon URLs include an artwork version to refresh browser and dashboard caches when the design changes.

Use Node.js 26.12.0 (pinned in the root `.node-version`) or newer, with npm 12.2.0 or newer. The Docker build uses these exact versions. Upgrade npm with `npm install --global npm@12.2.0`, then run from this directory:

```sh
npm ci
npm test
npm run build
```

For frontend development, run the backend on port 8787, then `npm run dev`. Vite proxies `/v1` to `http://127.0.0.1:8787`. The repository's build/deployment scripts copy `dist/` into the backend package.

Open **Settings → Profiles** to select an active profile, see all profiles, edit their names, or create an empty archive. Profile selection appears only in settings and is remembered in this browser. On a first visit, or when a remembered profile no longer exists, choose a profile explicitly before opening recordings, diaries, chat, or other archive pages. Direct links and Back/Forward navigation redirect to **Settings → Profiles** until a profile is selected; all settings pages remain available. Choose **Choose a profile…** again to clear the selection. Existing recordings belong to **Default**. Profiles separate recordings, hints, transcripts, recaps, chats, people/projects, inbox items, and decision records. Switching clears the previous profile’s screen and cancels its pending browser requests. All server settings and connections remain global, and appearance remains a device preference shared across profiles. Imports and receiver details need a selected archive. Profiles have no passwords or access restrictions: anyone with server access can select any profile.

Navigation uses URL paths, with the backend serving the app for direct links and refreshes. Selected dates use paths such as `/today/2026-10-16` and `/diary/2026-10-16`; `/today` and `/diary` follow the current day. Existing hash bookmarks are converted to paths when opened. All dates are rendered in the backend's configured timezone. Recording upload timestamps use the browser's timezone, shown explicitly in the dialog. Supported browser audio files are decoded and converted locally to 16 kHz mono PCM16 WAV, split into chunks of up to two minutes, then sent to the backend.

Use **Upload recording** to import audio exported from another phone, Voice Memos, a voice recorder, or a computer. Drag one audio file into the drop area, or use **Choose audio file**, then enter either the recording end or start time. The default is **Recording ended** at the current local date and time: this means when recording stopped. The app subtracts the decoded audio duration to calculate its start, including across midnight. Choose **Recording started** to enter when it began instead. Files may be up to 100 MB and two hours, subject to the browser's codec support and available memory. The archive records `source_kind=manual_upload`, the original filename, and the supplied capture time; playback downloads contain the converted WAV. Keep the dialog open while uploading. An interrupted request can be retried with the same chunk IDs while the dialog remains open, and each response is checked before advancing; closing or reloading discards that upload plan. File import works on LAN HTTP without microphone permission.

Settings → Connections lists recording brands from the server vendor catalog. Capabilities determine which brands offer audio uploads, transcript export imports, or optional cloud imports. Choose the device or brand for each file in Upload recordings; the server upload plan preserves that provenance when resuming an interrupted upload. Pocket cloud imports use a server-held API key. Plaud supports exported audio and original SRT/TXT transcript files; transcript imports require explicit original start and end times and do not include audio. Setup links open the vendor’s documentation.

The UI shows empty, queued, failure, and model-unavailable states from the actual API. Recaps, chats, original audio, and correction history always come from home-core. Rewind audio uses the exact same end timestamp as its summary request. Gesture hints do not crop recordings, and the UI observes the configured flag cooldown.

Today shows recording activity above the recap, including recordings waiting to upload,
server queue and processing counts, transcription stages and chunk progress, and queued or
active AI tasks (summaries, recording topics and translations). These counts cover the selected
profile across days. Live Omi or phone recording requires a fresh companion report with recent
audio evidence. A connection or an imported recording alone does not establish live capture.
Stale observations show last known counts and withhold live stages. The iPhone Today screen
also uses its current local capture state and upload queue while offline.

Today shows identical manually uploaded audio once, even when it was uploaded again with a
different filename or capture time. The entry uses the earliest supplied capture time within
the selected day; expand **Other uploads of this recording** to inspect each upload's original
time, audio and transcript. Counts and pagination apply to these entries. Transcript search
shows matching readings. All original uploads remain in the archive. Exact audio matches also
share conversation matching and compatible transcription results, including legacy web uploads;
changing transcription settings or correcting a transcript prevents recognition reuse.

After transcription, the backend generates a descriptive recording title and topic sections using
the local summary model. Open a recording in Today to expand each titled topic and read its
original transcript passage. Existing recordings are analyzed automatically after upgrade. The
original recording stays whole; topic boundaries do not claim exact audio timestamps. Corrections
clear old titles and sections until the updated transcript has been analyzed.

Saved recordings, meetings, person/project labels, day recaps and individual recap points/sections have a **Mark important** toggle. Marks are stored on home-core, shared with the iPhone companion, and can be removed later. Recap marks belong to the displayed saved version; they do not move to different text after regeneration. During-recording gesture hints remain separate.

Settings includes a searchable **Models, keys & server settings** editor shared with iPhone. It starts with separate **Speech-to-text (STT)** and **Language model (LLM)** sections, each showing its own endpoint URL, write-only API key and model field. Enter local or hosted endpoints and choose a discovered model or Custom to enter a name. Processing options, STT language routes and LLM task routes appear below these connections. All backend settings remain available across the preference controls and server editor. Key actions distinguish Keep, Replace, Clear, and route inheritance. Settings requiring a backend restart display that state explicitly. Configuration is fetched without HTTP caching and replacement keys are never stored in browser storage.

Transcript and summary language controls keep **Original** available and limit translation
choices to **Settings → Translation languages**. Targets matching at least 90% of the current
text are hidden, based on local detection weighted by letter count. For mixed text below 90%,
the dominant language is suggested when enabled in Settings; an explicit target choice wins.
The share is an estimate of text coverage, not detector confidence or audio duration.
Uncertain text keeps translation available without presenting a dominant-language suggestion.
Opening a language tab only checks its saved translation; an untranslated tab shows a blurred
original with an explicit **Translate** action. Generation keeps that preview with a progress
status, then displays the saved translation. Failures show an error and **Translate again**
over the blurred preview. Original remains accessible throughout.

Use **Record audio → Start recording** on Today to capture this device’s microphone.
Stop to listen, **Upload now**, **Remove**, or **Upload later**. Stopped recordings
are saved in IndexedDB for the current account and profile and remain available after
reload. Uploads use the original capture start and the existing resumable upload
plans; failed transfers keep the local copy. A confirmed upload removes that copy.
You can also download a local recording before removing it.

Microphone capture requires HTTPS or localhost, browser microphone permission, and
MediaRecorder support. A plain LAN HTTP address can still upload saved audio files.
Keep the page open while capturing; recordings stop before two hours or at the local
size limit. Closing/crashing the tab during capture can lose the unfinished recording.
Browser storage limits or clearing site data can remove local recordings. If saving
fails, keep the page open to retry or download the captured audio.

Day recaps include uploaded files after transcription. Live conversations wait for five minutes
of continuously recorded quiet by default; change **Pause before summarizing live conversations**
in Daily preferences. Transcription continues during speech. Missing uploads are not quiet, and
resumed speech resets the pause. Eligible recordings share a short batching window: 15 seconds
without changes, or up to 60 seconds when other known file uploads remain unfinished. Ongoing
live chunks do not delay eligible uploads, and the batching limit cannot include an ongoing
conversation. Multipart files wait for every part to arrive and finish transcription. The
recap's status distinguishes live conversations awaiting a pause from uploads/transcriptions
still in progress. Model processing and the 15-second recap refresh interval add latency.
