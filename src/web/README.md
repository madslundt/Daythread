# Omi local web app

The React app talks to the backend on the same origin. It has no sign-in, remote fonts, analytics, or cloud dependencies. The backend serves the built app at `/`.

Use Node.js 26.12.0 (pinned in the root `.node-version`) or newer, with npm 12.2.0 or newer. The Docker build uses these exact versions. Upgrade npm with `npm install --global npm@12.2.0`, then run from this directory:

```sh
npm ci
npm test
npm run build
```

For frontend development, run the backend on port 8787, then `npm run dev`. Vite proxies `/v1` to `http://127.0.0.1:8787`. The repository's build/deployment scripts copy `dist/` into the backend package.

Navigation uses URL hashes so direct page links work without a separate client routing server. All dates are rendered in the backend's configured timezone. Recording upload timestamps use the browser's timezone, shown explicitly in the dialog. Supported browser audio files are decoded and converted locally to 16 kHz mono PCM16 WAV, split into chunks of up to two minutes, then sent to the backend.

The UI shows empty, queued, failure, and model-unavailable states from the actual API. Recaps, chats, original audio, and correction history always come from home-core. Rewind audio uses the exact same end timestamp as its summary request. Gesture hints do not crop recordings, and the UI observes the configured flag cooldown.

Saved recordings, meetings, person/project labels, day recaps and individual recap points/sections have a **Mark important** toggle. Marks are stored on home-core, shared with the iPhone companion, and can be removed later. Recap marks belong to the displayed saved version; they do not move to different text after regeneration. During-recording gesture hints remain separate.

Settings includes a searchable **Models, keys & server settings** editor shared with iPhone. All backend settings are available across the preference controls and server editor, including STT language routes, LLM task routes, local endpoints and write-only keys. Key actions distinguish Keep, Replace, Clear, and route inheritance. Settings requiring a backend restart display that state explicitly. Configuration is fetched without HTTP caching and replacement keys are never stored in browser storage.
