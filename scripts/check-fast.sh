#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "=== Ruff Lint ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff check server tests workflow_nodes

echo "=== Ruff Format ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff format --check server tests workflow_nodes

echo "=== MyPy Type Check ==="
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run mypy server/app workflow_nodes

echo "=== Frontend Lint ==="
cd "$ROOT_DIR/frontend"
npm run lint

echo "=== Frontend Type Check ==="
npm run typecheck
