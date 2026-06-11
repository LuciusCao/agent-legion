#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

COVERAGE_FILE="${COVERAGE_FILE:-$ROOT_DIR/.coverage.check-quick.$$}"
export COVERAGE_FILE
cleanup_coverage() {
  rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*
}
trap cleanup_coverage EXIT

echo "=== Ruff Lint ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff check .

echo "=== Ruff Format ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff format --check .

echo "=== Python Tests + Coverage ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q --cov=server --cov-report=term-missing

echo "=== MyPy Type Check ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run mypy server/app

echo "=== Frontend Tests ==="
cd "$ROOT_DIR/frontend"
npm run format:check
npm run lint
npm run typecheck
npm run test:coverage

echo "=== Spec Health Check ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/verify_specs.py --check
