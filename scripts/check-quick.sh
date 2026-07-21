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

log_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-legion-quick.XXXXXX")"
cleanup_logs() {
  rm -rf "$log_dir"
}
if [[ -z "${KEEP_COVERAGE:-}" ]]; then
  trap 'cleanup_logs; cleanup_coverage' EXIT
else
  trap cleanup_logs EXIT
fi

echo "=== Parallel Quick Gate ==="
echo "Running parallel static-check and test rounds; lane output is buffered."
lanes_started_at=$SECONDS

run_round() {
  round="$1"
  backend_phase="$2"
  frontend_phase="$3"
  backend_log="$log_dir/backend-${round}.log"
  frontend_log="$log_dir/frontend-${round}.log"

  echo "Starting ${round} round."
  BACKEND_GATE_PHASE="$backend_phase" \
    "$ROOT_DIR/scripts/check-quick-backend.sh" >"$backend_log" 2>&1 &
  backend_pid=$!
  FRONTEND_GATE_PHASE="$frontend_phase" \
    FRONTEND_TEST_MODE="${FRONTEND_TEST_MODE:-test}" \
    "$ROOT_DIR/scripts/check-quick-frontend.sh" >"$frontend_log" 2>&1 &
  frontend_pid=$!

  set +e
  wait "$backend_pid"
  backend_status=$?
  wait "$frontend_pid"
  frontend_status=$?
  set -e

  echo "=== Backend ${round} Output ==="
  cat "$backend_log"
  echo "=== Frontend ${round} Output ==="
  cat "$frontend_log"

  if [[ "$backend_status" -ne 0 || "$frontend_status" -ne 0 ]]; then
    echo "Parallel ${round} round failed: backend=$backend_status frontend=$frontend_status" >&2
    return 1
  fi
}

run_round "static-check" "static" "static"
run_round "test" "test" "test"

echo "Parallel quick gate passed in $((SECONDS - lanes_started_at))s."
