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

# Space-separated lane selector: "backend frontend" (default, full quick gate),
# "backend" / "frontend" (single lane), or "static" (both lanes, static phase
# only — used by pre-push for docs-only changes). The full gate in CI always
# runs every lane; this only trims the local feedback loop.
GATE_LANES="${GATE_LANES:-backend frontend}"

lane_enabled() {
  [[ "$GATE_LANES" == "static" || " $GATE_LANES " == *" $1 "* ]]
}

run_round() {
  round="$1"
  backend_phase="$2"
  frontend_phase="$3"
  backend_log="$log_dir/backend-${round}.log"
  frontend_log="$log_dir/frontend-${round}.log"

  echo "Starting ${round} round."
  backend_pid=""
  frontend_pid=""
  if lane_enabled backend; then
    BACKEND_GATE_PHASE="$backend_phase" \
      "$ROOT_DIR/scripts/check-quick-backend.sh" >"$backend_log" 2>&1 &
    backend_pid=$!
  else
    echo "Skipping backend ${round} lane (GATE_LANES=$GATE_LANES)."
  fi
  if lane_enabled frontend; then
    FRONTEND_GATE_PHASE="$frontend_phase" \
      FRONTEND_TEST_MODE="${FRONTEND_TEST_MODE:-test}" \
      "$ROOT_DIR/scripts/check-quick-frontend.sh" >"$frontend_log" 2>&1 &
    frontend_pid=$!
  else
    echo "Skipping frontend ${round} lane (GATE_LANES=$GATE_LANES)."
  fi

  backend_status=0
  frontend_status=0
  set +e
  if [[ -n "$backend_pid" ]]; then
    wait "$backend_pid"
    backend_status=$?
  fi
  if [[ -n "$frontend_pid" ]]; then
    wait "$frontend_pid"
    frontend_status=$?
  fi
  set -e

  if [[ -n "$backend_pid" ]]; then
    echo "=== Backend ${round} Output ==="
    cat "$backend_log"
  fi
  if [[ -n "$frontend_pid" ]]; then
    echo "=== Frontend ${round} Output ==="
    cat "$frontend_log"
  fi

  if [[ "$backend_status" -ne 0 || "$frontend_status" -ne 0 ]]; then
    echo "Parallel ${round} round failed: backend=$backend_status frontend=$frontend_status" >&2
    return 1
  fi
}

run_round "static-check" "static" "static"
if [[ "$GATE_LANES" != "static" ]]; then
  run_round "test" "test" "test"
fi

echo "Parallel quick gate passed in $((SECONDS - lanes_started_at))s."
