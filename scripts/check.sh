#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Quick Gate ==="
"$ROOT_DIR/scripts/check-quick.sh"

echo "=== Full Gate Architecture Evidence ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q tests/full -m full_gate

echo "=== Frontend Production Build ==="
cd "$ROOT_DIR/frontend"
npm run build
