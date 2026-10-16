#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
exec uv run uvicorn omi_local.api:create_app --factory \
  --workers 1 \
  --host "${OMI_HOST:-0.0.0.0}" --port "${OMI_PORT:-8787}"
