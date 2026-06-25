#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COVERAGE_FILE="${COVERAGE_FILE:-$ROOT_DIR/.coverage.check.$$}"
export COVERAGE_FILE
KEEP_COVERAGE=1
export KEEP_COVERAGE

cleanup_coverage() {
  rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*
}
trap cleanup_coverage EXIT

echo "=== Quick Gate ==="
"$ROOT_DIR/scripts/check-quick.sh"

echo "=== Full Gate Architecture Evidence ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q tests/full -m full_gate --cov=server --cov-report= --cov-append

echo "=== Combined Coverage Report ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run coverage report

echo "=== Frontend Production Build ==="
cd "$ROOT_DIR/frontend"
npm run build
