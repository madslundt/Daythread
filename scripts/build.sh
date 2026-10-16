#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
uv sync --frozen
npm --prefix src/web ci
npm --prefix src/web run build
uv run python scripts/package_web.py
uv build
