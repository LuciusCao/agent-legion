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

  # The business skill shared-assets check (scripts/check-skills-shared.py)
  # retired with the business skill sources; the script itself leaves with the
  # business runtime code in P4.

  echo "=== MyPy Type Check ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run mypy server/app scripts/architecture scripts/quality workflow_nodes

  echo "=== Architecture Contracts ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.check_architecture

  echo "=== Architecture Docs Freshness ==="
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.generate_architecture --check

  # The spec health check (scripts/verify_specs.py) retired with the
  # unpublished docs/superpowers specs (f4e7e46f): the directory is
  # gitignored and absent, so the step had been passing vacuously.
}

run_tests() {
  # worker/ui is a no-build static console; its pure-function tests run on
  # the backend lane (worker belongs to backend semantics) via Node's
  # built-in runner. Skip with a notice when node is absent — or when the
  # test file is missing (gate-script tests run this script inside fixture
  # repos that only contain a copy of the script itself).
  # BACKEND_SKIP_WORKER_UI_TESTS=1 (set only by scripts/check.sh's postgres
  # segment) skips the suite there: the unit segment already ran the
  # identical invocation, and the tier selection below cannot affect these
  # tier-independent tests.
  if [[ "${BACKEND_SKIP_WORKER_UI_TESTS:-0}" == "1" ]]; then
    echo "=== Worker UI Tests (skipped: BACKEND_SKIP_WORKER_UI_TESTS=1) ==="
  elif ! command -v node >/dev/null 2>&1; then
    echo "node not found; skipping worker/ui tests (install Node.js to enable them)."
  elif [[ ! -f "$ROOT_DIR/worker/ui/app.test.mjs" ]]; then
    echo "worker/ui/app.test.mjs not present; skipping worker/ui tests."
  else
    echo "=== Worker UI Tests (node --test) ==="
    node --test "$ROOT_DIR/worker/ui/app.test.mjs"
  fi

  # GATE_TIER=smoke runs the curated fast subset (membership lives in
  # config/architecture/smoke-test-files.json, loaded by tests/conftest.py —
  # the same loader also reads postgres-test-files.json, which backs the
  # marker-based postgres classification) without coverage — the 85% coverage
  # floor only makes
  # sense for the full suite, which remains the CI boundary. GATE_TIER=unit
  # runs every non-PostgreSQL test against an intentionally unreachable
  # database URL; this is marker-based membership rather than a file
  # allowlist, and proves the pure layer remains independently runnable.
  #
  # AGENT_LEGION_TEST_WORKERS caps pytest-xdist parallelism. Default is
  # worktree-aware (scripts/gate-jobs.sh): the machine budget divides across
  # live gates ((cores-2)/N, clamped to [2,8]); the sibling-lock probe applies
  # when the queue is not visible — without oversubscribing machines that run
  # several worktrees or a frontend lane at the same time (raise it on a
  # dedicated box; CI 4-vCPU runners are unaffected). Computed at tier start
  # (per round from check-quick.sh's perspective), so a sibling gate taking
  # its slot between rounds is reflected by the next invocation.
  #
  # --reruns absorbs timing-sensitive flakes under parallel-gate load; a real
  # regression still fails after the single retry (visible as RERUN in output).
  #
  # --dist worksteal: the default `load` distribution hands each worker a
  # batch of ~60 tests up front, so one slow test (or a worker stuck behind
  # collection/import) strands its whole batch — the suite then waits on a
  # single busy worker while the others sit idle. worksteal lets an idle
  # worker steal pending tests, shrinking the tail latency that previously
  # stretched quick-gate wall time (and that tail is exactly where
  # timeout-sensitive tests flaked under CPU contention). Collection stays
  # deterministic; only assignment order changes.
  source "$ROOT_DIR/scripts/gate-jobs.sh"
  workers="${AGENT_LEGION_TEST_WORKERS:-$(detect_gate_default_jobs_worktree_aware)}"
  telemetry_args=()
  if [[ -n "${AGENT_LEGION_TEST_RESULTS_DIR:-}" ]]; then
    result_name="${AGENT_LEGION_TEST_RESULT_NAME:-backend}"
    mkdir -p "$AGENT_LEGION_TEST_RESULTS_DIR"
    telemetry_args=(
      --durations="${AGENT_LEGION_TEST_DURATIONS:-30}"
      --junitxml="$AGENT_LEGION_TEST_RESULTS_DIR/${result_name}-junit.xml"
      -p scripts.pytest_telemetry
    )
    export AGENT_LEGION_RERUN_REPORT="$AGENT_LEGION_TEST_RESULTS_DIR/${result_name}-reruns.json"
  fi
  cov_args=()
  if [[ "${AGENT_LEGION_COV:-0}" == "1" ]]; then
    cov_args=(--cov=server --cov-report=term-missing)
    if [[ "${AGENT_LEGION_COV_APPEND:-0}" == "1" ]]; then
      cov_args+=(--cov-append)
    fi
  fi
  split_cov_floor_args=()
  if [[ "${AGENT_LEGION_COV:-0}" == "1" ]]; then
    split_cov_floor_args=(--cov-fail-under=0)
  fi
  case "${GATE_TIER:-full}" in
    smoke)
      echo "=== Python Smoke Tests (curated, no coverage) ==="
      UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "smoke" \
        -n "$workers" --dist worksteal \
        --reruns 1 \
        --reruns-delay 2
      ;;
    unit)
      echo "=== Python Unit Tests (PostgreSQL offline) ==="
      AGENT_LEGION_TEST_DATABASE_URL="postgresql://127.0.0.1:1/agent_legion_unit_offline" \
        UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "not postgres and not repository_gate" \
        -n "$workers" --dist worksteal \
        --reruns 1 \
        --reruns-delay 2 \
        "${telemetry_args[@]}" \
        "${cov_args[@]}" \
        "${split_cov_floor_args[@]}"
      ;;
    aff)
      # Agent inner-loop tier: affected-test selection over the unit layer.
      # The selection needs a coverage-derived index (.pytest-aff-index.json,
      # built by GATE_TIER=aff-index or scripts/pytest_aff_selection.py);
      # without one — or when the selection would not save time — it falls
      # back to the plain unit tier. Never a gate pass: full suite stays the
      # pre-push/CI boundary.
      aff_args=()
      if command -v git >/dev/null 2>&1 && [[ -f "$ROOT_DIR/.pytest-aff-index.json" ]]; then
        base_ref="$(git merge-base HEAD develop 2>/dev/null || git merge-base HEAD origin/develop 2>/dev/null || true)"
        selected=""
        selection_status=0
        selected="$(UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.pytest_aff_selection select \
          ${base_ref:+--base "$base_ref"} 2>/dev/null)" || selection_status=$?
        # Exit 4 = a changed source file is missing from the index (stale
        # index or a --cov blind spot): the affected tests are unknown, so
        # run the full unit tier rather than a silently incomplete subset.
        if [[ "$selection_status" -eq 4 ]]; then
          echo "=== Python Unit Tests (aff fallback: changed source files missing from the index) ==="
        elif [[ -n "$selected" ]] && [[ "$(printf '%s\n' "$selected" | wc -l)" -lt 400 ]]; then
          echo "=== Python Affected Tests (selected $(printf '%s' "$selected" | wc -l | tr -d ' ') of unit tier) ==="
          while IFS= read -r nodeid; do
            aff_args+=("$nodeid")
          done <<<"$selected"
        else
          echo "=== Python Unit Tests (aff fallback: no index or selection too broad) ==="
        fi
      else
        echo "=== Python Unit Tests (aff fallback: no .pytest-aff-index.json; prime it with GATE_TIER=aff-index) ==="
      fi
      if [[ ${#aff_args[@]} -gt 0 ]]; then
        AGENT_LEGION_TEST_DATABASE_URL="postgresql://127.0.0.1:1/agent_legion_unit_offline" \
          UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
          --ignore=tests/full \
          --ignore=tests/ci \
          -m "not postgres and not repository_gate" \
          -n "$workers" --dist worksteal \
          --reruns 1 \
          --reruns-delay 2 \
          "${aff_args[@]}"
      else
        AGENT_LEGION_TEST_DATABASE_URL="postgresql://127.0.0.1:1/agent_legion_unit_offline" \
          UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
          --ignore=tests/full \
          --ignore=tests/ci \
          -m "not postgres and not repository_gate" \
          -n "$workers" --dist worksteal \
          --reruns 1 \
          --reruns-delay 2 \
          "${telemetry_args[@]}" \
          "${cov_args[@]}" \
          "${split_cov_floor_args[@]}"
      fi
      ;;
    aff-index)
      # One-off index primer: full unit-tier run with per-test coverage
      # contexts, then distilled into .pytest-aff-index.json. Keep coverage
      # off elsewhere; this tier exists to pay the 15-40% tracing cost once.
      echo "=== Python Unit Tests + Coverage Contexts (aff index build) ==="
      aff_index_cov_file="$ROOT_DIR/.pytest-aff-coverage"
      rm -f "$aff_index_cov_file" "$aff_index_cov_file".*.*.*
      # COVERAGE_FILE must stay exported across the pytest run: pytest-cov
      # merges its xdist worker shards into that path itself, so a separate
      # `coverage combine` step is unnecessary (and would fail with "No data
      # to combine" once the shards are already merged).
      export COVERAGE_FILE="$aff_index_cov_file"
      AGENT_LEGION_TEST_DATABASE_URL="postgresql://127.0.0.1:1/agent_legion_unit_offline" \
        UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "not postgres and not repository_gate" \
        -n "$workers" --dist worksteal \
        --reruns 1 \
        --reruns-delay 2 \
        --cov=server --cov=worker --cov=shared --cov=workflow_nodes --cov=scripts --cov=workspace_libs \
        --cov-context=test \
        --cov-fail-under=0 \
        --cov-report= \
        "$@"
      UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.pytest_aff_selection build "$aff_index_cov_file"
      rm -f "$aff_index_cov_file" "$aff_index_cov_file".*.*.*
      unset COVERAGE_FILE
      ;;
    postgres)
      echo "=== Python PostgreSQL Tests ==="
      # GATE_SHARD=i/n (CI only) hash-shards the tier via a collection filter;
      # unset locally, where the tier keeps running as one unsplit suite.
      shard_args=()
      if [[ -n "${GATE_SHARD:-}" ]]; then
        echo "=== GATE_SHARD=${GATE_SHARD} (deterministic hash shard) ==="
        shard_args=(-p scripts.pytest_gate_shard)
      fi
      UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "postgres and not repository_gate" \
        -n "$workers" --dist worksteal \
        --reruns 1 \
        --reruns-delay 2 \
        "${shard_args[@]}" \
        "${telemetry_args[@]}" \
        "${cov_args[@]}" \
        "${split_cov_floor_args[@]}"
      ;;
    full)
      # The local full tier is the unit layer — same selection as GATE_TIER=unit.
      # The PostgreSQL layer (1711 of 3644 quick-suite tests, ~2.5x the unit
      # tier's cost) moved out of the local default: CI re-runs all of it on
      # every PR (backend-postgres-a/b/c), so paying it on every local gate
      # bought little. Run GATE_TIER=postgres explicitly before handing off
      # database-touching work, or rely on CI; scripts/check.sh (the local
      # full-gate substitute) pins unit + postgres itself and stays whole.
      # Coverage tracing costs 15-40% CPU on the Python side; the 85% floor is
      # enforced by CI and scripts/check.sh, so the local quick gate skips it
      # unless AGENT_LEGION_COV=1.
      if [[ "${AGENT_LEGION_COV:-0}" == "1" ]]; then
        echo "=== Python Tests + Coverage (unit tier) ==="
      else
        echo "=== Python Tests (unit tier, coverage off; set AGENT_LEGION_COV=1 to enable) ==="
      fi
      AGENT_LEGION_TEST_DATABASE_URL="postgresql://127.0.0.1:1/agent_legion_unit_offline" \
        UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q \
        --ignore=tests/full \
        --ignore=tests/ci \
        -m "not postgres and not repository_gate" \
        -n "$workers" --dist worksteal \
        --reruns 1 \
        --reruns-delay 2 \
        "${telemetry_args[@]}" \
        "${cov_args[@]}" \
        "${split_cov_floor_args[@]}"
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
