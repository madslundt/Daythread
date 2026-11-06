# Scripts

Tools for importing recordings, running/building Daythread, archive maintenance, and local
model evaluation. Run repository commands below from the repository root.

## Standalone recording importer

Download only [import_recordings.py](import_recordings.py) to your laptop. It needs Python
3.10+, FFmpeg, and `lsof` (included with macOS), plus access to an existing Daythread server.
No Daythread checkout, backend installation, or pip packages are required on the laptop.
Download [.env.example](.env.example) alongside the script, copy it with
`cp .env.example .env`, and edit the saved defaults:

```dotenv
DAYTHREAD_SERVER_URL=https://daythread.home.arpa
DAYTHREAD_PROFILE=default
# Optional, only if your server/proxy requires a bearer token:
# DAYTHREAD_IMPORT_TOKEN=your-private-token
# Optional, for a generic audio folder instead of Parrot:
# DAYTHREAD_IMPORT_FOLDER="~/Recordings"
# Optional, to trust your server's private certificate authority:
# SSL_CERT_FILE="/path/to/root-ca.crt"
```

Then run `python3 import_recordings.py` to watch Parrot with those saved settings,
or `python3 import_recordings.py --once` for one import. No repeated exports are needed.
`.env` is ignored by Git. The importer uses the first `.env` it finds: the working folder,
then the folder beside the script. Use `--env-file /path/to/.env.import` to select a file
explicitly. Command arguments override exported environment variables, which override file
defaults. Quote values containing spaces or `#`. `export KEY=value` and comments are supported.
Shell commands and variable substitutions are never executed. `--parrot` overrides a saved
generic folder.

For an explicit certificate-verification bypass, add `DAYTHREAD_INSECURE=true` to `.env`
or run `python3 import_recordings.py --include-existing --insecure`. This disables both
certificate-chain and hostname checks for the importer; it does not bypass network access
rules. Verification stays enabled by default, and `--no-insecure` overrides a saved bypass.
When enabled, the bypass takes precedence over `SSL_CERT_FILE` and prints a startup warning.
Use a trusted CA with `SSL_CERT_FILE` when available.

You can still supply settings directly:

```bash
# Install dependencies if needed (macOS with Homebrew).
brew install python ffmpeg

# Keep watching Parrot for finished recordings; Ctrl+C stops it.
python3 import_recordings.py --server https://YOUR_SERVER --parrot --watch

# Import currently available Parrot recordings once, then exit.
python3 import_recordings.py --server https://YOUR_SERVER --parrot --once

# Import from another folder instead.
python3 import_recordings.py --server https://YOUR_SERVER --folder ~/Recordings --once
```

Watch mode processes new/changed files; add `--include-existing` to include older files.
Once mode includes existing files automatically. Add `--dry-run` to preview, `--profile UUID`
to select another archive, or `--recursive` for subfolders. Run with `--help` for all options.
Optional installation: `python3 import_recordings.py --install` copies the standalone command
to `~/.local/bin/daythread-import`.

The importer waits for stable, closed files, combines matching Parrot microphone/system
tracks, and preserves originals. A persistent local content index and server duplicate checks
skip acknowledged recordings, including after renaming files or restarting; interrupted
uploads resume missing parts. Keep the index under
`~/Library/Application Support/Daythread Folder Import/`. Only one importer runs per server/profile.

