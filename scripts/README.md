# Scripts

Tools for importing recordings, running/building Daythread, archive maintenance, and local
model evaluation. Run repository commands below from the repository root.

## Standalone recording importer

Download only [import_recordings.py](import_recordings.py) to your laptop. It needs Python
3.10+, FFmpeg, and `lsof` (included with macOS), plus access to an existing Daythread server.
No Daythread checkout, backend installation, or pip packages are required on the laptop.
From the folder containing the downloaded script:

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
| `alignment_server.py`, `diarization_server.py`, `nemotron_diarization_server.py` | Optional model services with separate runtimes and weights; follow the [operations guide](../docs/README.md), [alignment guide](../docs/alignment.md), or [Nemotron guide](../docs/nemotron-diarization.md). |
| `provision_nemotron_diarization.sh`, `build_nemotron_cpu_image.sh`, `nemotron_provenance.py`, `requirements-nemotron.txt`, `alignment-requirements.lock` | Model provisioning, image builds, provenance checks, and dependency pins used by those service guides. |
| `deploy_busy_retry_homecore.py` | Operator-specific staging/deployment to home-core: `uv run python scripts/deploy_busy_retry_homecore.py --help`. |
