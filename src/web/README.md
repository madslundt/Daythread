# Omi local web app

The React app talks to the backend on the same origin. It has no sign-in, remote fonts, analytics, or cloud dependencies. The backend serves the built app at `/`.

Use Node.js 26.12.0 (pinned in the root `.node-version`) or newer, with npm 12.2.0 or newer. The Docker build uses these exact versions. Upgrade npm with `npm install --global npm@12.2.0`, then run from this directory:

```sh
npm ci
npm test
npm run build
```

For frontend development, run the backend on port 8787, then `npm run dev`. Vite proxies `/v1` to `http://127.0.0.1:8787`. The repository's build/deployment scripts copy `dist/` into the backend package.

Use the **Profile** selector above each page to switch archives, or **Create profile** to start an empty one. Selection is remembered in this browser; existing recordings belong to **Default**. Profiles separate recordings, hints, transcripts, recaps, chats, people/projects, inbox items, and decision records. Switching clears the previous profile’s screen and cancels its pending browser requests. All server settings and connections remain global, and appearance remains a device preference shared across profiles. Profiles have no passwords or access restrictions: anyone with server access can select any profile.

Navigation uses URL hashes so direct page links work without a separate client routing server. All dates are rendered in the backend's configured timezone. Recording upload timestamps use the browser's timezone, shown explicitly in the dialog. Supported browser audio files are decoded and converted locally to 16 kHz mono PCM16 WAV, split into chunks of up to two minutes, then sent to the backend.

Use **Upload recording** to import audio exported from another phone, Voice Memos, a voice recorder, or a computer. Drag one audio file into the drop area, or use **Choose audio file**, then enter either the recording end or start time. The default is **Recording ended** at the current local date and time: this means when recording stopped. The app subtracts the decoded audio duration to calculate its start, including across midnight. Choose **Recording started** to enter when it began instead. Files may be up to 100 MB and two hours, subject to the browser's codec support and available memory. The archive records `source_kind=manual_upload`, the original filename, and the supplied capture time; playback downloads contain the converted WAV. Keep the dialog open while uploading. An interrupted request can be retried with the same chunk IDs while the dialog remains open, and each response is checked before advancing; closing or reloading discards that upload plan. File import works on LAN HTTP without microphone permission.

The UI shows empty, queued, failure, and model-unavailable states from the actual API. Recaps, chats, original audio, and correction history always come from home-core. Rewind audio uses the exact same end timestamp as its summary request. Gesture hints do not crop recordings, and the UI observes the configured flag cooldown.

Saved recordings, meetings, person/project labels, day recaps and individual recap points/sections have a **Mark important** toggle. Marks are stored on home-core, shared with the iPhone companion, and can be removed later. Recap marks belong to the displayed saved version; they do not move to different text after regeneration. During-recording gesture hints remain separate.

Settings includes a searchable **Models, keys & server settings** editor shared with iPhone. All backend settings are available across the preference controls and server editor, including STT language routes, LLM task routes, local endpoints and write-only keys. Key actions distinguish Keep, Replace, Clear, and route inheritance. Settings requiring a backend restart display that state explicitly. Configuration is fetched without HTTP caching and replacement keys are never stored in browser storage.
