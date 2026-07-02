#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

COVERAGE_FILE="${COVERAGE_FILE:-$ROOT_DIR/.coverage.check-quick.$$}"
export COVERAGE_FILE
if [[ -z "${KEEP_COVERAGE:-}" ]]; then
  cleanup_coverage() {
    rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*
  }
  trap cleanup_coverage EXIT
fi

echo "=== Ruff Lint ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff check .

echo "=== Ruff Format ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff format --check .

echo "=== Architecture Invariant Registry ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/check_invariants.py

echo "=== Skill Shared Content Sync ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/check-skills-shared.py

echo "=== Python Tests + Coverage ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q --ignore=tests/full --ignore=tests/ci -n auto --cov=server --cov-report=term-missing

echo "=== MyPy Type Check ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run mypy server/app

echo "=== Architecture Contracts ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/check_architecture.py

echo "=== Generated API Contract ==="
cd "$ROOT_DIR/frontend"
npm run api:check

echo "=== Frontend Tests ==="
cd "$ROOT_DIR/frontend"
npm run format:check
npm run lint
npm run typecheck
npm run test

echo "=== Spec Health Check ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/verify_specs.py --check
