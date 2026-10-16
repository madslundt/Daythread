# Omi local web app

The React app talks to the backend on the same origin. It has no sign-in, remote fonts, analytics, or cloud dependencies. The backend serves the built app at `/`.

Use Node.js 22.12 or later (24 or 26 recommended):

```sh
npm ci
npm test
npm run build
```

For frontend development, run the backend on port 8787, then `npm run dev`. Vite proxies `/v1` to `http://127.0.0.1:8787`. The repository's build/deployment scripts copy `dist/` into the backend package.

Navigation uses URL hashes so direct page links work without a separate client routing server. All dates are rendered in the backend's configured timezone. Recording upload timestamps use the browser's timezone, shown explicitly in the dialog. Supported browser audio files are decoded and converted locally to 16 kHz mono PCM16 WAV, split into chunks of up to two minutes, then sent to the backend.

The UI shows empty, queued, failure, and model-unavailable states from the actual API. Recaps, chats, original audio, and correction history always come from home-core. Rewind audio uses the exact same end timestamp as its summary request. Gesture hints do not crop recordings, and the UI observes the configured flag cooldown.