Parrot folder mode also imports **on-device-only recordings**, because audio files do not
contain that privacy flag. Generic folder mode uses file modification time as recording end;
use `--mtime-is-start` if it represents recording start. See the
[complete setup and behavior](../docs/README.md#parrot-meeting-webhooks-and-audio-options).

## Repository tools

| Scripts | Purpose / how to run |
| --- | --- |
| `dev.sh` | Start the backend: `bash scripts/dev.sh`. |
| `build.sh`, `package_web.py` | Build the web app, bundle its assets, and build the Python package: `bash scripts/build.sh`. Packaging is included in that command. |
| `build_android.sh` | Test, lint, and build the Android APK with an installed Android SDK: `bash scripts/build_android.sh`. |
| `init_config.py` | Create initial private configuration, preserving an existing `.env`: `uv run python scripts/init_config.py`. |
| `archive.py` | Back up or restore an archive: `uv run python scripts/archive.py --help`. |
| `migrate_model_settings.py` | Move legacy model configuration into private settings; stop the backend first. Run `uv run python scripts/migrate_model_settings.py --help`. |
| `check_locales.py`, `localization_*` | Check translations; the other files are shared helpers: `uv run python scripts/check_locales.py`. |
| `evaluate_*.py`, `compare_*.py`, `benchmark_retrieval_scan.py` | Evaluate audio, speakers, diarization, or retrieval. Each CLI documents its inputs with `--help`, e.g. `uv run python scripts/evaluate_retrieval.py --help`. |
| `run_transcription_benchmark.py` | Replay explicit local fixtures through STT with filtering on/off. Requires `--live`, an output directory and a model revision (or explicit unknown); supports explicit private settings, resumable results and isolated storage. Run `uv run python scripts/run_transcription_benchmark.py --help`, then score saved runs with `evaluate_audio.py --hypotheses`. |
| `check_transcription_repeatability.py` | Send fresh repetitions of exactly the same prepared WAV and multipart bytes. Requires `--live`; a verified pass also requires pinned model/runtime response headers. Any exact-text change, request failure, reported cache hit, or provenance mismatch fails the gate. |
| `alignment_server.py`, `diarization_server.py`, `nemotron_diarization_server.py` | Optional model services with separate runtimes and weights; follow the [operations guide](../docs/README.md), [alignment guide](../docs/alignment.md), or [Nemotron guide](../docs/nemotron-diarization.md). |
| `provision_nemotron_diarization.sh`, `build_nemotron_cpu_image.sh`, `nemotron_provenance.py`, `requirements-nemotron.txt`, `alignment-requirements.lock` | Model provisioning, image builds, provenance checks, and dependency pins used by those service guides. |
| `deploy_busy_retry_homecore.py` | Operator-specific staging/deployment to home-core: `uv run python scripts/deploy_busy_retry_homecore.py --help`. |
| `deploy_transcription_homecore.py` | Pinned transcription overlay for the reviewed home-core baseline. `plan` prints hashes; `stage --qualification /private/receipt.json` builds and checks an isolated image; `deploy --qualification /private/receipt.json` requires the same receipt, published source and idle work. The receipt must explicitly qualify request determinism, pass every required reliability check and retain unresolved accuracy limitations. `recover --release RELEASE` resumes an interrupted rollback from its durable receipt. Run `--help` for arguments. |

## Transcription repeatability and fresh benchmarks

Use an already prepared mono PCM16 WAV at 16 kHz, at most 120 seconds and 4 MB. The repeat
gate does not amplify or split it. Pin `x-homecompute-stt-model-sha256` plus either
`x-homecompute-stt-runtime-commit` or `x-homecompute-stt-image-id` in a JSON object using
values obtained from trusted model/runtime inspection. Additional supported identity headers
can be pinned too. The server must return the pinned values on every response; copying
unverified response values into the pins does not establish the deployed identity.

```bash
uv run python scripts/check_transcription_repeatability.py /private/prepared.wav \
  --endpoint https://your-stt.local/v1/audio/transcriptions --model whisper \
  --output-dir data/benchmarks/experiment/repeat-default --repeats 3 \
  --expected-provenance /private/model-provenance.json \
  --ca-file /private/gateway-root.crt --live
```

The output directory must be new. Requests reuse identical multipart bytes and send
`Cache-Control: no-cache, no-store`; responses reporting hits through `X-Litellm-Cache-Hit`,
`X-Cache`, or `Cache-Status` fail. The gate compares exact text and normalized word hashes
separately, retaining private raw successful responses and sanitized provenance. Missing
pins allow diagnostic measurements, but cannot produce a verified pass. Exit code 0 means
the gate passed; 1 means failure or unverified results. Passing establishes repeatability
for those requests, not transcription accuracy or determinism on every recording.

No decoder options or language are forced by default. To test a specific profile, supply
`--profile /private/profile.json`, for example `{"temperature": 0, "temperature_inc": 0}`.
Language may only be `auto` and translation may only be `false`. If authentication is
needed, use `--api-key-env OMI_STT_API_KEY` so the secret stays out of command arguments
and artifacts.

After the gate passes, run the same manifest into two fresh benchmark directories:

```bash
SSL_CERT_FILE=/private/gateway-root.crt uv run python scripts/run_transcription_benchmark.py \
  /private/manifest.json --output-dir data/benchmarks/experiment/pass-one \
  --settings-file /private/settings.json --revision model-runtime-v1 \
  --expected-provenance /private/model-provenance.json --fresh --live

SSL_CERT_FILE=/private/gateway-root.crt uv run python scripts/run_transcription_benchmark.py \
  /private/manifest.json --output-dir data/benchmarks/experiment/pass-two \
  --settings-file /private/settings.json --revision model-runtime-v1 \
  --expected-provenance /private/model-provenance.json --fresh --live
```

The settings file uses `{"overrides": {...}}`. Only transcription, alignment, and relevant
inference settings apply; storage remains isolated. `--fresh` rejects existing output
directories, bypasses saved results, and rejects reported provider cache hits. Per-case
records retain provenance for every received provider response. Intentional no-inference
results, such as digital silence, are labeled `inference_status: skipped` and
`provenance_status: not_applicable`; they do not claim a verified model response.

Use `--variant daythread-full-audio` or `--variant daythread-vad` to run one policy.
`--capture-requests` adds private per-request audio and multipart hashes, safe request
fields, successful response text and any reported language. Vocabulary/prompt values
are hashed; credentials and failed response bodies are excluded. Requests that fail
before a response remain recorded. This option does not change the response format
or force a language. An absent language field remains unknown.

Compare both passes' text and word hashes before interpreting quality-score changes.
Saved `hypotheses.json` files can be scored with `evaluate_audio.py --hypotheses`; vendor
references still measure disagreement, not human-reference accuracy.
