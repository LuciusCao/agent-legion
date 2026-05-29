#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

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
npm run test
