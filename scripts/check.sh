#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COVERAGE_FILE="${COVERAGE_FILE:-$ROOT_DIR/.coverage.check.$$}"
export COVERAGE_FILE
KEEP_COVERAGE=1
export KEEP_COVERAGE

cleanup_coverage() {
  # xdist workers write "$COVERAGE_FILE".<host>.<pid>.<random>; require the
  # two extra dots so an unrelated same-prefix file (e.g. .log) survives.
  rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*.*.*
}
trap cleanup_coverage EXIT

echo "=== Quick Gate (segmented) ==="
# The full local gate keeps coverage semantics: check-quick.sh skips backend
# coverage by default, so re-enable it here for the combined report below.
# GATE_LANES is pinned explicitly: without it check-quick.sh derives lanes
# from the dirty worktree, but check.sh is the local full-gate substitute and
# must always run every lane.
#
# Segmentation (issue #92): when the coverage-instrumented backend suite races
# the frontend/rust lanes for CPU, pytest-cov/xdist intermittently loses one
# whole worker's coverage data (TOTAL drops ~15-30 points even though every
# test passes, then the 85% floor below cannot be met). The backend coverage
# run therefore gets the machine to itself first; the frontend and rust lanes
# run afterwards without backend coverage. Trade-off: +2-4 minutes wall clock
# (static rounds no longer overlap, frontend/rust tests no longer hide behind
# the backend suite).
#
# Splitting one invocation into two keeps every check exactly once: segment 1
# runs backend static + backend tests with coverage; the hoisted api-contract
# step only fires when both backend and frontend lanes are enabled in a single
# invocation, so segment 2's frontend static lane runs api:check inline
# (FRONTEND_API_CHECK defaults to 1 when the backend lane is absent).
# Standalone ./scripts/check-quick.sh usage is unaffected.
#
# The local full gate keeps BOTH backend tiers even though the quick gate's
# full tier shrank to the unit layer: segment 1a runs the unit tier, segment
# 1b appends the postgres tier onto the same COVERAGE_FILE (AGENT_LEGION_
# COV_APPEND=1), so the combined report below still sees the whole suite.
# Segment 1b re-enters check-quick.sh with GATE_SKIP_STATIC=1 (no static
# round, no api-contract step) and BACKEND_SKIP_WORKER_UI_TESTS=1: segment
# 1a already ran every static check and the tier-independent worker UI
# tests, so the postgres segment pays only its own pytest run and every
# check still runs exactly once. Lock, slot queue, and coverage append
# semantics are unchanged.
echo "--- Segment 1a: backend unit tier with coverage (exclusive machine) ---"
GATE_LANES="backend" GATE_TIER=unit AGENT_LEGION_COV=1 "$ROOT_DIR/scripts/check-quick.sh"
echo "--- Segment 1b: backend postgres tier with coverage (exclusive machine) ---"
GATE_LANES="backend" GATE_TIER=postgres GATE_SKIP_STATIC=1 BACKEND_SKIP_WORKER_UI_TESTS=1 \
  AGENT_LEGION_COV=1 AGENT_LEGION_COV_APPEND=1 "$ROOT_DIR/scripts/check-quick.sh"
echo "--- Segment 2: frontend + rust lanes (no backend coverage) ---"
GATE_LANES="frontend rust" FRONTEND_TEST_MODE=coverage "$ROOT_DIR/scripts/check-quick.sh"

log_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-legion-full.XXXXXX")"
cleanup_logs() {
  rm -rf "$log_dir"
}
trap 'cleanup_coverage; cleanup_logs' EXIT

full_log="$log_dir/backend-full.log"
build_log="$log_dir/frontend-build.log"

echo "=== Parallel Full Gate Extensions ==="
echo "Starting full backend evidence and frontend production bundle."
extensions_started_at=$SECONDS

(
  cd "$ROOT_DIR"
  UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run pytest -q tests/full \
    -m full_gate --reruns 1 --reruns-delay 2 \
    --cov=server --cov=worker --cov-report= --cov-append
) >"$full_log" 2>&1 &
full_pid=$!
(
  cd "$ROOT_DIR/frontend"
  npm run build:bundle
) >"$build_log" 2>&1 &
build_pid=$!

set +e
wait "$full_pid"
full_status=$?
wait "$build_pid"
build_status=$?
set -e

echo "Parallel full extensions finished in $((SECONDS - extensions_started_at))s."
echo "=== Full Backend Lane Output ==="
cat "$full_log"
echo "=== Frontend Build Lane Output ==="
cat "$build_log"

if [[ "$full_status" -ne 0 || "$build_status" -ne 0 ]]; then
  echo "Parallel full gate extension failed: backend=$full_status build=$build_status" >&2
  exit 1
fi

echo "=== Combined Coverage Report ==="
cd "$ROOT_DIR"
UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run coverage report

echo "=== Coverage Partition Report ==="
# Per-partition floors keep key modules from hiding behind the global average.
# Default stays report mode (the pre-existing partitions were never validated
# against a blocking gate); AGENT_LEGION_COV_PARTITIONS=enforce turns
# violations into a failure — CI's backend-coverage job runs the worker
# execution-plane floor (issue #275) in enforce mode, where the merged
# shard data is complete.
if ! UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python scripts/check_coverage_partitions.py \
  --backend "$COVERAGE_FILE" \
  --frontend "$ROOT_DIR/frontend/coverage/coverage-final.json"; then
  echo "WARNING: coverage partition check reported violations (non-blocking)." >&2
fi

echo "=== Exemption Age Check (non-blocking) ==="
if ! UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}" uv run python -m scripts.check_exemption_age; then
  echo "WARNING: exemption age check reported overdue exemptions (non-blocking)." >&2
fi

echo "=== Dependency Vulnerability Audit (non-blocking) ==="
if ! "$ROOT_DIR/scripts/check-deps-audit.sh"; then
  echo "WARNING: dependency audit reported issues or could not complete (non-blocking)." >&2
fi
