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

# Space-separated lane selector: "backend frontend rust" (default, full quick
# gate), any subset (e.g. "backend" / "rust"), or "static" (all lanes, static
# phase only — used by pre-push for docs-only changes). The full gate in CI
# always runs every lane; this only trims the local feedback loop.
GATE_LANES="${GATE_LANES:-backend frontend rust}"

lane_enabled() {
  [[ "$GATE_LANES" == "static" || " $GATE_LANES " == *" $1 "* ]]
}

run_rust_round() {
  round="$1"
  if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo not found; skipping rust ${round} lane (install Rust stable to enable it)."
    return 0
  fi
  if [[ ! -d "$ROOT_DIR/velites" ]]; then
    # Gate-script tests copy this script into fixture repos without the crate.
    echo "velites/ not present; skipping rust ${round} lane."
    return 0
  fi
  cd "$ROOT_DIR/velites"
  if [[ "$round" == "static-check" ]]; then
    cargo fmt --all -- --check
    cargo clippy --all-targets --locked -- -D warnings
  else
    cargo test --locked
  fi
}

run_round() {
  round="$1"
  backend_phase="$2"
  frontend_phase="$3"
  backend_log="$log_dir/backend-${round}.log"
  frontend_log="$log_dir/frontend-${round}.log"
  rust_log="$log_dir/rust-${round}.log"

  echo "Starting ${round} round."
  backend_pid=""
  frontend_pid=""
  rust_pid=""
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
  if lane_enabled rust; then
    run_rust_round "$round" >"$rust_log" 2>&1 &
    rust_pid=$!
  else
    echo "Skipping rust ${round} lane (GATE_LANES=$GATE_LANES)."
  fi

  backend_status=0
  frontend_status=0
  rust_status=0
  set +e
  if [[ -n "$backend_pid" ]]; then
    wait "$backend_pid"
    backend_status=$?
  fi
  if [[ -n "$frontend_pid" ]]; then
    wait "$frontend_pid"
    frontend_status=$?
  fi
  if [[ -n "$rust_pid" ]]; then
    wait "$rust_pid"
    rust_status=$?
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
  if [[ -n "$rust_pid" ]]; then
    echo "=== Rust ${round} Output ==="
    cat "$rust_log"
  fi

  if [[ "$backend_status" -ne 0 || "$frontend_status" -ne 0 || "$rust_status" -ne 0 ]]; then
    echo "Parallel ${round} round failed: backend=$backend_status frontend=$frontend_status rust=$rust_status" >&2
    return 1
  fi
}

run_round "static-check" "static" "static"
if [[ "$GATE_LANES" != "static" ]]; then
  echo "Backend test tier: ${GATE_TIER:-full}"
  run_round "test" "test" "test"
fi

echo "Parallel quick gate passed in $((SECONDS - lanes_started_at))s."
