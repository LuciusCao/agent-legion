#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Full Gate ==="
"$ROOT_DIR/scripts/check.sh"

echo "=== CI Extended Architecture Evidence ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q tests/ci -m ci_extended --reruns 1 --reruns-delay 2
