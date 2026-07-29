#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

run_static_checks() {
  echo "=== Ruff Lint ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff check .

  echo "=== Ruff Format ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run ruff format --check .

  echo "=== Architecture Invariant Registry ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.check_invariants

  # Skill repos live at machine-local paths (config/skills.yaml), so this check
  # is meaningless on CI runners; set AGENT_LEGION_SKIP_SKILLS_SHARED_CHECK=1 there.
  if [[ "${AGENT_LEGION_SKIP_SKILLS_SHARED_CHECK:-0}" != "1" ]]; then
    echo "=== Skill Shared Content Sync ==="
    UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/check-skills-shared.py
  fi

  echo "=== MyPy Type Check ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run mypy server/app

  echo "=== Architecture Contracts ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.check_architecture

  echo "=== Architecture Docs Freshness ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.generate_architecture --check

  echo "=== Spec Health Check ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/verify_specs.py --check
}

run_tests() {
  echo "=== Python Tests + Coverage ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
    --ignore=tests/full \
    --ignore=tests/ci \
    -m "not repository_gate" \
    -n auto \
    --cov=server \
    --cov-report=term-missing
}

case "${BACKEND_GATE_PHASE:-all}" in
  static) run_static_checks ;;
  test) run_tests ;;
  all)
    run_static_checks
    run_tests
    ;;
  *)
    echo "Unsupported BACKEND_GATE_PHASE: ${BACKEND_GATE_PHASE}" >&2
    exit 2
    ;;
esac
