#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff check .
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q

cd "$ROOT_DIR/frontend"
npm run test
