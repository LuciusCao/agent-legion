#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Space-separated lane selector: "backend frontend rust" (default, full quick
# gate), any subset (e.g. "backend" / "rust"), or "static" (all lanes, static
# phase only — used by pre-push for docs-only changes). The full gate in CI
# always runs every lane; this only trims the local feedback loop.
#
# When GATE_LANES is unset, lanes are derived from the worktree's uncommitted
# changes using the same path rules as .githooks/pre-push: a pure backend
# edit skips the rust lane, a pure velites/ edit runs rust only, docs-only
# runs the static phase, and shared files or anything ambiguous (not a git
# repo, git failure, clean tree) falls back to all lanes. Derivation runs
# before the lock/coverage artifacts below so they cannot pollute the
# working-tree classification (they are gitignored in this repo, but the
# gate-script tests run the script inside fixture repos without ignores).
derive_lanes_from_worktree() {
  local saw_frontend=0 saw_backend=0 saw_rust=0 saw_non_docs=0 saw_any=0
  local status line path
  if ! status="$(git status --porcelain=v1 --untracked-files=all 2>/dev/null)"; then
    echo "backend frontend rust"
    return
  fi
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    path="${line:3}"
    path="${path##* -> }"
    saw_any=1
    case "$path" in
      # Shared/global files: all lanes, no trimming.
      pyproject.toml|uv.lock|Makefile|scripts/*|.githooks/*|.github/*|config/*|frontend/package.json|frontend/package-lock.json)
        echo "backend frontend rust"
        return
        ;;
      velites/*)
        saw_rust=1
        saw_non_docs=1
        ;;
      frontend/*)
        saw_frontend=1
        saw_non_docs=1
        ;;
      docs/*|*.md|LICENSE)
        ;;
      *)
        saw_backend=1
        saw_non_docs=1
        ;;
    esac
  done <<<"$status"

  if [[ "$saw_any" -eq 0 ]]; then
    echo "backend frontend rust"
  elif [[ "$saw_non_docs" -eq 0 ]]; then
    echo "static"
  else
    local lanes=""
    [[ "$saw_backend" -eq 1 ]] && lanes="backend"
    [[ "$saw_frontend" -eq 1 ]] && lanes="${lanes:+$lanes }frontend"
    [[ "$saw_rust" -eq 1 ]] && lanes="${lanes:+$lanes }rust"
    echo "$lanes"
  fi
}

if [[ -z "${GATE_LANES:-}" ]]; then
  GATE_LANES="$(derive_lanes_from_worktree)"
  echo "Derived lanes from worktree changes: $GATE_LANES"
fi

# Serialize same-worktree gates: concurrent invocations (multiple agent
# sessions, or a manual run next to an agent loop) share the per-worktree
# test database, and their xdist workers use the same gw0..gwN schemas —
# TRUNCATE isolation then wipes each other's tables mid-run, surfacing as
# "flaky" failures at random tests/lanes. Cross-worktree runs are already
# isolated by per-worktree databases and do not take this lock.
lock_dir="$ROOT_DIR/.quick-gate.lock"
waited=0
while ! mkdir "$lock_dir" 2>/dev/null; do
  holder="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  if [[ -n "$holder" ]] && ! kill -0 "$holder" 2>/dev/null; then
    # Stale lock from a crashed/killed gate: reclaim it.
    rm -rf "$lock_dir"
    continue
  fi
  if [[ "$waited" -eq 0 ]]; then
    echo "Another quick gate holds this worktree (pid ${holder:-unknown}); waiting for it to finish..."
  fi
  sleep 5
  waited=$((waited + 5))
done
echo $$ >"$lock_dir/pid"
cleanup_lock() {
  rm -rf "$lock_dir"
}

# Machine-wide gate queue (scripts/gate-queue.sh): with several agent
# worktrees on one host, unlimited parallel gates thrash the CPU (observed
# ~1h for the last of 4 concurrent quick gates). A slot in the shared git
# common directory caps concurrent gates at AGENT_LEGION_MAX_PARALLEL_GATES
# (default 2); later gates queue with holder announcements. Acquired after
# the worktree lock so same-worktree serialization stays first.
source "$ROOT_DIR/scripts/gate-queue.sh"
acquire_gate_slot
cleanup_gate_slot() {
  release_gate_slot
}

COVERAGE_FILE="${COVERAGE_FILE:-$ROOT_DIR/.coverage.check-quick.$$}"
export COVERAGE_FILE
if [[ -z "${KEEP_COVERAGE:-}" ]]; then
  cleanup_coverage() {
    # xdist workers write "$COVERAGE_FILE".<host>.<pid>.<random>; require the
    # two extra dots so an unrelated same-prefix file (e.g. .log) survives.
    rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*.*.*
  }
  trap cleanup_coverage EXIT
fi

log_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-legion-quick.XXXXXX")"
cleanup_logs() {
  rm -rf "$log_dir"
}
if [[ -z "${KEEP_COVERAGE:-}" ]]; then
  trap 'cleanup_lock; cleanup_gate_slot; cleanup_logs; cleanup_coverage' EXIT
else
  trap 'cleanup_lock; cleanup_gate_slot; cleanup_logs' EXIT
fi

echo "=== Parallel Quick Gate ==="
echo "Parallel static/test rounds; the API contract check runs once between them."
lanes_started_at=$SECONDS

# Shared per-lane job cap: worktree-aware (scripts/gate-jobs.sh) — the
# machine budget divides across live gates ((cores-2)/N, clamped to [2,8]);
# the sibling-lock probe applies when the queue is not visible (per-lane envs
# override; CI 4-vCPU runners are unaffected). Recomputed per round, not
# snapshotted once: a second gate may take its slot between rounds, and each
# round should run against the concurrency that round actually sees.
source "$ROOT_DIR/scripts/gate-jobs.sh"

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
  # Cargo defaults to one rustc job per core; keep the gate polite on machines
  # running several worktrees (AGENT_LEGION_RUST_WORKERS overrides; CI 4-vCPU
  # runners are unaffected).
  rust_jobs="${AGENT_LEGION_RUST_WORKERS:-$(detect_gate_default_jobs_worktree_aware)}"
  if [[ "$round" == "static-check" ]]; then
    cargo fmt --all -- --check
    cargo clippy --all-targets --locked -j "$rust_jobs" -- -D warnings
  else
    cargo test --locked -j "$rust_jobs"
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
    frontend_api_check="${FRONTEND_API_CHECK:-1}"
    if [[ "$round" == "static-check" && -z "${FRONTEND_API_CHECK:-}" ]] && lane_enabled backend; then
      # api:check boots the backend app; the integration step below runs it once.
      frontend_api_check=0
    fi
    FRONTEND_GATE_PHASE="$frontend_phase" \
      FRONTEND_API_CHECK="$frontend_api_check" \
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
  # Heartbeat: lane output goes to log files and is only cat'ed at round end,
  # so a long round looks silent. Poll once a second so a finished lane exits
  # the loop within ~1s; print elapsed time plus each running lane's latest
  # log line only every GATE_HEARTBEAT_SECONDS (default 30).
  heartbeat_seconds="${GATE_HEARTBEAT_SECONDS:-30}"
  last_heartbeat=$SECONDS
  while true; do
    running=()
    [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" 2>/dev/null && running+=("backend")
    [[ -n "$frontend_pid" ]] && kill -0 "$frontend_pid" 2>/dev/null && running+=("frontend")
    [[ -n "$rust_pid" ]] && kill -0 "$rust_pid" 2>/dev/null && running+=("rust")
    [[ ${#running[@]} -eq 0 ]] && break
    sleep 1
    if (( SECONDS - last_heartbeat >= heartbeat_seconds )); then
      last_heartbeat=$SECONDS
      for lane in "${running[@]}"; do
        last_line="$(tail -n 1 "$log_dir/${lane}-${round}.log" 2>/dev/null | cut -c1-120)"
        echo "[gate:${round}] $((SECONDS - lanes_started_at))s ${lane}: ${last_line}"
      done
    fi
  done
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
# Integration step: the OpenAPI contract spans backend (schema export boots the
# full app) and frontend (type generation), so it runs once here instead of
# competing for CPU/memory inside the parallel static round.
if lane_enabled backend && lane_enabled frontend; then
  FRONTEND_GATE_PHASE="api-contract" \
    "$ROOT_DIR/scripts/check-quick-frontend.sh"
fi
if [[ "$GATE_LANES" != "static" ]]; then
  # GATE_TIER=aff is the agent inner-loop combination: backend affected-test
  # selection over the unit tier (PostgreSQL offline, index-backed; falls
  # back to the plain unit tier without an index) + frontend affected-test
  # selection (`vitest related`). A pass is NOT a full-gate pass — the full
  # suite stays the pre-push/CI boundary; aff only trims the edit-test
  # iteration cost. Prime the backend index with GATE_TIER=aff-index.
  if [[ "${GATE_TIER:-full}" == "aff" ]]; then
    echo "Backend test tier: aff (inner loop)"
    GATE_TIER=aff FRONTEND_TEST_MODE=related run_round "test" "test" "test"
  else
    echo "Backend test tier: ${GATE_TIER:-full}"
    FRONTEND_TEST_MODE="${FRONTEND_TEST_MODE:-test}" run_round "test" "test" "test"
  fi
fi

echo "Parallel quick gate passed in $((SECONDS - lanes_started_at))s."
