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
  # GATE_TIER=smoke runs the curated fast subset (membership lives in
  # tests/conftest.py) without coverage — the 85% coverage floor only makes
  # sense for the full suite, which remains the CI boundary.
  #
  # AGENT_LEGION_TEST_WORKERS caps pytest-xdist parallelism (default: auto =
  # all cores). Machines running several worktrees at once should set it
  # (e.g. 3-4) to avoid oversubscribing CPU and the shared Postgres.
  #
  # --reruns absorbs timing-sensitive flakes under parallel-gate load; a real
  # regression still fails after the single retry (visible as RERUN in output).
  workers="${AGENT_LEGION_TEST_WORKERS:-auto}"
  case "${GATE_TIER:-full}" in
    smoke)
      echo "=== Python Smoke Tests (no coverage) ==="
      UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "smoke and not repository_gate" \
        -n "$workers" \
        --reruns 1 \
        --reruns-delay 2
      ;;
    full)
      # Coverage tracing costs 15-40% CPU on the Python side; the 85% floor is
      # enforced by CI and scripts/check.sh, so the local quick gate skips it
      # unless AGENT_LEGION_COV=1.
      cov_args=()
      if [[ "${AGENT_LEGION_COV:-0}" == "1" ]]; then
        echo "=== Python Tests + Coverage ==="
        cov_args=(--cov=server --cov-report=term-missing)
      else
        echo "=== Python Tests (coverage off; set AGENT_LEGION_COV=1 to enable) ==="
      fi
      UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "not repository_gate" \
        -n "$workers" \
        --reruns 1 \
        --reruns-delay 2 \
        "${cov_args[@]}"
      ;;
    *)
      echo "Unsupported GATE_TIER: ${GATE_TIER}" >&2
      exit 2
      ;;
  esac
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
